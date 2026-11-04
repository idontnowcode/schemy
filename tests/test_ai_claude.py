"""Claude as AI provider: the real ``anthropic`` SDK against a mock HTTP transport, the ``ant auth login``
profile check, the CLI login runner and the backend settings. Nothing talks to the real API."""

import json
import subprocess

import httpx2
import pytest

from ai_engine import AiError, Attachment, claude_login_status
from ai_engine.providers import CLAUDE_FALLBACK_BETA, ClaudeProvider
from backend.claude_login import ClaudeLogin

from .test_ai import ai_client


def sse(text="{\"ok\": true}", stop="end_turn", model="claude-opus-5-5"):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 11, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"output_tokens": 7}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    body = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def provider(handler, model="claude-opus-5-5"):
    import anthropic

    client = anthropic.Anthropic(api_key="sk-ant-test", max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return ClaudeProvider(model=model, client=client)


def test_request_shape_and_result():
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return sse()

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"},
                                               "items": {"type": "array", "items": {"type": "object", "properties": {}}}},
              "required": ["ok"]}
    result = provider(handler).generate(prompt="회로를 설계하세요", attachments=[Attachment("ds.pdf", "application/pdf", b"%PDF-1.7")],
                                        json_schema=schema, max_output_tokens=4000, temperature=0.0)
    assert result.text == '{"ok": true}' and result.finish_reason == "STOP"
    assert result.usage == {"input_tokens": 11, "output_tokens": 7}
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 4000 and body["stream"] is True
    assert "temperature" not in body and "thinking" not in body
    assert body["output_config"]["effort"] == "high"
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["properties"]["items"]["items"]["additionalProperties"] is False
    assert body["fallbacks"] == "default" and CLAUDE_FALLBACK_BETA in seen["headers"]["anthropic-beta"]
    content = body["messages"][0]["content"]
    assert [c["type"] for c in content] == ["document", "text"]
    assert content[0]["source"] == {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjc="}
    assert seen["headers"]["x-api-key"] == "sk-ant-test"


def test_haiku_has_no_server_side_fallback():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return sse(model="claude-haiku-5-5")

    provider(handler, model="claude-haiku-5-5").generate(prompt="hi")
    assert "fallbacks" not in seen["body"] and not seen["beta"]


def test_truncated_and_refused_answers():
    assert provider(lambda r: sse(stop="max_tokens")).generate(prompt="x").finish_reason == "MAX_TOKENS"
    with pytest.raises(AiError) as err:
        provider(lambda r: sse(text="", stop="refusal")).generate(prompt="x")
    assert err.value.code == "AI_REFUSED"


@pytest.mark.parametrize(("status", "kind", "message", "code"), [
    (401, "authentication_error", "invalid x-api-key", "AI_AUTH_FAILED"),
    (400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API.", "AI_BILLING"),
    (404, "not_found_error", "model: claude-nope", "AI_MODEL_NOT_FOUND"),
    (429, "rate_limit_error", "Number of request tokens has exceeded your rate limit", "AI_QUOTA"),
    (529, "overloaded_error", "Overloaded", "AI_UNAVAILABLE"),
])
def test_errors_map_to_our_codes(status, kind, message, code):
    def handler(request):
        return httpx2.Response(status, json={"type": "error", "error": {"type": kind, "message": message}})

    with pytest.raises(AiError) as err:
        provider(handler).generate(prompt="x")
    assert err.value.code == code


def test_list_models():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx2.Response(200, json={"data": [
            {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5", "created_at": "2026-09-01T00:00:00Z",
             "max_input_tokens": 1000000, "max_tokens": 128000}], "has_more": False, "first_id": None, "last_id": None})

    models = provider(handler).list_models()
    assert [(m.name, m.display_name, m.output_token_limit) for m in models] == [("claude-opus-5-5", "Claude Opus 5.5", 128000)]


# --- ``ant auth login`` profile ----------------------------------------------------------------------

@pytest.fixture
def anthropic_home(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "anthropic"))
    return tmp_path / "anthropic"


def write_profile(base, name="default"):
    for folder in ("configs", "credentials"):
        (base / folder).mkdir(parents=True, exist_ok=True)
        (base / folder / f"{name}.json").write_text("{}", encoding="utf-8")


def test_login_profile_is_detected_without_reading_it(anthropic_home, monkeypatch):
    assert claude_login_status()["logged_in"] is False
    write_profile(anthropic_home)
    assert claude_login_status() == {"logged_in": True, "source": "ant_login", "profile": "default"}
    (anthropic_home / "active_config").write_text("work\n", encoding="utf-8")
    assert claude_login_status()["logged_in"] is False  # the active profile has no credentials yet
    write_profile(anthropic_home, "work")
    assert claude_login_status()["profile"] == "work"
    (anthropic_home / "active_config").write_text("../evil", encoding="utf-8")
    assert claude_login_status()["logged_in"] is False
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    assert claude_login_status()["source"] == "env_token"


def test_cli_login_runs_ant_with_an_argument_list(anthropic_home):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        write_profile(anthropic_home)
        return subprocess.CompletedProcess(argv, 0, "", "")

    login = ClaudeLogin(run=run, which=lambda name: f"/usr/bin/{name}")
    state = login.start()
    assert state["started"] is True
    login._thread.join(5)
    assert calls[0][0] == ["/usr/bin/ant", "auth", "login"]
    assert calls[0][1]["stdin"] == subprocess.DEVNULL and calls[0][1]["timeout"] > 0 and "shell" not in calls[0][1]
    status = login.status()
    assert status["logged_in"] is True and status["running"] is False and status["last_error"] is None


def test_cli_login_failure_and_missing_cli(anthropic_home):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "error: login cancelled")

    login = ClaudeLogin(run=run, which=lambda name: "/usr/bin/ant")
    login.start()
    login._thread.join(5)
    assert "login cancelled" in login.status()["last_error"]
    missing = ClaudeLogin(run=run, which=lambda name: None)
    state = missing.start()
    assert state["started"] is False and state["cli_found"] is False and "anthropic-cli" in state["install_hint"]


# --- backend ------------------------------------------------------------------------------------

def test_switching_to_claude_uses_its_default_model_and_login(tmp_path, anthropic_home, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no `ant` on PATH
    monkeypatch.delenv("AICS_ANT_CLI", raising=False)
    client, created = ai_client(tmp_path)
    r = client.put("/api/ai/settings", json={"provider": "claude", "enabled": True}).json()
    assert r["settings"]["provider"] == "claude" and r["settings"]["model"] == "claude-opus-5-5"
    assert r["providers"] == ["gemini", "claude"]
    assert r["key"]["configured"] is False and r["key"]["login"]["logged_in"] is False
    no_key = client.get("/api/ai/models").json()
    assert no_key["error_code"] == "AI_NO_KEY" and "Claude" in no_key["message"]

    write_profile(anthropic_home)  # as if `ant auth login` had run
    r = client.get("/api/ai/settings").json()
    assert r["key"]["configured"] is True and r["key"]["source"] == "login"
    assert client.get("/api/ai/models").status_code == 200
    assert created[0] == {"provider": "claude", "key": None, "model": "claude-opus-5-5"}

    # An API key in the OS store wins over the login, as in the SDK.
    client.put("/api/ai/key", json={"api_key": "sk-ant-api-key-123456"})
    assert client.get("/api/ai/settings").json()["key"]["source"] == "keyring"
    client.get("/api/ai/models")
    assert [c for c in created if isinstance(c, dict)][-1]["key"] == "sk-ant-api-key-123456"

    # Back to Gemini: its default model again, its own key slot.
    r = client.put("/api/ai/settings", json={"provider": "gemini"}).json()
    assert r["settings"]["model"] == "gemini-flash-latest" and r["key"]["configured"] is False


def test_login_endpoint_without_cli(tmp_path, anthropic_home, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("AICS_ANT_CLI", raising=False)
    client, _ = ai_client(tmp_path)
    assert client.get("/api/ai/claude/login").json()["cli_found"] is False
    r = client.post("/api/ai/claude/login")
    assert r.status_code == 409 and r.json()["error_code"] == "ANT_CLI_NOT_FOUND"
