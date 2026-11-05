"""API key storage in the operating system's credential store (plan v1.2 section 14).

Windows: Credential Manager, macOS: Keychain, Linux: Secret Service — through the
``keyring`` package. Keys never go into project files, the SQLite index or logs.
``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) / ``ANTHROPIC_API_KEY`` in the environment is used
when set, which also covers machines without a usable credential store.

Claude can also use the login of Anthropic's ``ant`` CLI (``ant auth login``). Its tokens live in
Anthropic's own config folder and are read by the ``anthropic`` SDK; this app only checks that the
active profile exists and never opens the credentials file.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SERVICE = "ai-circuit-studio"
ENV_VARS = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "claude": ("ANTHROPIC_API_KEY",),
            # Datasheet search (backend.datasheet_search): distributor keys, stored the same way.
            "mouser": ("MOUSER_API_KEY",), "digikey-id": ("DIGIKEY_CLIENT_ID",),
            "digikey-secret": ("DIGIKEY_CLIENT_SECRET",)}
_PROFILE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _anthropic_config_dir() -> Path:
    """Same rule as the ``anthropic`` SDK and the ``ant`` CLI."""
    env = os.environ.get("ANTHROPIC_CONFIG_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "Anthropic"
    return Path.home() / ".config" / "anthropic"


def claude_login_status() -> dict:
    """Whether an ``ant auth login`` profile (or an ``ANTHROPIC_AUTH_TOKEN``) is there for the SDK to use."""
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return {"logged_in": True, "source": "env_token", "profile": None}
    base = _anthropic_config_dir()
    profile = os.environ.get("ANTHROPIC_PROFILE") or ""
    if not profile:
        try:
            profile = (base / "active_config").read_text(encoding="utf-8").strip()
        except OSError:
            profile = ""
    profile = profile or "default"
    if not _PROFILE.fullmatch(profile):
        return {"logged_in": False, "source": None, "profile": None}
    present = (base / "configs" / f"{profile}.json").is_file() and (base / "credentials" / f"{profile}.json").is_file()
    return {"logged_in": present, "source": "ant_login" if present else None, "profile": profile}


class KeyStoreError(Exception):
    pass


class KeyStore:
    def __init__(self, service: str = SERVICE):
        self.service = service

    def _keyring(self):
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        return keyring

    @property
    def available(self) -> bool:
        try:
            return self._keyring() is not None
        except Exception:  # noqa: BLE001 - a broken backend counts as unavailable
            return False

    def get(self, provider: str) -> tuple[str | None, str | None]:
        """(key, source) where source is 'env' or 'keyring'."""
        for var in ENV_VARS.get(provider, ()):
            if os.environ.get(var):
                return os.environ[var], "env"
        try:
            kr = self._keyring()
            if kr is not None:
                value = kr.get_password(self.service, f"{provider}-api-key")
                if value:
                    return value, "keyring"
        except Exception:  # noqa: BLE001
            pass
        return None, None

    def set(self, provider: str, api_key: str) -> None:
        kr = self._keyring()
        if kr is None:
            raise KeyStoreError("이 컴퓨터에서 OS 보안 저장소를 쓸 수 없습니다.")
        kr.set_password(self.service, f"{provider}-api-key", api_key)

    def delete(self, provider: str) -> bool:
        kr = self._keyring()
        if kr is None:
            return False
        try:
            kr.delete_password(self.service, f"{provider}-api-key")
            return True
        except Exception:  # noqa: BLE001 - nothing stored
            return False
