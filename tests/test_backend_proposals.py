"""Proposals: one approval path. Create -> isolated check -> approve (new revision) or reject (nothing changes).

Plan v1.4 Phase 2a integration test: LED resistor 330 -> 1k patch -> diff -> approve -> generate -> ERC ->
netlist comparison; rejected or failed proposals leave every board file byte-for-byte unchanged."""

import hashlib

import pytest

from kicad_adapter import KiCadCli

from .backend_helpers import led_ir, make_client, new_project, save_ir
from .circuits import can_node

R330_TO_1K = [{"op": "set_property", "component_id": "res-001", "field": "value", "expect": "330", "value": "1k"}]


def board_hashes(client, pid):
    """Every file of the board except proposals/ (which is where proposals are allowed to write)."""
    folder = client.app.state.store.board_dir(pid, "a")
    return {str(f.relative_to(folder)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(folder.rglob("*")) if f.is_file() and "proposals" not in f.relative_to(folder).parts}


def propose(client, pid, ops=R330_TO_1K, title="R1 330 → 1k"):
    r = client.post(f"/api/projects/{pid}/boards/a/proposals", json={"title": title, "ops": ops,
                                                                    "rationale": "LED 전류를 줄이기 위해"})
    assert r.status_code == 201, r.text
    body = r.json()
    client.app.state.jobs.wait(body["job"]["id"], timeout=300)
    return client.get(f"/api/projects/{pid}/boards/a/proposals/{body['proposal']['id']}").json()


def test_create_check_and_reject_change_nothing(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    before = board_hashes(client, pid)
    p = propose(client, pid)
    assert p["status"] == "ready" and p["base_revision"] == 1 and p["source"] == "user"
    assert p["diff"]["properties_changed"] == [{"id": "res-001", "reference": "R1", "field": "value",
                                                "before": "330", "after": "1k"}]
    assert p["candidate"]["components"][1]["value"] == "1k"
    ev = p["evidence"]
    assert ev["blocking"] is False and ev["automated_check"]["status"] == "not_checked"  # no KiCad in this test
    assert ev["files"] and all(f in ev["files"] for f in ("led-demo-a.kicad_sch", "led-demo-a.kicad_pro"))
    assert board_hashes(client, pid) == before

    r = client.post(f"/api/projects/{pid}/boards/a/proposals/{p['id']}/reject", json={"note": "아직은 아님"})
    assert r.json()["status"] == "rejected"
    assert board_hashes(client, pid) == before
    assert client.post(f"/api/projects/{pid}/boards/a/proposals/{p['id']}/approve", json={}).status_code == 409


def test_approve_needs_passed_checks_or_explicit_acceptance(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    p = propose(client, pid)
    url = f"/api/projects/{pid}/boards/a/proposals/{p['id']}/approve"
    r = client.post(url, json={})
    assert r.status_code == 409 and r.json()["error_code"] == "CHECKS_NOT_PASSED"
    r = client.post(url, json={"accept_failed_checks": True, "generate": False}).json()
    assert r["revision"]["revision"] == 2 and r["revision"]["source"] == "proposal"
    assert r["proposal"]["status"] == "approved" and r["proposal"]["result_revision"] == 2
    assert client.get(f"/api/projects/{pid}/boards/a/circuit").json()["circuit"]["components"][1]["value"] == "1k"


def test_stale_and_invalid_proposals(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    p = propose(client, pid)
    ir = led_ir()
    ir["components"][2]["value"] = "RED"
    save_ir(client, pid, ir)  # the board moved on to revision 2
    got = client.get(f"/api/projects/{pid}/boards/a/proposals/{p['id']}").json()
    assert got["status"] == "stale"
    r = client.post(f"/api/projects/{pid}/boards/a/proposals/{p['id']}/approve", json={"accept_failed_checks": True})
    assert r.status_code == 409 and r.json()["error_code"] == "STALE_PROPOSAL"

    before = board_hashes(client, pid)
    nc = {"op": "set_no_connect", "component_id": "res-001", "pin": "1", "no_connect": True}
    bad = propose(client, pid, ops=[nc])
    assert bad["status"] == "invalid" and bad["evidence"]["blocking"]
    assert "NO_CONNECT_PIN_IN_NET" in {i["error_code"] for i in bad["evidence"]["ir_issues"]}
    assert client.post(f"/api/projects/{pid}/boards/a/proposals/{bad['id']}/approve",
                       json={"accept_failed_checks": True}).json()["error_code"] == "INVALID_PROPOSAL"
    r = client.post(f"/api/projects/{pid}/boards/a/proposals", json={"title": "x", "ops": [
        {"op": "set_property", "component_id": "res-001", "field": "value", "expect": "999", "value": "1"}]})
    assert r.status_code == 422 and r.json()["error_code"] == "PATCH_FAILED"
    assert board_hashes(client, pid) == before


def test_first_design_on_an_empty_board_and_file_serving(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    p = propose(client, pid, ops=[{"op": "replace_circuit", "circuit": can_node().model_dump(mode="json")}],
                title="CAN 노드 첫 설계")
    assert p["base_revision"] is None and p["diff"]["is_new_circuit"]
    assert p["evidence"]["power_flags"] == ["+3V3", "GND"]
    svg_free = [f for f in p["evidence"]["files"] if f.endswith(".kicad_sch")][0]
    assert client.get(f"/api/projects/{pid}/boards/a/proposals/{p['id']}/files/{svg_free}").status_code == 200
    assert client.get(f"/api/projects/{pid}/boards/a/proposals/{p['id']}/files/..%2Fproposal.json").status_code == 404
    lst = client.get(f"/api/projects/{pid}/boards/a/proposals").json()
    assert [x["id"] for x in lst] == [p["id"]]


@pytest.mark.kicad
def test_led_330_to_1k_with_kicad(tmp_path, kicad_cli):
    client = make_client(tmp_path, kicad_factory=KiCadCli.detect)
    pid = new_project(client)
    save_ir(client, pid)
    p = propose(client, pid)
    check = p["evidence"]["automated_check"]
    assert check["status"] == "passed" and check["netlist_matches_ir"] and check["erc_errors"] == 0
    assert any(f.endswith(".svg") for f in p["evidence"]["files"])
    r = client.post(f"/api/projects/{pid}/boards/a/proposals/{p['id']}/approve", json={}).json()
    job = client.app.state.jobs.wait(r["job"]["id"], timeout=300)
    assert job.status == "succeeded", job.error
    outputs = client.get(f"/api/projects/{pid}/boards/a/outputs").json()
    assert outputs["manifest"]["source_revision"] == 2
    assert outputs["manifest"]["automated_check"]["status"] == "passed"
    net = (client.app.state.store.board_dir(pid, "a") / "outputs" / "led-demo-a.net").read_text(encoding="utf-8")
    assert '(value "1k")' in net and '(value "330")' not in net
