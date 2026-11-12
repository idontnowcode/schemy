"""Optional AI features with a fake provider: settings, key storage, PDF import, suggestions, audit records.

No test talks to the real Gemini API (no key in CI); the Gemini adapter is covered
separately with a stubbed SDK client.
"""

import base64
import json
import types as pytypes

import pytest

from ai_engine import AiError, KeyStoreError, LlmResult, ModelInfo
from ai_engine.providers import GeminiProvider, _map_error

from .backend_helpers import TOKEN, new_project, save_ir
from .conftest import EXAMPLE_DIR, SYMBOL_DIR
from .test_backend_inventory import upload

EXAMPLE_MD = (EXAMPLE_DIR / "schematic.md").read_text(encoding="utf-8")
PDF = b"%PDF-1.7\n% fake schematic\n"


class MemoryKeyStore:
    def __init__(self, available=True):
        self.available = available
        self.keys = {}

    def get(self, provider):
        return (self.keys[provider], "keyring") if provider in self.keys else (None, None)

    def set(self, provider, key):
        if not self.available:
            raise KeyStoreError("no keyring")
        self.keys[provider] = key

    def delete(self, provider):
        return self.keys.pop(provider, None) is not None


class FakeProvider:
    name = "gemini"

    def __init__(self, model, reply):
        self.model = model
        self.reply = reply
        self.calls = []

    def generate(self, *, prompt, attachments=(), json_schema=None, max_output_tokens=8192, temperature=0.0):
        self.calls.append({"prompt": prompt, "attachments": list(attachments), "schema": json_schema})
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply(prompt) if callable(self.reply) else self.reply
        return LlmResult(text=text, model=self.model, usage={"total_token_count": 123}, finish_reason="STOP")

    def list_models(self):
        return [ModelInfo("gemini-flash-latest", "Gemini Flash Latest", 1_000_000, 65_536)]


def _no_network(url):
    raise AssertionError(f"a test tried to download {url}")


def ai_client(tmp_path, reply=f"```markdown\n{EXAMPLE_MD}```", keystore=None, fetcher=None, searchers=None):
    created = []

    def factory(provider, key, model, timeout):
        created.append({"provider": provider, "key": key, "model": model})
        p = FakeProvider(model, reply)
        created.append(p)
        return p

    from fastapi.testclient import TestClient

    from backend import Settings, create_app

    settings = Settings(workspace=tmp_path, token=TOKEN, symbol_dirs=[SYMBOL_DIR], kicad_factory=lambda: None,
                        keystore=keystore or MemoryKeyStore(), ai_provider_factory=factory,
                        datasheet_fetcher=fetcher or _no_network,
                        datasheet_searchers=searchers or (lambda keys: []))
    client = TestClient(create_app(settings))
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client, created


def enable(client, key="AIza-test-key-123456"):
    client.put("/api/ai/key", json={"api_key": key})
    return client.put("/api/ai/settings", json={"enabled": True})


def wait(client, job):
    return client.app.state.jobs.wait(job["id"], timeout=60).model_dump()


def test_ai_is_off_by_default_and_key_is_never_returned(tmp_path):
    client, _ = ai_client(tmp_path)
    r = client.get("/api/ai/settings").json()
    assert r["settings"]["enabled"] is False
    assert r["settings"]["model"] == "gemini-flash-latest"
    assert r["key"] == {"configured": False, "source": None, "keyring_available": True}
    enable(client)
    r = client.get("/api/ai/settings").json()
    assert r["key"]["configured"] is True and r["settings"]["enabled"] is True
    assert "AIza" not in json.dumps(r)
    assert "AIza" not in (tmp_path / "index.sqlite").read_bytes().decode("utf-8", "ignore")
    assert client.delete("/api/ai/key").json()["key"]["configured"] is False


def test_connection_test_sends_only_a_fixed_line(tmp_path):
    client, created = ai_client(tmp_path, reply="OK")
    assert client.post("/api/ai/test").json()["error_code"] == "AI_NO_KEY"
    client.put("/api/ai/key", json={"api_key": "AIza-test-key-123456"})
    preview = client.get("/api/ai/test").json()
    assert preview["provider"] == "gemini" and "OK" in preview["prompt"] and preview["key"]["configured"]
    r = client.post("/api/ai/test").json()  # AI features still off: the test works, nothing of the user's is sent
    assert r["ok"] and r["reply"] == "OK" and r["model"] == preview["model"] and r["latency_ms"] >= 0
    fake = next(c for c in created if isinstance(c, FakeProvider))
    assert [c["prompt"] for c in fake.calls] == [preview["prompt"]] and fake.calls[0]["attachments"] == []
    assert "AIza-test-key-123456" not in json.dumps(r)

    failing, _ = ai_client(tmp_path / "x", reply=AiError("AI_AUTH_FAILED", "API key not valid", "키를 확인하세요."))
    failing.put("/api/ai/key", json={"api_key": "AIza-wrong-key-123456"})
    bad = failing.post("/api/ai/test")
    assert bad.status_code == 401 and bad.json()["error_code"] == "AI_AUTH_FAILED"


def test_keyring_unavailable_points_to_env_var(tmp_path):
    client, _ = ai_client(tmp_path, keystore=MemoryKeyStore(available=False))
    r = client.put("/api/ai/key", json={"api_key": "AIza-test-key-123456"})
    assert r.status_code == 409
    assert r.json()["error_code"] == "KEYRING_UNAVAILABLE"
    assert "GEMINI_API_KEY" in r.json()["recovery_hint"]


def test_env_var_key(monkeypatch):
    from ai_engine import KeyStore

    monkeypatch.setenv("GEMINI_API_KEY", "from-env-123456")
    assert KeyStore().get("gemini") == ("from-env-123456", "env")


def test_models_need_a_key(tmp_path):
    client, _ = ai_client(tmp_path)
    assert client.get("/api/ai/models").json()["error_code"] == "AI_NO_KEY"
    client.put("/api/ai/key", json={"api_key": "AIza-test-key-123456"})
    assert client.get("/api/ai/models").json()["models"][0]["name"] == "gemini-flash-latest"


def test_markdown_import_needs_no_ai(tmp_path):
    client, created = ai_client(tmp_path)
    pid = new_project(client)
    r = client.post(f"/api/projects/{pid}/boards/a/imports/markdown", json={"filename": "led.md", "text": EXAMPLE_MD})
    body = r.json()
    assert body["ok"] is True and body["circuit"]["project_id"] == pid
    assert body["stats"]["components"] == 3
    assert body["saved_copy"].endswith("led.md")
    assert created == []  # no provider was created
    assert list((tmp_path / "projects" / pid / "boards" / "a" / "imports").glob("*led.md"))


def test_pdf_import_is_gated(tmp_path):
    client, _ = ai_client(tmp_path)
    pid = new_project(client)
    body = {"filename": "a.pdf", "content_base64": base64.b64encode(PDF).decode()}
    url = f"/api/projects/{pid}/boards/a/imports/pdf"
    assert client.post(url, json=body).json()["error_code"] == "AI_DISABLED"
    client.put("/api/ai/settings", json={"enabled": True})
    assert client.post(url, json=body).json()["error_code"] == "AI_NO_KEY"
    enable(client)
    not_pdf = {"filename": "a.pdf", "content_base64": base64.b64encode(b"hello").decode()}
    assert client.post(url, json=not_pdf).json()["error_code"] == "NOT_A_PDF"


def test_pdf_import_end_to_end_with_fake_provider(tmp_path):
    client, created = ai_client(tmp_path)
    enable(client)
    pid = new_project(client)
    r = client.post(f"/api/projects/{pid}/boards/a/imports/pdf",
                    json={"filename": "led.pdf", "content_base64": base64.b64encode(PDF).decode()})
    assert r.status_code == 202
    job = wait(client, r.json())
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["import"]["ok"] is True
    assert result["markdown"].startswith("# 회로도")  # code fence removed
    provider = created[1]
    call = provider.calls[0]
    assert call["attachments"][0].mime_type == "application/pdf" and call["attachments"][0].data == PDF
    assert "회로도 MD v0.1" in call["prompt"]
    assert created[0]["key"] == "AIza-test-key-123456" and created[0]["model"] == "gemini-flash-latest"
    # Nothing is saved to the circuit: it becomes a proposal the user reviews and approves.
    assert client.get(f"/api/projects/{pid}/boards/a/circuit").json()["circuit"] is None
    proposal = client.get(f"/api/projects/{pid}/boards/a/proposals/{result['proposal_id']}").json()
    assert proposal["source"] == "ai_pdf_import" and proposal["status"] == "ready"
    assert proposal["diff"]["summary"]["components_added"] == 3
    runs = list((tmp_path / "projects" / pid / "boards" / "a" / "ai_runs").glob("*.json"))
    record = json.loads(runs[0].read_text(encoding="utf-8"))
    assert record["kind"] == "pdf_import" and record["status"] == "succeeded"
    assert record["usage"] == {"total_token_count": 123} and record["input"]["bytes"] == len(PDF)
    assert "AIza" not in runs[0].read_text(encoding="utf-8")


def test_pdf_import_failure_is_recorded(tmp_path):
    client, _ = ai_client(tmp_path, reply=AiError("AI_QUOTA", "quota", "later"))
    enable(client)
    pid = new_project(client)
    job = wait(client, client.post(f"/api/projects/{pid}/boards/a/imports/pdf",
                                   json={"filename": "a.pdf", "content_base64": base64.b64encode(PDF).decode()}).json())
    assert job["status"] == "failed" and job["error"]["error_code"] == "AI_QUOTA"
    record = json.loads(next((tmp_path / "projects" / pid / "boards" / "a" / "ai_runs").glob("*.json")).read_text())
    assert record["status"] == "failed"


def test_bad_ai_output_is_rejected(tmp_path):
    client, _ = ai_client(tmp_path, reply="죄송하지만 읽을 수 없습니다.")
    enable(client)
    pid = new_project(client)
    job = wait(client, client.post(f"/api/projects/{pid}/boards/a/imports/pdf",
                                   json={"filename": "a.pdf", "content_base64": base64.b64encode(PDF).decode()}).json())
    assert job["error"]["error_code"] == "AI_BAD_OUTPUT"


def test_suggestions_only_use_listed_parts(tmp_path, led_circuit_data):
    led_circuit_data["components"][1]["footprint_id"] = "Resistor_SMD:R_1206_3216Metric"  # makes R1 a candidate

    def reply(prompt):
        request = json.loads(prompt.split("요청(JSON):\n", 1)[1])
        line = request["lines"][0]
        good = line["candidates"][0]["inventory_part_id"]
        return json.dumps({"suggestions": [
            {"line": line["line"], "inventory_part_id": good, "fit": "acceptable", "reason": "값 동일, 패키지 다름"},
            {"line": line["line"], "inventory_part_id": "inv-made-up", "fit": "good", "reason": "지어낸 부품"},
        ]})

    client, created = ai_client(tmp_path, reply=reply)
    enable(client)
    pid = new_project(client)
    save_ir(client, pid, led_circuit_data)
    upload(client, save=True)
    url = f"/api/projects/{pid}/boards/a"
    preview = client.get(url + "/inventory-suggest/preview").json()
    assert preview["line_count"] == 1 and preview["request"]["lines"][0]["references"] == ["R1"]
    job = wait(client, client.post(url + "/inventory-suggest").json())
    assert job["status"] == "succeeded", job
    suggestions = job["result"]["suggestions"]
    assert [(s["references"], s["fit"]) for s in suggestions] == [(["R1"], "acceptable")]
    line = preview["request"]["lines"][0]["line"]
    assert job["result"]["dropped"] == [f"목록에 없는 제안을 버렸습니다: line={line}, id=inv-made-up"]
    assert created[1].calls[0]["schema"]["required"] == ["suggestions"]
    # Applying is a separate, explicit user action.
    applied = client.post(url + "/inventory-apply", json={
        "apply_matched": False, "choices": {"res-001": suggestions[0]["inventory_part_id"]}}).json()
    assert applied["revision"]["source"] == "inventory"


def test_prompt_file_matches_the_documentation():
    import re

    from ai_engine.tasks import pdf_prompt

    from .conftest import ROOT

    doc = (ROOT.parent / "docs" / "prompts" / "pdf-schematic-to-markdown.md").read_text(encoding="utf-8")
    block = re.search(r"## 프롬프트 본문\n.*?````text\n(.*?)````", doc, re.S).group(1)
    assert pdf_prompt() == block
    example = re.search(r"## 예시: LED.*?````markdown\n(.*?)````", doc, re.S).group(1)
    assert example == EXAMPLE_MD


# --- Gemini adapter with a stubbed SDK client -------------------------------------------

def test_gemini_provider_builds_the_request(monkeypatch):
    sent = {}

    class Models:
        def generate_content(self, *, model, contents, config):
            sent.update(model=model, contents=contents, config=config)
            usage = pytypes.SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15)
            candidate = pytypes.SimpleNamespace(finish_reason=pytypes.SimpleNamespace(value="STOP"))
            return pytypes.SimpleNamespace(text="ok", usage_metadata=usage, candidates=[candidate])

        def list(self):
            return [pytypes.SimpleNamespace(name="models/gemini-flash-latest", display_name="Flash",
                                            supported_actions=["generateContent"], input_token_limit=1,
                                            output_token_limit=2),
                    pytypes.SimpleNamespace(name="models/embed", display_name="Embed",
                                            supported_actions=["embedContent"], input_token_limit=1,
                                            output_token_limit=1)]

    provider = GeminiProvider(api_key="k" * 20, model="gemini-flash-latest")
    provider._client = pytypes.SimpleNamespace(models=Models(), files=None)
    from ai_engine import Attachment

    result = provider.generate(prompt="p", attachments=[Attachment("a.pdf", "application/pdf", b"%PDF")],
                               json_schema={"type": "object"}, max_output_tokens=100)
    assert result.text == "ok" and result.finish_reason == "STOP" and result.usage["total_token_count"] == 15
    assert sent["model"] == "gemini-flash-latest"
    assert sent["contents"][0].inline_data.mime_type == "application/pdf" and sent["contents"][-1] == "p"
    assert sent["config"].response_mime_type == "application/json"
    assert sent["config"].max_output_tokens == 100 and sent["config"].temperature == 0.0
    assert [m.name for m in provider.list_models()] == ["gemini-flash-latest"]


@pytest.mark.parametrize("code, status, message, expected", [
    (403, "PERMISSION_DENIED", "bad key", "AI_AUTH_FAILED"),
    (400, "INVALID_ARGUMENT", "API key not valid", "AI_AUTH_FAILED"),
    (404, "NOT_FOUND", "no model", "AI_MODEL_NOT_FOUND"),
    (429, "RESOURCE_EXHAUSTED", "quota", "AI_QUOTA"),
    (429, "RESOURCE_EXHAUSTED", "Your prepayment credits are depleted. Please go to AI Studio at "
                                "https://ai.studio/projects to manage your project and billing.", "AI_BILLING"),
    (400, "FAILED_PRECONDITION", "Billing account not active", "AI_BILLING"),
    (503, "UNAVAILABLE", "busy", "AI_UNAVAILABLE"),
    (400, "INVALID_ARGUMENT", "other", "AI_REQUEST_FAILED"),
])
def test_gemini_error_mapping(code, status, message, expected):
    assert _map_error(code, status, message).code == expected
