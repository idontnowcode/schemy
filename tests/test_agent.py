"""Bounded AI EDA agent (Phase 4) with a scripted fake AI: dry-run plan, scope approval, sandboxed tool calls,
code validation, proposal, approval. Gates: no change to the board without approval, recovery on failure,
before/after report, limits, cancel, stale, tool allowlist, path and time limits, masked log."""

import hashlib
import json
import time

import pytest

from backend.agent.gateway import Gateway, RunContext, ScopeItem, ToolError

from .backend_helpers import led_ir, new_project, save_ir
from .test_ai import ai_client, enable

R1_TO_1K = {"op": "set_property", "component_id": "res-001", "field": "value", "expect": "330", "value": "1k"}
PLAN = {"summary": "R1을 1k로 바꿔 LED 전류를 줄입니다.", "steps": ["R1 값 변경", "회로도 생성과 ERC"],
        "scope": [{"op": "set_property", "target": "res-001", "field": "value", "why": "LED 전류 제한"},
                  {"op": "set_property", "target": "ghost-9", "field": "value", "why": "없는 부품"}],
        "risks": ["LED 밝기가 줄어듦"], "questions": []}
GOOD_STEPS = [("read_project", {}), ("propose_ir_patch", {"ops": [R1_TO_1K]}), ("validate_ir", {}),
              ("diff_ir", {}), ("generate_schematic", {"name": "candidate"}), ("run_erc", {"name": "candidate"}),
              ("finish", {"summary": "R1을 1k로 바꿨습니다."})]


def scripted(steps, plan=PLAN):
    def reply(prompt):
        if "회로 변경을 계획하는 EDA 에이전트" in prompt:
            return json.dumps(plan, ensure_ascii=False)
        assert "승인한 범위 안에서만" in prompt
        done = prompt.count("→ <data>")
        todo = steps
        if steps[0][0] == "request_scope_change" and "target=led-001" in prompt:
            todo = steps[1:]  # the scope it asked for has been approved: carry on
        tool, args = todo[min(done, len(todo) - 1)]
        return json.dumps({"thought": f"{done + 1}단계", "tool": tool, "args_json": json.dumps(args)})
    return reply


def setup(tmp_path, steps, plan=PLAN):
    client, created = ai_client(tmp_path, reply=scripted(steps, plan))
    enable(client)
    pid = new_project(client)
    save_ir(client, pid, led_ir())
    return client, pid


def board_hash(tmp_path, pid):
    return hashlib.sha256((tmp_path / "projects" / pid / "boards" / "a" / "circuit.json").read_bytes()).hexdigest()


def run_until(client, pid, rid, states, timeout=60):
    url = f"/api/projects/{pid}/boards/a/agent-runs/{rid}"
    end = time.time() + timeout
    while time.time() < end:
        run = client.get(url).json()
        if run["state"] in states:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run stayed in {run['state']}")


def start(client, pid, request="R1을 1k로 바꿔 LED 전류를 줄여줘", limits=None):
    r = client.post(f"/api/projects/{pid}/boards/a/agent-runs", json={"request": request, **({"limits": limits} if limits else {})})
    assert r.status_code == 202, r.text
    rid = r.json()["run"]["id"]
    client.app.state.jobs.wait(r.json()["job"]["id"], timeout=60)
    return rid


def approve(client, pid, rid, scope=None):
    r = client.post(f"/api/projects/{pid}/boards/a/agent-runs/{rid}/approve-scope", json={"scope": scope})
    assert r.status_code == 202, r.text
    client.app.state.jobs.wait(r.json()["job"]["id"], timeout=120)


def test_led_resistor_change_end_to_end(tmp_path):
    client, pid = setup(tmp_path, GOOD_STEPS)
    before = board_hash(tmp_path, pid)
    rid = start(client, pid)
    run = run_until(client, pid, rid, ["PROPOSED_PLAN"])
    # Dry run: a plan and a scope, nothing executed; the unknown part was dropped from the scope.
    assert [(s["op"], s["target"], s["field"]) for s in run["plan"]["scope"]] == [("set_property", "res-001", "value")]
    assert run["plan"]["dropped_scope"] and run["usage"]["tool_calls"] == 0
    assert board_hash(tmp_path, pid) == before

    approve(client, pid, rid)
    run = run_until(client, pid, rid, ["REVIEW_REQUIRED"])
    assert board_hash(tmp_path, pid) == before  # still nothing on the board
    assert run["validation"]["status"] == "passed_without_kicad"
    assert run["report"]["values"] == [{"id": "res-001", "reference": "R1", "field": "value", "before": "330", "after": "1k"}]
    assert run["report"]["connections"] == []
    tools = [e["tool"] for e in run["log"] if e["type"] == "tool"]
    assert tools == ["read_project", "propose_ir_patch", "validate_ir", "diff_ir", "generate_schematic", "run_erc", "finish"]
    erc = next(e for e in run["log"] if e["type"] == "tool" and e["tool"] == "run_erc")
    assert erc["ok"] is False and erc["error"]["code"] == "KICAD_UNAVAILABLE"
    states = [h["to"] for h in run["history"]]
    assert states == ["PROPOSED_PLAN", "SCOPE_APPROVED", "SANDBOX_EXECUTION", "VALIDATION", "REVIEW_REQUIRED"]
    log_text = (tmp_path / "projects" / pid / "boards" / "a" / "agent_runs" / rid / "log.jsonl").read_text(encoding="utf-8")
    assert "AIza" not in log_text  # the API key never reaches the log
    assert (tmp_path / "projects" / pid / "boards" / "a" / "agent_runs" / rid / "workspace" / "candidate" /
            "candidate.kicad_sch").is_file()

    proposal = client.get(f"/api/projects/{pid}/boards/a/proposals/{run['proposal_id']}").json()
    assert proposal["source"] == "agent" and proposal["agent"]["run_id"] == rid
    r = client.post(f"/api/projects/{pid}/boards/a/proposals/{run['proposal_id']}/approve",
                    json={"accept_failed_checks": True, "generate": False})
    assert r.status_code == 200, r.text
    run = run_until(client, pid, rid, ["COMMITTED"])
    circuit = client.get(f"/api/projects/{pid}/boards/a/circuit").json()["circuit"]
    assert next(c for c in circuit["components"] if c["id"] == "res-001")["value"] == "1k"


def test_scope_is_enforced_and_validation_retries_then_fails(tmp_path):
    out_of_scope = {"op": "set_property", "component_id": "led-001", "field": "value", "expect": "LED", "value": "RED"}
    steps = [("propose_ir_patch", {"ops": [out_of_scope]}), ("finish", {"summary": "끝"})]
    client, pid = setup(tmp_path, steps)
    before = board_hash(tmp_path, pid)
    rid = start(client, pid)
    approve(client, pid, rid)
    run = run_until(client, pid, rid, ["FAILED"])
    refused = next(e for e in run["log"] if e["type"] == "tool" and e["tool"] == "propose_ir_patch")
    assert refused["error"]["code"] == "SCOPE_EXCEEDED"
    # finish without an accepted patch: validation fails, 2 retries, then FAILED; the board never changed.
    assert run["attempts"] == 3 and run["failure"]["code"] == "validation_failed"
    assert [e["attempt"] for e in run["log"] if e["type"] == "validation_failed"] == [1, 2, 3]
    assert board_hash(tmp_path, pid) == before and run["proposal_id"] is None


def test_out_of_scope_need_goes_back_for_approval(tmp_path):
    led_red = {"op": "set_property", "component_id": "led-001", "field": "value", "expect": "LED", "value": "RED"}
    steps = [("request_scope_change", {"items": [{"op": "set_property", "target": "led-001", "field": "value"}],
                                       "why": "LED 색도 바꿔야 함"}),
             ("propose_ir_patch", {"ops": [R1_TO_1K, led_red]}), ("finish", {"summary": "둘 다 바꿈"})]
    client, pid = setup(tmp_path, steps)
    rid = start(client, pid)
    approve(client, pid, rid)
    run = run_until(client, pid, rid, ["PROPOSED_PLAN"])
    assert run["pending_scope"][0]["target"] == "led-001" and "LED 색" in run["plan"]["scope_request"]
    # A scope item that was never offered cannot be approved.
    r = client.post(f"/api/projects/{pid}/boards/a/agent-runs/{rid}/approve-scope",
                    json={"scope": [{"op": "remove_component", "target": "conn-001"}]})
    assert r.status_code == 422
    approve(client, pid, rid)  # all of it, including the requested item
    run = run_until(client, pid, rid, ["REVIEW_REQUIRED"])
    assert {c["reference"] for c in run["report"]["values"]} == {"R1", "D1"}


def test_limits_end_the_run(tmp_path):
    client, pid = setup(tmp_path, [("read_project", {})])  # never finishes
    rid = start(client, pid, limits={"max_llm_calls": 4})
    approve(client, pid, rid)
    run = run_until(client, pid, rid, ["FAILED"])
    assert run["failure"]["code"] == "limit_exceeded" and run["usage"]["llm_calls"] == 4


def test_cancel_and_stale(tmp_path):
    client, pid = setup(tmp_path, GOOD_STEPS)
    rid = start(client, pid)
    run = client.post(f"/api/projects/{pid}/boards/a/agent-runs/{rid}/cancel").json()
    assert run["state"] == "CANCELLED"

    rid2 = start(client, pid)
    approve(client, pid, rid2)
    run = run_until(client, pid, rid2, ["REVIEW_REQUIRED"])
    run = client.post(f"/api/projects/{pid}/boards/a/agent-runs/{rid2}/cancel").json()
    assert run["state"] == "CANCELLED"
    proposal = client.get(f"/api/projects/{pid}/boards/a/proposals/{run['proposal_id']}").json()
    assert proposal["status"] == "rejected"

    rid3 = start(client, pid)
    ir = led_ir()
    ir["title"] = "changed by hand"
    save_ir(client, pid, ir)  # the board moves on
    run = client.get(f"/api/projects/{pid}/boards/a/agent-runs/{rid3}").json()
    assert run["state"] == "STALE"
    r = client.post(f"/api/projects/{pid}/boards/a/agent-runs/{rid3}/approve-scope", json={})
    assert r.status_code == 409
    listed = client.get(f"/api/projects/{pid}/boards/a/agent-runs").json()["runs"]
    assert [r["id"] for r in listed] == [rid3, rid2, rid]


# --- the gateway on its own ----------------------------------------------------------------------------

def gateway(tmp_path, library=lambda: None, scale=1.0):
    from circuit_core import CircuitIR

    ctx = RunContext(project_id="p", base=CircuitIR.model_validate(led_ir()), base_revision=1,
                     scope=[ScopeItem(op="set_property", target="res-001", field="value")], workspace=tmp_path / "ws",
                     library=library, kicad=lambda: None, read_revision=lambda n: CircuitIR.model_validate(led_ir()))
    (tmp_path / "ws").mkdir(parents=True)
    return Gateway(ctx, scale)


def test_gateway_allowlist_arguments_paths_and_time(tmp_path):
    gw = gateway(tmp_path)
    for name, args, code in [("delete_project", {}, "TOOL_NOT_ALLOWED"), ("run_shell", {"cmd": "rm -rf /"}, "TOOL_NOT_ALLOWED"),
                             ("read_revision", {"revision": "x"}, "BAD_ARGS"), ("read_project", {"extra": 1}, "BAD_ARGS"),
                             ("generate_schematic", {"name": "../../etc"}, "PATH_REFUSED"),
                             ("generate_schematic", {"name": "a/b"}, "PATH_REFUSED"),
                             ("diff_ir", {}, "NO_PATCH"), ("run_erc", {"name": "candidate"}, "NOT_GENERATED")]:
        with pytest.raises(ToolError) as err:
            gw.call(name, args)
        assert err.value.code == code, (name, err.value.message)
    # Replacing the whole circuit is never in an agent's scope.
    with pytest.raises(ToolError) as err:
        gw.call("propose_ir_patch", {"ops": [{"op": "replace_circuit", "circuit": led_ir()}]})
    assert err.value.code == "SCOPE_EXCEEDED"
    assert gw.call("propose_ir_patch", {"ops": [R1_TO_1K]})["accepted"] == 1

    def slow_library():
        time.sleep(2)

    slow = gateway(tmp_path / "slow", library=slow_library, scale=0.005)
    with pytest.raises(ToolError) as err:
        slow.call("read_symbol_library", {"symbol_id": "Device:R"})
    assert err.value.code == "TOOL_TIMEOUT"


@pytest.mark.kicad
def test_led_change_with_kicad_reports_netlist_before_and_after(tmp_path, kicad_cli):
    from fastapi.testclient import TestClient

    from backend import Settings, create_app

    from .backend_helpers import TOKEN
    from .conftest import SYMBOL_DIR
    from .test_ai import FakeProvider, MemoryKeyStore

    steps = [*GOOD_STEPS[:6], ("export_netlist", {"name": "candidate"}), ("compare_netlists", {"name": "candidate"}),
             ("finish", {"summary": "R1을 1k로 바꿨고 ERC·Netlist를 확인했습니다."})]
    keystore = MemoryKeyStore()
    settings = Settings(workspace=tmp_path, token=TOKEN, symbol_dirs=[SYMBOL_DIR], kicad_factory=lambda: kicad_cli,
                        keystore=keystore, ai_provider_factory=lambda p, k, m, t: FakeProvider(m, scripted(steps)))
    client = TestClient(create_app(settings))
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    enable(client)
    pid = new_project(client)
    save_ir(client, pid, led_ir())
    rid = start(client, pid)
    approve(client, pid, rid)
    run = run_until(client, pid, rid, ["REVIEW_REQUIRED", "FAILED"], timeout=300)
    assert run["state"] == "REVIEW_REQUIRED", run.get("failure")
    v = run["validation"]
    assert v["status"] == "passed" and v["erc_errors"] == 0 and v["netlist_matches_ir"] is True
    compare = next(e for e in run["log"] if e["type"] == "tool" and e["tool"] == "compare_netlists")
    assert compare["ok"] and compare["result"]["matches_candidate_ir"] is True
    netlist = run["report"]["netlist"]
    assert netlist["values_changed"] == {"R1": {"before": "330", "after": "1k"}}
    assert netlist["nets_changed"] == {}  # only a value changed: connectivity is the same
