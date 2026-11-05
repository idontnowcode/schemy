"""ChatGPT with the user's own account (Sign in with ChatGPT), against a stand-in for OpenAI's servers.

The browser step is played by the test: it reads the authorize URL the app built and calls the app's loopback
callback the way auth.openai.com redirects. Everything else - token exchange, ID-token signature check, scopes,
streaming inference, refresh and sign-out - runs through the real code paths.
"""

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ai_engine import AiError
from ai_engine.chatgpt import DYNAMIC_CLIENT, PLAN_SCOPE, ChatGptLogin, ChatGptProvider, verify_id_token

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign(claims: dict, kid: str = "k1") -> str:
    head = b64url(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    body = b64url(json.dumps(claims).encode())
    sig = KEY.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{b64url(sig)}"


def jwks() -> dict:
    n = KEY.public_key().public_numbers()
    modulus = n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")
    return {"keys": [{"kty": "RSA", "kid": "k1", "alg": "RS256",
                      "n": b64url(modulus), "e": b64url(n.e.to_bytes(3, "big"))}]}


class FakeOpenAI:
    """auth.openai.com + api.openai.com, just enough of them."""

    def __init__(self, scopes: str = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"):
        self.scopes = scopes
        self.codes: dict[str, dict] = {}
        self.requests: list[tuple[str, dict]] = []
        self.responses_bodies: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, payload, ctype="application/json"):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path == "/.well-known/openid-configuration":
                    return self.reply(200, {"issuer": fake.base, "jwks_uri": f"{fake.base}/jwks",
                                            "revocation_endpoint": f"{fake.base}/revoke"})
                if self.path == "/jwks":
                    return self.reply(200, jwks())
                if self.path == "/v1/models":
                    return self.reply(200, {"models": [
                        {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1", "visibility": "list"},
                        {"slug": "hidden", "visibility": "hide"}]})
                return self.reply(404, {"error": "nope"})

            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/api/accounts/oauth/token":
                    form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    fake.requests.append(("token", form))
                    if form["grant_type"] == "authorization_code":
                        pending = fake.codes.pop(form["code"], None)
                        if pending is None or pending["client_id"] != form["client_id"]:
                            return self.reply(400, {"error": "invalid_grant"})
                        id_token = sign({"iss": fake.base, "aud": form["client_id"], "sub": "user-1",
                                         "email": "me@example.com", "exp": time.time() + 3600,
                                         "nonce": pending["nonce"]})
                        return self.reply(200, {"access_token": "at-1", "refresh_token": "rt-1", "id_token": id_token,
                                                "token_type": "Bearer", "expires_in": 3600, "scope": fake.scopes})
                    if form["grant_type"] == "refresh_token":
                        if form["refresh_token"] != "rt-1":
                            return self.reply(400, {"error": "invalid_grant"})
                        return self.reply(200, {"access_token": "at-2", "refresh_token": "rt-2", "token_type": "Bearer",
                                                "expires_in": 3600, "scope": fake.scopes})
                if self.path == "/revoke":
                    fake.requests.append(("revoke", {k: v[0] for k, v in parse_qs(raw.decode()).items()}))
                    return self.reply(200, b"", "text/plain")
                if self.path == "/v1/responses":
                    body = json.loads(raw)
                    fake.responses_bodies.append({"auth": self.headers.get("Authorization"), **body})
                    events = [("response.created", {"response": {"id": "r1"}}),
                              ("response.output_text.delta", {"delta": '{"ok": '}),
                              ("response.output_text.delta", {"delta": "true}"}),
                              ("response.completed", {"response": {"model": body["model"],
                                                                   "usage": {"input_tokens": 12, "output_tokens": 4}}})]
                    text = "".join(f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n"
                                   for name, data in events)
                    return self.reply(200, text.encode(), "text/event-stream")
                return self.reply(404, {"error": "nope"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def browser(self, url: str, *, deny: bool = False):
        """What the user's browser does: sign in, approve, and get redirected to the app's loopback callback."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert urlparse(url).path == "/api/accounts/authorize"
        issued = "oaiapp_test123" if q["client_id"] == DYNAMIC_CLIENT else q["client_id"]
        code = f"code-{len(self.codes)}"
        self.codes[code] = {"client_id": issued, "nonce": q["nonce"], "query": q}
        params = {"error": "access_denied", "state": q["state"]} if deny else {"code": code, "state": q["state"],
                                                                              "client_id": issued, "scope": self.scopes}

        def go():
            time.sleep(0.05)
            httpx.get(q["redirect_uri"], params=params, timeout=10)
        threading.Thread(target=go, daemon=True).start()
        return q


def wait_done(login: ChatGptLogin) -> dict:
    for _ in range(100):
        s = login.status()
        if not s["pending"]:
            return s
        time.sleep(0.05)
    raise AssertionError("login did not finish")


@pytest.fixture
def fake():
    f = FakeOpenAI()
    yield f
    f.server.shutdown()


def make_login(tmp_path, fake, opened=None):
    opened = opened if opened is not None else []

    def browser(url):
        opened.append(fake.browser(url))
    return ChatGptLogin(tmp_path, auth_base=fake.base, api_base=f"{fake.base}/v1", open_browser=browser, port=0), opened


def test_sign_in_registers_once_and_calls_the_responses_api(tmp_path, fake):
    login, opened = make_login(tmp_path, fake)
    login.start()
    status = wait_done(login)
    assert status["logged_in"] and status["plan_usage"] and status["email"] == "me@example.com", status
    first = opened[0]
    assert first["client_id"] == DYNAMIC_CLIENT and first["agent_name_hint"] == "AI Circuit Studio"
    assert first["code_challenge_method"] == "S256" and first["resource"] == f"{fake.base}/v1"
    assert first["ext_agent_host_id"].startswith("urn:uuid:") and first["redirect_uri"].endswith("/auth/callback")
    assert first["redirect_uri"].startswith("http://127.0.0.1:") and PLAN_SCOPE in first["scope"].split()
    saved = json.loads((tmp_path / "chatgpt.json").read_text())
    assert saved["client_id"] == "oaiapp_test123" and saved["credentials"]["refresh_token"] == "rt-1"
    assert (tmp_path / "chatgpt.json").stat().st_mode & 0o077 == 0  # only the user can read it

    provider = ChatGptProvider(login.access_token(), model="gpt-6.1-sol", api_base=f"{fake.base}/v1")
    result = provider.generate(prompt="회로 설계", json_schema={"type": "object"}, max_output_tokens=1000)
    assert result.text == '{"ok": true}' and result.usage == {"input_tokens": 12, "output_tokens": 4}
    sent = fake.responses_bodies[0]
    assert sent["auth"] == "Bearer at-1" and sent["store"] is False and sent["stream"] is True
    assert "max_output_tokens" not in sent and "temperature" not in sent
    assert sent["text"]["format"]["type"] == "json_schema"
    assert [m.name for m in provider.list_models()] == ["gpt-6.1-sol"]

    # Sign in again: the issued client id and the same host id are reused, with the ID token as a hint.
    login.start()
    wait_done(login)
    again = opened[1]
    assert again["client_id"] == "oaiapp_test123" and "agent_name_hint" not in again
    assert again["ext_agent_host_id"] == first["ext_agent_host_id"] and again["id_token_hint"]


def test_refresh_and_sign_out(tmp_path, fake):
    login, opened = make_login(tmp_path, fake)
    login.start()
    wait_done(login)
    data = json.loads((tmp_path / "chatgpt.json").read_text())
    data["credentials"]["saved_at"] = time.time() - 3500  # about to expire
    (tmp_path / "chatgpt.json").write_text(json.dumps(data))
    assert login.access_token() == "at-2"
    refresh = [f for kind, f in fake.requests if kind == "token" and f["grant_type"] == "refresh_token"][0]
    assert refresh["client_id"] == "oaiapp_test123" and refresh["resource"] == f"{fake.base}/v1"
    out = login.logout()
    assert out["revoked"] is True and not out["logged_in"] and out["registered"]
    assert [f for kind, f in fake.requests if kind == "revoke"][0]["token"] == "rt-2"
    with pytest.raises(AiError) as exc:
        login.access_token()
    assert exc.value.code == "AI_NO_KEY"

    # Switching accounts drops the registration: the next sign-in registers again.
    out = login.logout(forget=True)
    assert not out["registered"] and json.loads((tmp_path / "chatgpt.json").read_text())["ext_agent_host_id"]
    login.start()
    assert opened[-1]["client_id"] == DYNAMIC_CLIENT and "id_token_hint" not in opened[-1]
    assert wait_done(login)["logged_in"]


def test_declined_plan_usage_and_bad_tokens(tmp_path, fake):
    login, _ = make_login(tmp_path, fake)
    login.open_browser = lambda url: fake.browser(url, deny=True)
    login.start()
    assert "허용하지 않았습니다" in wait_done(login)["error"]

    fake.scopes = "openid profile email offline_access"  # signed in, but no permission to use the plan
    login.open_browser = lambda url: fake.browser(url)
    login.start()
    status = wait_done(login)
    assert status["logged_in"] and not status["plan_usage"]
    with pytest.raises(AiError) as exc:
        login.access_token()
    assert exc.value.code == "AI_AUTH_FAILED"

    claims = {"iss": "https://auth.openai.com", "aud": "c1", "exp": time.time() + 60, "nonce": "n"}
    check = {"issuer": "https://auth.openai.com", "audience": "c1", "nonce": "n"}
    assert verify_id_token(sign(claims), jwks(), **check)["aud"] == "c1"
    for bad in ({"nonce": "other"}, {"aud": "c2"}, {"exp": time.time() - 3600}):
        with pytest.raises(AiError):
            verify_id_token(sign({**claims, **bad}), jwks(), **check)
    forged = sign(claims)[:-4] + "AAAA"
    with pytest.raises(AiError):
        verify_id_token(forged, jwks(), **check)


def test_backend_settings_and_login_routes(tmp_path, fake):
    from .backend_helpers import make_client

    login, _ = make_login(tmp_path / "cfg", fake)
    client = make_client(tmp_path / "ws")
    client.app.state.settings.chatgpt_login = login
    r = client.put("/api/ai/settings", json={"provider": "chatgpt", "enabled": True}).json()
    assert r["settings"]["model"] == "gpt-6.1-sol" and r["key"]["configured"] is False and "chatgpt" in r["providers"]
    assert client.put("/api/ai/key", json={"api_key": "sk-123456789012"}).json()["error_code"] == "LOGIN_ONLY"
    started = client.post("/api/ai/chatgpt/login").json()
    assert started["authorize_url"] or started["logged_in"]
    wait_done(login)
    status = client.get("/api/ai/settings").json()["key"]
    assert status["configured"] and status["login"]["email"] == "me@example.com"
    out = client.post("/api/ai/chatgpt/logout").json()
    assert not out["logged_in"]
