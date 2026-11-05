"""Datasheet API: board report, upload, summary import, downloads from KiCad symbol links, AI summary.

Downloads use a fake fetcher (no internet in tests); the AI summary uses the fake provider."""

import base64
import json

from fastapi.testclient import TestClient

from backend import Settings, create_app
from backend.datasheets import DatasheetError

from .backend_helpers import TOKEN, new_project, save_ir
from .circuits import can_node
from .conftest import FIXTURES, SYMBOL_DIR
from .pdfs import SN65HVD230_TEST_PDF_LINES, make_pdf
from .test_ai import MemoryKeyStore, ai_client, enable, wait

SUMMARY = (FIXTURES / "datasheets" / "sn65hvd230-test.md").read_text(encoding="utf-8")
PDF = make_pdf(SN65HVD230_TEST_PDF_LINES)
TI_URL = "http://www.ti.com/lit/ds/symlink/sn65hvd230.pdf"


def client_with(tmp_path, fetcher=None):
    calls = []

    def fake(url):
        calls.append(url)
        if fetcher:
            return fetcher(url)
        return PDF, url.replace("http://", "https://")

    settings = Settings(workspace=tmp_path, token=TOKEN, symbol_dirs=[SYMBOL_DIR], kicad_factory=lambda: None,
                        keystore=MemoryKeyStore(), datasheet_fetcher=fake)
    client = TestClient(create_app(settings))
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client, calls


def can_project(client, **kwargs):
    pid = new_project(client, "CAN Node")
    save_ir(client, pid, can_node(**kwargs).model_dump(mode="json"))
    return pid, f"/api/projects/{pid}/boards/a"


def row(report, ref):
    return next(c for c in report["components"] if c["reference"] == ref)


def upload(client, part_numbers=("SN65HVD230DR",), symbols=(), pdf=PDF):
    r = client.post("/api/datasheets/upload", json={
        "filename": "sn65hvd230.pdf", "content_base64": base64.b64encode(pdf).decode(),
        "manufacturer": "Texas Instruments", "part_numbers": list(part_numbers), "symbols": list(symbols)})
    assert r.status_code == 200, r.text
    return r.json()["entry"]


def test_report_lists_parts_and_links(tmp_path):
    client, _ = client_with(tmp_path)
    _, board = can_project(client)
    report = client.get(board + "/datasheets").json()
    u1 = row(report, "U1")
    assert u1["identity"] == "mpn" and u1["relevant"] and u1["can_fetch"]
    assert u1["symbol_datasheet_url"] == TI_URL and u1["datasheet"] is None and u1["checks"] is None
    assert not row(report, "C1")["relevant"] and row(report, "C1")["identity"] == "generic"
    sources = [(f["source"], f["url"], f["references"]) for f in report["fetchable"]]
    assert sources == [("kicad_symbol", TI_URL, ["U1"]),
                       ("maker_site", "https://www.ti.com/lit/gpn/sn65hvd230", ["U1"])]
    maker = report["fetchable"][1]
    gpn = "https://www.ti.com/lit/gpn/"
    assert maker["urls"] == [gpn + "sn65hvd230", gpn + "sn65hvd2", gpn + "sn65hvd230dr", gpn + "sn65hvd230d"]
    assert maker["manufacturer"] == "Texas Instruments"
    assert u1["candidates"] == [f["id"] for f in report["fetchable"]] and u1["searched"] is None
    assert report["search"] == {"mouser": False, "digikey": False}


def test_upload_summary_and_checks(tmp_path):
    client, _ = client_with(tmp_path)
    _, board = can_project(client, supply="+5V")
    entry = upload(client)
    assert entry["identity"]["status"] == "family" and entry["path"].endswith(f"{entry['id']}.pdf")
    u1 = row(client.get(board + "/datasheets").json(), "U1")
    assert u1["datasheet"]["id"] == entry["id"] and not u1["can_fetch"] and u1["checks"] is None

    bad = client.post(f"/api/datasheets/{entry['id']}/summary", json={"filename": "x.md", "text": "# 데이터시트: x\n"})
    assert bad.json()["ok"] is False and bad.json()["entry"] is None
    r = client.post(f"/api/datasheets/{entry['id']}/summary", json={"filename": "sn65hvd230.md", "text": SUMMARY})
    assert r.json()["ok"] and r.json()["entry"]["summary"]["origin"] == "markdown"
    assert r.json()["entry"]["summary"]["reviewed"] is False

    u1 = row(client.get(board + "/datasheets").json(), "U1")
    checks = u1["checks"]
    assert checks["pin_map"]["status"] == "match"
    assert checks["ratings"]["rows"][0]["status"] == "outside_recommended"  # +5V on a 3.0-3.6 V part
    assert [x["status"] for x in checks["external"]["rows"]] == ["present", "present", "direct"]
    assert u1["notes"][0]["page"] == 15

    reviewed = client.put(f"/api/datasheets/{entry['id']}/reviewed", json={"reviewed": True}).json()
    assert reviewed["summary"]["reviewed"] is True
    stored = client.get(f"/api/datasheets/{entry['id']}/summary").json()
    assert stored["markdown"] == SUMMARY and stored["facts"]["manufacturer"] == "Texas Instruments"
    assert client.get(f"/api/datasheets/{entry['id']}/file").content == PDF
    # Nothing about the circuit changed.
    assert client.get(board + "/revisions").json()[-1]["revision"] == 1


def test_not_a_pdf_and_unknown_ids(tmp_path):
    client, _ = client_with(tmp_path)
    body = {"filename": "a.pdf", "content_base64": base64.b64encode(b"hi").decode()}
    r = client.post("/api/datasheets/upload", json=body)
    assert r.status_code == 422 and r.json()["error_code"] == "NOT_A_PDF"
    assert client.get("/api/datasheets/ds-nope/summary").json()["error_code"] == "DATASHEET_NOT_FOUND"
    assert client.delete("/api/datasheets/ds-nope").status_code == 404


def test_fetch_only_confirmed_library_links(tmp_path):
    client, calls = client_with(tmp_path)
    pid, board = can_project(client)
    r = client.post(board + "/datasheets/fetch", json={"urls": ["https://evil.example/x.pdf"]})
    assert r.status_code == 422 and r.json()["error_code"] == "URL_NOT_FROM_LIBRARY" and calls == []
    job = wait(client, client.post(board + "/datasheets/fetch", json={"urls": [TI_URL]}).json())
    assert job["status"] == "succeeded" and calls == [TI_URL]
    result = job["result"]["results"][0]
    assert result["ok"] and result["references"] == ["U1"] and result["identity"] == "matched"
    assert result["source"] == "kicad_symbol" and result["tried"] == []
    entry = client.get("/api/datasheets").json()["entries"][0]
    assert entry["source"] == "kicad_symbol" and entry["urls"] == [TI_URL]
    assert entry["part_numbers"] == ["SN65HVD230DR"] and entry["symbols"] == ["Interface_CAN_LIN:SN65HVD230"]
    assert entry["manufacturer"] == "Texas Instruments" and entry["original_name"] == "sn65hvd230.pdf"
    report = client.get(board + "/datasheets").json()
    assert report["fetchable"] == [] and row(report, "U1")["datasheet"]["id"] == entry["id"]


def test_fetch_failures_are_reported_per_link(tmp_path):
    def broken(url):
        raise DatasheetError("DATASHEET_NOT_PDF", "링크가 PDF가 아닙니다", "브라우저로 받으세요")

    client, _ = client_with(tmp_path, fetcher=broken)
    _, board = can_project(client)
    job = wait(client, client.post(board + "/datasheets/fetch", json={"urls": [TI_URL]}).json())
    assert job["status"] == "succeeded" and job["result"]["failed"] == 1
    result = job["result"]["results"][0]
    assert result["error_code"] == "DATASHEET_NOT_PDF"
    # The symbol link failed, then the four TI addresses of the maker rule were tried (fallback).
    assert [t["source"] for t in result["tried"]] == ["kicad_symbol"] + ["maker_site"] * 4
    assert client.get("/api/datasheets").json()["entries"] == []


def test_ai_summary(tmp_path):
    client, created = ai_client(tmp_path, reply=f"```markdown\n{SUMMARY}```")
    pid = new_project(client, "CAN")
    save_ir(client, pid, can_node().model_dump(mode="json"))
    board = f"/api/projects/{pid}/boards/a"
    entry = upload(client)
    url = f"{board}/datasheets/{entry['id']}/summarize"
    assert client.post(url).json()["error_code"] == "AI_DISABLED"
    enable(client)
    job = wait(client, client.post(url).json())
    assert job["status"] == "succeeded", job
    assert job["result"]["saved"] is True and job["result"]["import"]["ok"] is True
    call = created[1].calls[0]
    assert call["attachments"][0].data == PDF and "데이터시트 요약 MD v0.1" in call["prompt"]
    summary = client.get(f"/api/datasheets/{entry['id']}/summary").json()["entry"]["summary"]
    assert summary["origin"] == "ai" and summary["model"] == "gemini-flash-latest" and summary["reviewed"] is False
    runs = list((tmp_path / "datasheets" / "ai_runs").glob("*.json"))
    record = json.loads(runs[0].read_text(encoding="utf-8"))
    assert record["kind"] == "datasheet_summary" and record["input"]["sha256"] == entry["sha256"]
    assert "AIza" not in runs[0].read_text(encoding="utf-8")


def test_ai_summary_bad_output_saves_nothing(tmp_path):
    client, _ = ai_client(tmp_path, reply="I cannot read this file.")
    pid = new_project(client, "CAN")
    save_ir(client, pid, can_node().model_dump(mode="json"))
    entry = upload(client)
    enable(client)
    job = wait(client, client.post(f"/api/projects/{pid}/boards/a/datasheets/{entry['id']}/summarize").json())
    assert job["status"] == "failed" and job["error"]["error_code"] == "AI_BAD_OUTPUT"
    assert client.get(f"/api/datasheets/{entry['id']}/summary").json()["entry"]["summary"] is None


def test_markdown_import_reads_manufacturer(tmp_path):
    client, _ = client_with(tmp_path)
    pid = new_project(client)
    md = (FIXTURES.parent.parent / "examples" / "led-resistor-connector" / "schematic.md").read_text(encoding="utf-8")
    md = md.replace("| R1 | 330 | 저항 | 0603 | ? | ? |", "| R1 | 330 | 저항 | 0603 | Yageo | RC0603FR-07330RL |")
    r = client.post(f"/api/projects/{pid}/boards/a/imports/markdown", json={"filename": "led.md", "text": md}).json()
    r1 = next(c for c in r["circuit"]["components"] if c["reference"] == "R1")
    assert (r1["manufacturer"], r1["manufacturer_part_number"]) == ("Yageo", "RC0603FR-07330RL")


def test_generic_symbols_are_never_linked_and_prompts_are_served(tmp_path):
    client, _ = client_with(tmp_path)
    entry = upload(client, part_numbers=(), symbols=("Device:R", "Interface_CAN_LIN:SN65HVD230"))
    assert entry["symbols"] == ["Interface_CAN_LIN:SN65HVD230"]
    text = client.get("/api/prompts/datasheet-summary").json()["text"]
    assert text.startswith("당신은 전자부품 데이터시트를") and "데이터시트 요약 MD v0.1" in text
    assert "회로도 MD v0.1" in client.get("/api/prompts/pdf-schematic").json()["text"]
    assert client.get("/api/prompts/nope").status_code == 404
