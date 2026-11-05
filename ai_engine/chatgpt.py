"""ChatGPT with the user's own account: Sign in with ChatGPT and ChatGPT plan usage (no API key).

OpenAI's flow for open-source and locally installed apps (developers.openai.com/siwc/token-sharing-open-source):

1. A stable, opaque host id (``urn:uuid:...``) is created once per installation.
2. The system browser opens ``auth.openai.com/api/accounts/authorize``: the first time with
   ``client_id=dynamic_agent_client`` (the user names and authorizes this app, and the callback returns the issued
   ``client_id``), later with that issued id. PKCE (S256), a fresh ``state`` and OIDC ``nonce`` for each attempt, and
   a loopback callback on ``http://127.0.0.1:<port>/auth/callback``.
3. The code is exchanged at ``auth.openai.com/api/accounts/oauth/token`` (no client secret). The ID token is checked
   against OpenAI's published keys (issuer, audience = issued client id, expiry, nonce), and the granted scopes must
   include ``chatgpt.tokens.use.direct`` before any inference.
4. Inference is ``POST api.openai.com/v1/responses`` with the access token, ``store: false`` and ``stream: true``.
   Access tokens last an hour; the rotating refresh token renews them. Signing out revokes the refresh token.

Usage counts against the user's ChatGPT plan (they set per-app limits in ChatGPT settings -> Usage). Credentials are
kept in a local file only the user can read, never in projects or logs.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable, Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .providers import DEFAULT_CHATGPT_MODEL, AiError, Attachment, LlmResult, ModelInfo

AUTH_BASE = "https://auth.openai.com"
API_BASE = "https://api.openai.com/v1"
AGENT_NAME = "AI Circuit Studio"
DYNAMIC_CLIENT = "dynamic_agent_client"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"
CALLBACK_PATH = "/auth/callback"
DEFAULT_PORT = 1455
LOGIN_TIMEOUT_S = 600
REFRESH_MARGIN_S = 600
USAGE_HINT = "ChatGPT 설정 → 사용량(Usage)에서 이 앱의 사용 한도를 확인·조정할 수 있습니다."


def default_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "AI Circuit Studio"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AI Circuit Studio"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ai-circuit-studio"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_id_token(token: str, jwks: dict, *, issuer: str, audience: str, nonce: str, now: float | None = None) -> dict:
    """Check an RS256 ID token against a JWKS; returns its claims or raises AiError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64url_decode(head_b64))
        claims = json.loads(_b64url_decode(body_b64))
        signature = _b64url_decode(sig_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AiError("AI_AUTH_FAILED", "ChatGPT 로그인 응답의 ID 토큰을 읽지 못했습니다.") from exc
    if header.get("alg") != "RS256":
        raise AiError("AI_AUTH_FAILED", f"지원하지 않는 ID 토큰 서명 방식입니다: {header.get('alg')}")
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None)
    if key is None:
        raise AiError("AI_AUTH_FAILED", "ID 토큰의 서명 키를 OpenAI 공개 키 목록에서 찾지 못했습니다.")
    public = rsa.RSAPublicNumbers(int.from_bytes(_b64url_decode(key["e"]), "big"),
                                  int.from_bytes(_b64url_decode(key["n"]), "big")).public_key()
    try:
        public.verify(signature, f"{head_b64}.{body_b64}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise AiError("AI_AUTH_FAILED", "ID 토큰 서명이 맞지 않습니다.") from exc
    now = time.time() if now is None else now
    aud = claims.get("aud")
    if claims.get("iss") != issuer:
        raise AiError("AI_AUTH_FAILED", f"ID 토큰 발급자가 다릅니다: {claims.get('iss')}")
    if audience not in (aud if isinstance(aud, list) else [aud]):
        raise AiError("AI_AUTH_FAILED", "ID 토큰이 이 앱(발급된 client_id)을 위한 것이 아닙니다.")
    if float(claims.get("exp", 0)) < now - 60:
        raise AiError("AI_AUTH_FAILED", "ID 토큰이 만료되었습니다. 다시 로그인하세요.")
    if claims.get("nonce") != nonce:
        raise AiError("AI_AUTH_FAILED", "ID 토큰의 nonce가 이 로그인 시도와 다릅니다.")
    return claims


class ChatGptLogin:
    """The ChatGPT account of this installation: host id, issued client id, tokens (one active account)."""

    def __init__(self, config_dir: Path | None = None, *, auth_base: str = AUTH_BASE, api_base: str = API_BASE,
                 open_browser: Callable[[str], object] = webbrowser.open, port: int = DEFAULT_PORT,
                 http: httpx.Client | None = None):
        self.path = (config_dir or default_config_dir()) / "chatgpt.json"
        self.auth_base = auth_base.rstrip("/")
        self.api_base = api_base.rstrip("/")
        self.open_browser = open_browser
        self.port = port
        self.http = http or httpx.Client(timeout=30.0)
        self._lock = threading.RLock()
        self._pending: dict | None = None
        self._server: ThreadingHTTPServer | None = None
        self._discovery: dict | None = None

    # --- storage (owner-only file) -------------------------------------------------------------

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def host_id(self) -> str:
        with self._lock:
            data = self._load()
            if not data.get("ext_agent_host_id"):
                data["ext_agent_host_id"] = f"urn:uuid:{uuid.uuid4()}"
                self._save(data)
            return data["ext_agent_host_id"]

    def discovery(self) -> dict:
        if self._discovery is None:
            r = self.http.get(f"{self.auth_base}/.well-known/openid-configuration")
            r.raise_for_status()
            self._discovery = r.json()
        return self._discovery

    # --- status --------------------------------------------------------------------------------

    def status(self) -> dict:
        data = self._load()
        cred = data.get("credentials") or {}
        pending = self._pending
        return {
            "logged_in": bool(cred.get("refresh_token") or cred.get("access_token")),
            "email": cred.get("email"),
            "plan_usage": PLAN_SCOPE in (cred.get("scopes") or []),
            "registered": bool(data.get("client_id")),
            "pending": bool(pending and not pending.get("done")),
            "authorize_url": pending.get("url") if pending and not pending.get("done") else None,
            "error": (pending or {}).get("error"),
            "usage_hint": USAGE_HINT,
        }

    # --- sign in -------------------------------------------------------------------------------

    def start(self) -> dict:
        """Open the browser for sign-in; the loopback listener finishes the login in the background."""
        with self._lock:
            self._stop_server()
            data = self._load()
            host = self.host_id()
            client_id = data.get("client_id") or DYNAMIC_CLIENT
            verifier = _b64url(secrets.token_bytes(48))
            pending = {"state": _b64url(secrets.token_bytes(24)), "nonce": _b64url(secrets.token_bytes(24)),
                       "verifier": verifier, "client_id": client_id, "done": False, "error": None,
                       "started": time.time()}
            server = self._listen()
            redirect = f"http://127.0.0.1:{server.server_address[1]}{CALLBACK_PATH}"
            params = {"client_id": client_id, "ext_agent_host_id": host, "response_type": "code",
                      "redirect_uri": redirect, "scope": SCOPES, "resource": self.api_base, "state": pending["state"],
                      "nonce": pending["nonce"], "code_challenge_method": "S256",
                      "code_challenge": _b64url(hashlib.sha256(verifier.encode("ascii")).digest())}
            if client_id == DYNAMIC_CLIENT:
                params["agent_name_hint"] = AGENT_NAME
            else:
                cred = data.get("credentials") or {}
                if cred.get("id_token"):
                    params["id_token_hint"] = cred["id_token"]
                if cred.get("email"):
                    params["login_hint"] = cred["email"]
            pending.update(redirect=redirect, url=f"{self.auth_base}/api/accounts/authorize?{urlencode(params)}")
            self._pending = pending
            threading.Thread(target=server.serve_forever, daemon=True).start()
            timer = threading.Timer(LOGIN_TIMEOUT_S, self._expire, args=(pending,))
            timer.daemon = True
            timer.start()
        try:
            self.open_browser(pending["url"])
        except Exception:  # noqa: BLE001 - the UI shows the link to open by hand
            pass
        return self.status()

    def _listen(self) -> ThreadingHTTPServer:
        login = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # no request logging: the URL carries the code
                pass

            def do_GET(self):  # noqa: N802 - http.server API
                url = urlparse(self.path)
                if url.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                ok, message = login._callback({k: v[0] for k, v in parse_qs(url.query).items()})
                body = (f"<!doctype html><meta charset='utf-8'><title>AI Circuit Studio</title>"
                        f"<p style='font-family:sans-serif'>{html.escape(message)}</p>").encode("utf-8")
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                threading.Thread(target=login._stop_server, daemon=True).start()

        for port in (self.port, 0):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                self._server = server
                return server
            except OSError:
                continue
        raise AiError("AI_REQUEST_FAILED", "로그인 응답을 받을 로컬 포트를 열지 못했습니다.")

    def _stop_server(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    def _expire(self, pending: dict) -> None:
        if not pending.get("done"):
            pending.update(done=True, error="로그인 시간이 지났습니다. 다시 시도하세요.")
            self._stop_server()

    def _callback(self, query: dict) -> tuple[bool, str]:
        with self._lock:
            pending = self._pending
            if pending is None or pending.get("done") or query.get("state") != pending["state"]:
                return False, "이 로그인 응답은 진행 중인 로그인과 맞지 않습니다. 앱에서 다시 시도하세요."
            pending["done"] = True
            if query.get("error"):
                pending["error"] = ("ChatGPT 사용 권한을 허용하지 않았습니다." if query["error"] == "access_denied"
                                    else f"로그인 실패: {query.get('error_description') or query['error']}")
                return False, pending["error"]
            issued = query.get("client_id")
            client_id = pending["client_id"]
            if client_id == DYNAMIC_CLIENT:
                if not issued or issued == DYNAMIC_CLIENT:
                    pending["error"] = "등록이 끝나지 않았습니다(발급된 client_id가 없음). 다시 시도하세요."
                    return False, pending["error"]
                client_id = issued
            elif issued and issued != client_id:
                pending["error"] = "응답의 client_id가 이 계정의 등록과 다릅니다."
                return False, pending["error"]
            try:
                self._exchange(query.get("code") or "", client_id, pending)
            except AiError as exc:
                pending["error"] = exc.message
                return False, exc.message
            except httpx.HTTPError as exc:
                pending["error"] = f"토큰 교환에 실패했습니다: {exc}"
                return False, pending["error"]
            return True, "ChatGPT 로그인이 끝났습니다. AI Circuit Studio로 돌아가세요."

    def _exchange(self, code: str, client_id: str, pending: dict) -> None:
        r = self.http.post(f"{self.auth_base}/api/accounts/oauth/token", data={
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": pending["verifier"], "redirect_uri": pending["redirect"], "resource": self.api_base})
        if r.status_code != 200:
            raise AiError("AI_AUTH_FAILED", f"토큰 교환이 거부되었습니다 ({r.status_code}): {r.text[:200]}")
        tokens = r.json()
        disc = self.discovery()
        jwks = self.http.get(disc["jwks_uri"]).json()
        claims = verify_id_token(tokens.get("id_token") or "", jwks, issuer=disc.get("issuer", self.auth_base),
                                 audience=client_id, nonce=pending["nonce"])
        data = self._load()
        old = data.get("credentials") or {}
        if old.get("subject") and data.get("client_id") == client_id and old["subject"] != claims.get("sub"):
            raise AiError("AI_AUTH_FAILED", "다른 ChatGPT 계정으로 로그인했습니다. 원래 계정으로 다시 시도하세요.",
                          "계정을 바꾸려면 설정에서 '다른 계정으로 바꾸기'를 누르세요.")
        data["client_id"] = client_id
        data["credentials"] = self._record(tokens, claims, old)
        self._save(data)

    @staticmethod
    def _record(tokens: dict, claims: dict | None, old: dict) -> dict:
        return {"email": (claims or {}).get("email") or old.get("email"),
                "subject": (claims or {}).get("sub") or old.get("subject"),
                "id_token": tokens.get("id_token") or old.get("id_token"),
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token") or old.get("refresh_token"),
                "token_type": tokens.get("token_type", "Bearer"), "expires_in": int(tokens.get("expires_in") or 3600),
                "scopes": (tokens.get("scope") or " ".join(old.get("scopes") or [])).split(),
                "saved_at": time.time()}

    # --- tokens --------------------------------------------------------------------------------

    def access_token(self) -> str:
        """A valid access token, refreshed when it is about to expire (one refresh at a time)."""
        with self._lock:
            data = self._load()
            cred = data.get("credentials") or {}
            if not cred.get("access_token") and not cred.get("refresh_token"):
                raise AiError("AI_NO_KEY", "ChatGPT에 로그인하지 않았습니다.", "설정에서 'ChatGPT로 계속하기'를 누르세요.")
            if PLAN_SCOPE not in (cred.get("scopes") or []):
                raise AiError("AI_AUTH_FAILED", "ChatGPT 요금제 사용이 허용되지 않았습니다.",
                              "설정에서 다시 로그인하고 ChatGPT 요금제 사용을 허용하세요.")
            if cred.get("access_token") and time.time() < cred.get("saved_at", 0) + cred.get("expires_in", 0) - REFRESH_MARGIN_S:
                return cred["access_token"]
            r = self.http.post(f"{self.auth_base}/api/accounts/oauth/token", data={
                "grant_type": "refresh_token", "client_id": data.get("client_id"),
                "refresh_token": cred.get("refresh_token"), "resource": self.api_base})
            if r.status_code != 200:
                code = ""
                try:
                    code = r.json().get("error", "")
                except ValueError:
                    pass
                if code in ("invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                            "refresh_token_invalidated", "refresh_token_reused"):
                    data["credentials"] = {k: v for k, v in cred.items() if k in ("email", "subject", "id_token")}
                    self._save(data)
                    raise AiError("AI_AUTH_FAILED", "ChatGPT 로그인이 만료되었습니다.", "설정에서 다시 로그인하세요.")
                raise AiError("AI_UNAVAILABLE", f"ChatGPT 토큰을 갱신하지 못했습니다 ({r.status_code}).",
                              "잠시 후 다시 시도하세요.")
            data["credentials"] = self._record(r.json(), None, cred)
            self._save(data)
            return data["credentials"]["access_token"]

    def logout(self, *, forget: bool = False) -> dict:
        """Revoke and drop the tokens. ``forget`` also drops this app's registration, so the next sign-in
        registers again and may use another ChatGPT account (an issued client stays bound to its account)."""
        with self._lock:
            data = self._load()
            cred = data.get("credentials") or {}
            revoked = None
            if cred.get("refresh_token"):
                try:
                    endpoint = self.discovery().get("revocation_endpoint")
                    if endpoint:
                        r = self.http.post(endpoint, data={"token": cred["refresh_token"],
                                                           "token_type_hint": "refresh_token",
                                                           "client_id": data.get("client_id")})
                        revoked = r.status_code == 200
                except httpx.HTTPError:
                    revoked = False
            # Keep the issued client id and the host id for a later sign-in; drop the tokens.
            data["credentials"] = {k: v for k, v in cred.items() if k in ("email", "subject")}
            if forget:
                data.pop("client_id", None)
                data.pop("credentials", None)
            self._save(data)
            return {**self.status(), "revoked": revoked}


def _map_chatgpt_error(status: int | None, code: str | None, message: str, param: str | None = None) -> AiError:
    text = message or code or "unknown error"
    if code == "subscription_sharing_usage_limit_exceeded":
        return AiError("AI_QUOTA", f"ChatGPT 요금제 사용 한도에 도달했습니다: {text}", USAGE_HINT)
    if code == "subscription_sharing_user_not_eligible":
        return AiError("AI_AUTH_FAILED", f"이 ChatGPT 계정·워크스페이스는 앱에서 요금제를 쓸 수 없습니다: {text}",
                       "ChatGPT Plus/Pro 요금제인지, 워크스페이스 정책이 허용하는지 확인하세요.")
    if code in ("subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable") or status == 503:
        return AiError("AI_UNAVAILABLE", f"ChatGPT 사용량을 확인할 수 없습니다: {text}", "잠시 후 다시 시도하세요.")
    if code == "subscription_sharing_unsupported_capability":
        return AiError("AI_REQUEST_FAILED", f"이 방식에서 지원하지 않는 요청입니다({param or '알 수 없음'}): {text}")
    if status == 401 or code == "subscription_sharing_invalid_user":
        return AiError("AI_AUTH_FAILED", f"ChatGPT 인증이 거부되었습니다: {text}", "설정에서 다시 로그인하세요.")
    if status == 403:
        return AiError("AI_AUTH_FAILED", f"ChatGPT 요청이 허용되지 않았습니다: {text}",
                       "지역 제한이나 계정 정책 때문일 수 있습니다.")
    if status == 404 or code == "model_not_found":
        return AiError("AI_MODEL_NOT_FOUND", f"모델을 찾지 못했습니다: {text}",
                       "설정에서 '모델 목록 불러오기'로 이 계정에서 쓸 수 있는 모델을 고르세요.")
    if status == 429:
        return AiError("AI_QUOTA", f"ChatGPT 요청 한도를 넘었습니다: {text}", USAGE_HINT)
    if status and status >= 500:
        return AiError("AI_UNAVAILABLE", f"OpenAI 서버 오류 ({status}): {text}", "잠시 후 다시 시도하세요.")
    return AiError("AI_REQUEST_FAILED", f"ChatGPT 요청 실패 ({status} {code or ''}): {text}")


class ChatGptProvider:
    """The Responses API with the signed-in account's access token (``store: false``, ``stream: true``)."""

    name = "chatgpt"

    def __init__(self, access_token: str, model: str = DEFAULT_CHATGPT_MODEL, timeout_s: float = 300,
                 api_base: str = API_BASE, http: httpx.Client | None = None):
        if not access_token:
            raise AiError("AI_NO_KEY", "ChatGPT에 로그인하지 않았습니다.", "설정에서 'ChatGPT로 계속하기'를 누르세요.")
        self.token = access_token
        self.model = model or DEFAULT_CHATGPT_MODEL
        self.api_base = api_base.rstrip("/")
        self.http = http or httpx.Client(timeout=httpx.Timeout(float(timeout_s), connect=30.0))

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def generate(self, *, prompt: str, attachments: Iterable[Attachment] = (), json_schema: dict | None = None,
                 max_output_tokens: int = 8192, temperature: float = 0.0) -> LlmResult:
        # max_output_tokens and temperature are not accepted on this route (OpenAI: preview limitations).
        content: list[dict] = []
        for a in attachments:
            data = base64.standard_b64encode(a.data).decode("ascii")
            if a.mime_type == "application/pdf":
                content.append({"type": "input_file", "filename": a.filename,
                                "file_data": f"data:application/pdf;base64,{data}"})
            elif a.mime_type.startswith("image/"):
                content.append({"type": "input_image", "image_url": f"data:{a.mime_type};base64,{data}"})
            else:
                raise AiError("AI_REQUEST_FAILED", f"ChatGPT에 보낼 수 없는 첨부 형식입니다: {a.mime_type}")
        content.append({"type": "input_text", "text": prompt})
        body: dict = {"model": self.model, "input": [{"role": "user", "content": content}], "store": False,
                      "stream": True}
        if json_schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": "result", "schema": json_schema, "strict": False}}
        parts: list[str] = []
        done: dict | None = None
        try:
            with self.http.stream("POST", f"{self.api_base}/responses", headers=self._headers(), json=body) as r:
                if r.status_code != 200:
                    raw = r.read().decode("utf-8", "replace")
                    code, message, param = None, raw[:300], None
                    try:
                        payload = json.loads(raw)
                        err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                        code, param = err.get("code"), err.get("param")
                        message = err.get("message") or payload.get("detail") or message
                    except ValueError:
                        pass
                    raise _map_chatgpt_error(r.status_code, code, str(message), param)
                for event in _sse_events(r.iter_lines()):
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        parts.append(event.get("delta") or "")
                    elif kind == "response.completed":
                        done = event.get("response") or {}
                        break
                    elif kind == "response.failed":
                        err = (event.get("response") or {}).get("error") or {}
                        raise _map_chatgpt_error(None, err.get("code"), err.get("message") or "응답 실패")
                    elif kind == "response.incomplete":
                        reason = ((event.get("response") or {}).get("incomplete_details") or {}).get("reason")
                        raise AiError("AI_BAD_OUTPUT", f"ChatGPT 응답이 끝나지 않았습니다({reason or '이유 모름'}).",
                                      "요청을 줄이거나 다시 시도하세요.")
                    elif kind == "error":
                        raise _map_chatgpt_error(None, event.get("code"), event.get("message") or "오류")
        except httpx.HTTPError as exc:
            raise AiError("AI_UNAVAILABLE", f"OpenAI에 연결하지 못했습니다: {exc}", "네트워크 연결을 확인하세요.") from exc
        if done is None:
            raise AiError("AI_UNAVAILABLE", "ChatGPT 응답 스트림이 완료 전에 끊겼습니다.", "다시 시도하세요.")
        usage = done.get("usage") or {}
        return LlmResult(text="".join(parts), model=done.get("model") or self.model,
                         usage={"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
                         finish_reason="STOP")

    def list_models(self) -> list[ModelInfo]:
        try:
            r = self.http.get(f"{self.api_base}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise AiError("AI_UNAVAILABLE", f"OpenAI에 연결하지 못했습니다: {exc}") from exc
        if r.status_code != 200:
            raise _map_chatgpt_error(r.status_code, None, r.text[:300])
        return [ModelInfo(name=m.get("slug") or m.get("id"), display_name=m.get("display_name") or m.get("slug"),
                          input_token_limit=None, output_token_limit=None)
                for m in r.json().get("models", []) if m.get("visibility", "list") == "list"]


def _sse_events(lines: Iterable[str]):
    """Server-sent events: each ``data:`` payload (JSON) as a dict, with ``type`` from the event name if missing."""
    name, data = None, []
    for line in [*lines, ""]:
        if line == "":
            if data:
                text = "\n".join(data)
                if text.strip() != "[DONE]":
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        payload = {}
                    if isinstance(payload, dict):
                        payload.setdefault("type", name)
                        yield payload
            name, data = None, []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
