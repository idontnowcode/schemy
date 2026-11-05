"""Export jobs, the outputs manifest, stale / external-edit detection, and the determinism gate."""

import json

import pytest

from kicad_adapter import KiCadCli

from .backend_helpers import led_ir, make_client, new_project, run_export, save_ir


def outputs_dir(tmp_path, pid):
    return tmp_path / "projects" / pid / "boards" / "a" / "outputs"


def checksums(client, pid):
    manifest = client.get(f"/api/projects/{pid}/boards/a/outputs").json()["manifest"]
    return {a["path"]: a["checksum"] for a in manifest["artifacts"]}


def test_export_needs_a_circuit(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    r = client.post(f"/api/projects/{pid}/boards/a/exports", json={})
    assert r.status_code == 409
    assert r.json()["error_code"] == "NO_CIRCUIT"


def test_export_without_kicad_is_not_checked(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    job = run_export(client, pid)
    assert job["status"] == "succeeded", job
    check = job["result"]["automated_check"]
    assert check["status"] == "not_checked"
    assert "kicad-cli not found" in check["reason"]
    assert set(job["result"]["artifacts"]) == {
        "led-demo-a.kicad_sch", "led-demo-a.kicad_pro", "circuit.json", "circuit.md", "rationale.md", "bom.csv",
        "symbol_sources.json"}

    status = client.get(f"/api/projects/{pid}/boards/a/outputs").json()
    assert status["stale"] is False and status["externally_modified"] == [] and status["missing"] == []
    manifest = status["manifest"]
    assert manifest["source_revision"] == 1
    sch = next(a for a in manifest["artifacts"] if a["path"] == "led-demo-a.kicad_sch")
    assert sch["artifact_type"] == "schematic"
    assert sch["validation_status"] == "not_checked"
    assert len(sch["checksum"]) == 64
    assert next(a for a in manifest["artifacts"] if a["path"] == "bom.csv")["validation_status"] is None
    # No staging or backup folders are left behind.
    assert sorted(p.name for p in (tmp_path / "projects" / pid).iterdir() if p.name.startswith(".")) == []


def test_new_revision_makes_outputs_stale(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    run_export(client, pid)
    ir = led_ir()
    ir["components"][1]["value"] = "1k"
    save_ir(client, pid, ir)
    assert client.get(f"/api/projects/{pid}/boards/a/outputs").json()["stale"] is True
    run_export(client, pid)
    assert client.get(f"/api/projects/{pid}/boards/a/outputs").json()["stale"] is False


def test_external_edit_blocks_regeneration_until_forced(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    run_export(client, pid)
    sch = outputs_dir(tmp_path, pid) / "led-demo-a.kicad_sch"
    sch.write_text(sch.read_text(encoding="utf-8").replace('"eeschema"', '"x"') + "\n", encoding="utf-8")
    # KiCad rewriting the project file on open/close is not an external edit.
    pro = outputs_dir(tmp_path, pid) / "led-demo-a.kicad_pro"
    pro.write_text(pro.read_text(encoding="utf-8").replace('"version": 1', '"version": 3'), encoding="utf-8")

    status = client.get(f"/api/projects/{pid}/boards/a/outputs").json()
    assert status["externally_modified"] == ["led-demo-a.kicad_sch"]
    r = client.post(f"/api/projects/{pid}/boards/a/exports", json={})
    assert r.status_code == 409
    assert r.json()["error_code"] == "EXTERNAL_MODIFICATION"
    assert r.json()["details"] == {"paths": ["led-demo-a.kicad_sch"]}

    assert run_export(client, pid, force=True)["status"] == "succeeded"
    assert client.get(f"/api/projects/{pid}/boards/a/outputs").json()["externally_modified"] == []


def test_generation_errors_fail_the_job_and_keep_old_outputs(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    run_export(client, pid)
    before = (outputs_dir(tmp_path, pid) / "manifest.json").read_text(encoding="utf-8")

    swapped = led_ir()
    swapped["components"][2]["pins"] = [{"number": "1", "name": "A"}, {"number": "2", "name": "K"}]
    save_ir(client, pid, swapped)
    job = run_export(client, pid)
    assert job["status"] == "failed"
    assert job["error"]["error_code"] == "GENERATION_FAILED"
    assert {i["error_code"] for i in job["error"]["details"]["issues"]} == {"PIN_NAME_MISMATCH"}
    assert job["error"]["affected_component_ids"] == ["led-001"]
    assert (outputs_dir(tmp_path, pid) / "manifest.json").read_text(encoding="utf-8") == before
    history = client.get(f"/api/projects/{pid}/boards/a/jobs").json()
    assert [j["status"] for j in history] == ["failed", "succeeded"]


def test_missing_symbol_library_is_reported(tmp_path):
    client = make_client(tmp_path, symbol_dirs=[])
    pid = new_project(client)
    save_ir(client, pid)
    job = run_export(client, pid)
    assert job["status"] == "failed"
    assert job["error"]["error_code"] == "NO_SYMBOL_LIBRARY"


def test_output_files_are_served_by_name_only(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    run_export(client, pid)
    r = client.get(f"/api/projects/{pid}/boards/a/outputs/files/led-demo-a.kicad_sch")
    assert r.status_code == 200 and r.text.startswith("(kicad_sch")
    assert client.get(f"/api/projects/{pid}/boards/a/outputs/files/led-demo-a.svg").status_code == 404  # not generated
    assert client.get(f"/api/projects/{pid}/boards/a/outputs/files/manifest.json").status_code == 404
    assert client.get(f"/api/projects/{pid}/boards/a/outputs/files/..%2Fproject.json").status_code == 404


# --- Phase 1 gate: same input -> same output ---------------------------------------

def test_same_input_gives_same_files(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    run_export(client, pid)
    first = checksums(client, pid)
    run_export(client, pid)
    assert checksums(client, pid) == first

    # A second app instance (restart) on a fresh workspace produces the same bytes too.
    other = make_client(tmp_path / "other")
    pid2 = new_project(other)
    assert pid2 == pid
    save_ir(other, pid2)
    run_export(other, pid2)
    assert checksums(other, pid2) == first


@pytest.mark.kicad
def test_export_with_kicad_end_to_end(tmp_path, kicad_cli):
    client = make_client(tmp_path, kicad_factory=lambda: kicad_cli)
    pid = new_project(client)
    save_ir(client, pid)
    job = run_export(client, pid)
    assert job["status"] == "succeeded", job
    check = job["result"]["automated_check"]
    assert check["status"] == "passed", check
    assert (check["erc_errors"], check["erc_warnings"], check["netlist_matches_ir"]) == (0, 0, True)
    assert check["kicad_version"].startswith("10.")
    out = outputs_dir(tmp_path, pid)
    for name in ("led-demo-a.svg", "led-demo-a.net", "erc.json", "check_report.json"):
        assert (out / name).is_file(), name
    assert not list(out.glob("*.kicad_prl"))
    svg = client.get(f"/api/projects/{pid}/boards/a/outputs/files/led-demo-a.svg")
    assert svg.headers["content-type"] == "image/svg+xml" and "<svg" in svg.text
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    tools = {a["path"]: a["tool_version"] for a in manifest["artifacts"]}
    assert tools["erc.json"].startswith("kicad-cli 10.")
    assert tools["led-demo-a.kicad_sch"] == "ai_circuit_studio 0.1"
    assert client.get("/api/system").json()["kicad"]["available"] is True


@pytest.mark.kicad
def test_erc_failure_is_reported_not_hidden(tmp_path, kicad_cli):
    client = make_client(tmp_path, kicad_factory=lambda: kicad_cli)
    pid = new_project(client)
    ir = led_ir()
    ir["nets"][2]["nodes"] = [{"component_id": "conn-001", "pin": "2"}]  # LED cathode left open
    save_ir(client, pid, ir)
    job = run_export(client, pid)
    assert job["status"] == "succeeded"  # the job ran; the check failed
    check = job["result"]["automated_check"]
    assert check["status"] == "failed"
    assert check["erc_errors"] >= 1
    assert any(v["type"] == "pin_not_connected" for v in check["violations"])


def test_kicad_cli_detect_is_the_default_factory():
    from backend import Settings

    assert Settings().kicad_factory == KiCadCli.detect



def test_multi_page_export_lists_pages(tmp_path):
    from .circuits import two_page_led

    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid, two_page_led().model_dump(mode="json"))
    job = run_export(client, pid)
    assert job["status"] == "succeeded", job
    manifest = client.get(f"/api/projects/{pid}/boards/a/outputs").json()["manifest"]
    assert [(p["number"], p["name"], p["schematic"]) for p in manifest["pages"]] == [
        (1, "전원 입력", "led-demo-a.kicad_sch"), (2, "LED 출력", "led-demo-a-p2-led.kicad_sch")]
    assert all(p["svg"] is None and p["paper"] == "A4" and len(p["bbox_mm"]) == 4 for p in manifest["pages"])
    paths = {a["path"] for a in manifest["artifacts"]}
    assert {"led-demo-a.kicad_sch", "led-demo-a-p2-led.kicad_sch", "led-demo-a.kicad_pro"} <= paths
    r = client.get(f"/api/projects/{pid}/boards/a/outputs/files/led-demo-a-p2-led.kicad_sch")
    assert r.status_code == 200 and "LED_A" in r.text


@pytest.mark.kicad
def test_multi_page_export_with_kicad(tmp_path, kicad_cli):
    from .circuits import two_page_led

    client = make_client(tmp_path, kicad_factory=lambda: kicad_cli)
    pid = new_project(client)
    save_ir(client, pid, two_page_led().model_dump(mode="json"))
    job = run_export(client, pid)
    assert job["result"]["automated_check"]["status"] == "passed", job
    pages = client.get(f"/api/projects/{pid}/boards/a/outputs").json()["manifest"]["pages"]
    assert [p["svg"] for p in pages] == ["led-demo-a.svg", "led-demo-a-LED 출력.svg"]
    svg = client.get(f"/api/projects/{pid}/boards/a/outputs/files/led-demo-a-LED 출력.svg")
    assert svg.status_code == 200 and "<svg" in svg.text
