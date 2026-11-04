"""Sign in to Claude with Anthropic's ``ant`` CLI instead of pasting an API key.

``ant auth login`` opens the browser, the user signs in to their Claude Console (API) account, and the CLI
stores a short-lived token with a refresh token under Anthropic's config folder. The ``anthropic`` SDK picks
that profile up by itself, so the app never handles the token. Usage is billed to the Console account's API
credits; a claude.ai subscription (Pro/Max) cannot be used by other apps.

The CLI is run with an argument list (no shell), in a background thread, with a time limit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable

from ai_engine import claude_login_status

LOGIN_TIMEOUT_S = 300
INSTALL_HINT = ("Anthropic CLI(`ant`)가 필요합니다. github.com/anthropics/anthropic-cli/releases 에서 받아 PATH에 두거나, "
                "AICS_ANT_CLI 환경 변수에 실행 파일 경로를 지정하세요. 설치하지 않고 쓰려면 Claude API 키를 저장하세요.")


class ClaudeLogin:
    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which):
        self._run = run
        self._which = which
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    def cli(self) -> str | None:
        return os.environ.get("AICS_ANT_CLI") or self._which("ant")

    def status(self) -> dict:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            error = self._last_error
        return {**claude_login_status(), "cli_found": self.cli() is not None, "running": running,
                "last_error": error, "install_hint": INSTALL_HINT}

    def start(self) -> dict:
        cli = self.cli()
        if cli is None:
            return {**self.status(), "started": False}
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {**self._unlocked_status(), "started": False}
            self._last_error = None
            self._thread = threading.Thread(target=self._login, args=(cli,), name="claude-login", daemon=True)
            self._thread.start()
        return {**self.status(), "started": True}

    def _unlocked_status(self) -> dict:
        return {**claude_login_status(), "cli_found": True, "running": True, "last_error": None,
                "install_hint": INSTALL_HINT}

    def _login(self, cli: str) -> None:
        error = None
        try:
            done = self._run([cli, "auth", "login"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=LOGIN_TIMEOUT_S, check=False)
            if done.returncode != 0:
                tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
                error = f"`ant auth login`이 실패했습니다 (종료 코드 {done.returncode}). " + " ".join(tail)[:400]
        except subprocess.TimeoutExpired:
            error = f"{LOGIN_TIMEOUT_S // 60}분 안에 브라우저 로그인이 끝나지 않았습니다. 다시 시도하세요."
        except OSError as exc:
            error = f"Anthropic CLI를 실행하지 못했습니다: {exc}"
        if error is None and not claude_login_status()["logged_in"]:
            error = "로그인이 끝났지만 저장된 프로필을 찾지 못했습니다. 터미널에서 `ant auth status`로 확인하세요."
        with self._lock:
            self._last_error = error

    def logout(self) -> dict:
        cli = self.cli()
        if cli is not None:
            try:
                self._run([cli, "auth", "logout"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                with self._lock:
                    self._last_error = f"`ant auth logout`을 실행하지 못했습니다: {exc}"
        return self.status()
