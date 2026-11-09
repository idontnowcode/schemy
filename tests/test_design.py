"""AI design (Phase 2b) with a scripted fake AI: plan -> real KiCad symbols -> connections -> repair loop
-> proposal -> isolated check -> review. No real AI is called."""

import json
import re

import pytest

from ai_engine.design import DesignInput, build_circuit, design_circuit
from backend.design_resolver import PartResolver, symbol_index
from kicad_adapter import KiCadCli, generate_schematic

from .backend_helpers import new_project
from .conftest import SYMBOL_DIR
from .test_ai import ai_client, enable, wait

REQUIREMENTS = "3.3V 전원으로 동작하는 CAN 노드. MCU와 UART(TXD/RXD)로 연결. CAN 버스 종단 저항 포함."

PLAN = {
    "title": "CAN 노드 (SN65HVD230)", "summary": "3.3V CAN 트랜시버와 디커플링, 종단 저항.",
    "assumptions": ["버스 끝단 노드라서 120Ω 종단을 넣음"], "questions": ["CAN 커넥터 형태?"],
    "parts": [
        {"ref": "J1", "role": "전원 입력", "category": "connector", "value": "PWR", "pin_count": 2,
         "package": "2.54mm 헤더"},
        {"ref": "J2", "role": "CAN 버스", "category": "connector", "value": "CAN", "pin_count": 2,
         "package": "2.54mm 헤더"},
        {"ref": "J3", "role": "UART", "category": "connector", "value": "UART", "pin_count": 2,
         "package": "2.54mm 헤더"},
        {"ref": "U1", "role": "CAN 트랜시버", "category": "ic", "value": "SN65HVD230", "mpn": "SN65HVD230DR",
         "manufacturer": "Texas Instruments", "search_terms": ["CAN transceiver"]},
        {"ref": "C1", "role": "디커플링", "category": "capacitor", "value": "100nF", "package": "0603"},
        {"ref": "R1", "role": "버스 종단", "category": "resistor", "value": "120", "package": "0603"},
    ],
}
GOOD = {
    "nets": [{"name": "+3V3", "pins": ["J1.1", "U1.3", "C1.1"]},
             {"name": "GND", "pins": ["J1.2", "U1.2", "U1.8", "C1.2"]},
             {"name": "CANH", "pins": ["U1.7", "J2.1", "R1.1"]},
             {"name": "CANL", "pins": ["U1.6", "J2.2", "R1.2"]},
             {"name": "TXD", "pins": ["J3.1", "U1.1"]},
             {"name": "RXD", "pins": ["J3.2", "U1.4"]}],
    "no_connect": ["U1.5"],
    "citations": [{"ref": "U1", "page": 15, "text": "Rs를 GND에 연결하면 고속 모드"}],
}
BAD = {**GOOD, "nets": [*GOOD["nets"][:4], {"name": "TXD", "pins": ["J3.1", "U1.9"]}, GOOD["nets"][5]], "no_connect": []}
REVIEW = {"summary": "요구사항을 충족합니다.", "findings": [
    {"severity": "warning", "title": "ESD 보호 없음", "detail": "CAN 라인에 TVS가 없습니다.", "references": ["J2", "X9"],
     "suggestion": "CANH/CANL에 TVS 다이오드 추가"},
    {"severity": "bogus", "title": "참고", "detail": "Rs를 GND에 직접 연결해 고속 모드입니다.", "references": ["U1"]}]}
SPEC = {"summary": "3.3V CAN 노드", "functions": ["CAN 통신"], "power": [{"name": "입력", "source": "외부", "voltage": "3.3 V"}],
        "interfaces": [{"name": "CAN", "kind": "CAN"}], "parts_preferences": [], "constraints": [], "open_questions": ["버스 속도?"]}


def edit_reply(state: dict) -> str:
    """A revision by edits: R1 gets a new value each time (so every revision really changes the circuit)."""
    state["edits"] = state.get("edits", 0) + 1
    return json.dumps({"summary": "R1 값 조정", "addressed": state.get("asked") or ["F1"], "not_addressed": [],
                       "edits": [{"action": "set_value", "ref": "R1", "value": f"12{state['edits']}", "why": "F1 반영"}]},
                      ensure_ascii=False)


def scripted(bad_first=True):
    state = {"connect": 0}

    def reply(prompt):
        if "회로를 새로 설계하지 마세요" in prompt:
            state["asked"] = re.findall(r"- (F[0-9]+) \[", prompt)
            return edit_reply(state)
        if "부품 목록을 정하세요" in prompt:
            return json.dumps(PLAN, ensure_ascii=False)
        if "핀 표만 써서" in prompt:
            state["connect"] += 1
            first = state["connect"] == 1 and bad_first
            return json.dumps(BAD if first else GOOD, ensure_ascii=False)
        if "검토자" in prompt:
            return json.dumps(REVIEW, ensure_ascii=False)
        if "정해진 항목으로 정리" in prompt:
            return json.dumps(SPEC, ensure_ascii=False)
        raise AssertionError("unexpected prompt: " + prompt[:80])
    return reply


class Scripted:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate(self, *, prompt, attachments=(), json_schema=None, max_output_tokens=8192, temperature=0.0):
        from ai_engine import LlmResult

        self.calls.append({"prompt": prompt, "schema": json_schema})
        return LlmResult(text=self.reply(prompt), model="fake-model", usage={"total_token_count": 10}, finish_reason="STOP")


@pytest.fixture
def resolver(library, tmp_path):
    return PartResolver(library, symbol_index([SYMBOL_DIR], tmp_path / "idx.json"), None, None)


def test_design_pipeline_with_repair(library, resolver):
    ai = Scripted(scripted())
    result = design_circuit(ai, DesignInput(requirements=REQUIREMENTS), resolver, project_id="can")
    assert result.unresolved == [] and result.problems == [] and result.attempts == 2
    repair_prompt = ai.calls[2]["prompt"]
    assert "U1.9: U1에 9번 핀이 없습니다" in repair_prompt and "U1.5 (Vref): 어느 Net에도 없고" in repair_prompt
    c = result.circuit
    u1 = c.component("u1")
    assert u1.symbol_id == "Interface_CAN_LIN:SN65HVD230" and u1.manufacturer_part_number == "SN65HVD230DR"
    assert [p.name for p in u1.pins][:3] == ["D", "GND", "VCC"] and u1.pins[4].no_connect
    assert c.component("r1").footprint_id == "Resistor_SMD:R_0603_1608Metric"
    assert c.component("j1").symbol_id == "Connector_Generic:Conn_01x02"
    assert c.validation.status.value == "unverified"
    assert {p.ref: p.how for p in result.parts}["U1"].startswith("라이브러리 검색")
    generated = generate_schematic(c, library)
    assert generated.power_flags == ["+3V3", "GND"]
    # The pin table the AI saw came from the library, never from the AI.
    assert "7=CANH(bidirectional)" in ai.calls[1]["prompt"]


def test_unknown_part_stops_with_a_reason(resolver):
    plan = {**PLAN, "parts": [*PLAN["parts"], {"ref": "U2", "role": "MCU", "category": "ic", "value": "STM32F103C8T6",
                                                "mpn": "STM32F103C8T6", "kicad_symbol": "MCU_ST_STM32F1:STM32F103C8Tx"}]}
    ai = Scripted(lambda prompt: json.dumps(plan))
    result = design_circuit(ai, DesignInput(requirements=REQUIREMENTS), resolver, project_id="can")
    assert result.circuit is None and len(ai.calls) == 1
    assert result.unresolved[0].startswith("U2 (MCU, STM32F103C8T6): 쓸 수 있는 KiCad 심볼이 없습니다")


def test_build_circuit_reports_every_wiring_problem(resolver):
    parts = [resolver({"ref": "R1", "category": "resistor", "value": "1k"})[0],
             resolver({"ref": "J1", "category": "connector", "value": "X", "pin_count": 2})[0]]
    circuit, problems = build_circuit(parts, {"nets": [{"name": "A", "pins": ["R1.1", "J1.1"]},
                                                       {"name": "B b", "pins": ["R1.1", "Q7.1", "R1"]}],
                                              "no_connect": ["J1.1"]}, project_id="x", title="t", model="m")
    assert problems == [
        "R1.1: 'A'와 'B_b' 두 Net에 들어 있습니다.", "Q7: 부품 목록에 없는 기호입니다.",
        "'R1': 'U1.3'처럼 기호.핀번호로 써야 합니다.", "J1.1: no_connect인데 'A' Net에도 들어 있습니다.",
        "R1.2: 어느 Net에도 없고 no_connect도 아닙니다.", "J1.2 (Pin_2): 어느 Net에도 없고 no_connect도 아닙니다.",
        "NO_CONNECT_PIN_IN_NET: J1 pin 1 is marked no-connect but is in net 'A'. (J1)"]
    assert circuit is not None


# --- API ------------------------------------------------------------------------------------------

def design_client(tmp_path, reply=None):
    client, created = ai_client(tmp_path, reply=reply or scripted())
    enable(client)
    pid = new_project(client, "CAN Node")
    client.put(f"/api/projects/{pid}/requirements", json={"text": REQUIREMENTS})
    return client, created, pid


def test_requirements_sheet(tmp_path):
    client, _, pid = design_client(tmp_path)
    job = wait(client, client.post(f"/api/projects/{pid}/requirements/structure").json())
    assert job["status"] == "succeeded" and job["result"]["spec"]["open_questions"] == ["버스 속도?"]
    assert client.get(f"/api/projects/{pid}/requirements/spec").json()["spec"] is None  # not saved until the user does
    client.put(f"/api/projects/{pid}/requirements/spec", json={"spec": job["result"]["spec"]})
    assert client.get(f"/api/projects/{pid}/requirements/spec").json()["spec"]["summary"] == "3.3V CAN 노드"


def test_design_creates_a_checked_and_reviewed_proposal(tmp_path):
    client, created, pid = design_client(tmp_path)
    board = f"/api/projects/{pid}/boards/a"
    preview = client.get(board + "/ai/design/preview").json()
    assert preview["requirements_chars"] > 10 and preview["enabled"] and preview["key"]
    job = wait(client, client.post(board + "/ai/design", json={}).json())
    assert job["status"] == "succeeded", job
    assert job["result"]["attempts"] == 2 and job["result"]["remaining_problems"] == []
    p = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    assert p["source"] == "ai_design" and p["status"] == "ready" and p["base_revision"] is None
    assert p["diff"]["is_new_circuit"] and p["diff"]["summary"]["components_added"] == 6
    assert p["evidence"]["power_flags"] == ["+3V3", "GND"] and p["evidence"]["blocking"] is False
    ai = p["ai"]
    assert ai["questions"] == ["CAN 커넥터 형태?"] and ai["citations"][0]["page"] == 15
    assert [f["severity"] for f in ai["review"]["findings"]] == ["warning", "info"]
    assert ai["review"]["findings"][0]["references"] == ["J2"]  # unknown X9 dropped
    # Nothing on the board changed until approval.
    assert client.get(board + "/circuit").json()["circuit"] is None
    r = client.post(f"{board}/proposals/{p['id']}/approve", json={"accept_failed_checks": True, "generate": False})
    assert r.json()["revision"]["source"] == "proposal"
    runs = list((tmp_path / "projects" / pid / "boards" / "a" / "ai_runs").glob("*.json"))
    record = json.loads(runs[0].read_text(encoding="utf-8"))
    assert record["kind"] == "ai_design" and record["attempts"] == 2 and "AIza" not in runs[0].read_text()


def test_review_and_revise(tmp_path):
    client, _, pid = design_client(tmp_path)
    board = f"/api/projects/{pid}/boards/a"
    first = wait(client, client.post(board + "/ai/design", json={"review": False}).json())["result"]["proposal_id"]
    assert client.get(f"{board}/proposals/{first}").json()["ai"]["review"] is None
    job = wait(client, client.post(board + "/ai/review", json={"proposal_id": first}).json())
    assert job["status"] == "succeeded"
    assert client.get(f"{board}/proposals/{first}").json()["ai"]["review"]["findings"][0]["title"] == "ESD 보호 없음"
    job = wait(client, client.post(board + "/ai/revise", json={"proposal_id": first}).json())
    assert job["status"] == "succeeded", job
    second = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    assert second["source"] == "ai_revision" and second["ai"]["parent"] == first
    # Board-level review of the saved circuit.
    client.post(f"{board}/proposals/{second['id']}/approve", json={"accept_failed_checks": True, "generate": False})
    job = wait(client, client.post(board + "/ai/review", json={}).json())
    assert job["status"] == "succeeded"
    assert client.get(board + "/ai/review").json()["review"]["revision"] == 1


def test_gates(tmp_path):
    client, _, pid = design_client(tmp_path)
    board = f"/api/projects/{pid}/boards/a"
    client.put("/api/ai/settings", json={"enabled": False})
    assert client.post(board + "/ai/design", json={}).json()["error_code"] == "AI_DISABLED"
    client.put("/api/ai/settings", json={"enabled": True})
    client.put(f"/api/projects/{pid}/requirements", json={"text": ""})
    assert client.post(board + "/ai/design", json={}).json()["error_code"] == "NO_REQUIREMENTS"
    assert client.post(board + "/ai/revise", json={}).json()["error_code"] == "NO_CIRCUIT"


def test_incomplete_design_fails_the_job_with_questions(tmp_path):
    plan = {**PLAN, "parts": [{"ref": "U9", "role": "MCU", "category": "ic", "value": "XYZ123", "mpn": "XYZ123"}]}
    client, _, pid = design_client(tmp_path, reply=lambda prompt: json.dumps(plan))
    job = wait(client, client.post(f"/api/projects/{pid}/boards/a/ai/design", json={}).json())
    assert job["status"] == "failed" and job["error"]["error_code"] == "AI_DESIGN_INCOMPLETE"
    assert job["error"]["details"]["questions"] == ["CAN 커넥터 형태?"]
    assert client.get(f"/api/projects/{pid}/boards/a/proposals").json() == []


@pytest.mark.kicad
def test_ai_designed_circuit_passes_kicad_checks(tmp_path, kicad_cli):
    from fastapi.testclient import TestClient

    from backend import Settings, create_app

    from .test_ai import FakeProvider, MemoryKeyStore

    reply = scripted(bad_first=False)
    settings = Settings(workspace=tmp_path, token=None, symbol_dirs=[SYMBOL_DIR], kicad_factory=KiCadCli.detect,
                        keystore=MemoryKeyStore(), ai_provider_factory=lambda *a: FakeProvider("fake", reply))
    client = TestClient(create_app(settings))
    enable(client)
    pid = new_project(client, "CAN Node")
    client.put(f"/api/projects/{pid}/requirements", json={"text": REQUIREMENTS})
    job = wait(client, client.post(f"/api/projects/{pid}/boards/a/ai/design", json={"review": False}).json())
    p = client.get(f"/api/projects/{pid}/boards/a/proposals/{job['result']['proposal_id']}").json()
    check = p["evidence"]["automated_check"]
    assert check["status"] == "passed" and check["erc_errors"] == 0 and check["netlist_matches_ir"], check


def test_revision_with_chosen_findings_own_request_and_diff(tmp_path):
    client, created, pid = design_client(tmp_path)
    board = f"/api/projects/{pid}/boards/a"
    first = wait(client, client.post(board + "/ai/design", json={}).json())["result"]["proposal_id"]
    preview = client.get(board + "/ai/revise/preview", params={"proposal_id": first}).json()
    assert [(f["index"], f["title"]) for f in preview["findings"]] == [(0, "ESD 보호 없음"), (1, "참고")]
    assert isinstance(preview["checks"], list)

    body = {"proposal_id": first, "finding_indexes": [0], "include_checks": False, "review": False,
            "instructions": "커넥터를 JST-XH로 바꿔 주세요"}
    job = wait(client, client.post(board + "/ai/revise", json=body).json())
    assert job["status"] == "succeeded", job
    second = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    ai = second["ai"]
    assert ai["parent"] == first and ai["instructions"] == "커넥터를 JST-XH로 바꿔 주세요"
    assert [(a["title"], a["source"]) for a in ai["addressed"]] == [("ESD 보호 없음", "ai")]
    # A revision edits the current circuit (R1's value here) instead of designing it again.
    assert ai["method"] == "edits" and ai["changes"] == ["R1 값 120 → 121"]
    assert ai["diff_from_parent"]["summary"]["components_added"] == 0
    calls = [c for provider in created if hasattr(provider, "calls") for c in provider.calls]
    edit_prompt = [c["prompt"] for c in calls if "회로를 새로 설계하지 마세요" in c["prompt"]][-1]
    assert "F1 [warning] ESD 보호 없음" in edit_prompt and "JST-XH" in edit_prompt and "[현재 회로]" in edit_prompt
    assert re.search(r"- U1 SN65HVD230( 품번 \S+)? \[Interface_CAN_LIN:SN65HVD230\] 핀: 1=", edit_prompt)  # real pins
    assert sum("부품 목록을 정하세요" in c["prompt"] for c in calls) == 1  # no second parts plan


def test_revision_from_own_request_alone_and_nothing_to_do(tmp_path):
    client, _, pid = design_client(tmp_path)
    board = f"/api/projects/{pid}/boards/a"
    first = wait(client, client.post(board + "/ai/design", json={"review": False}).json())["result"]["proposal_id"]
    r = client.post(board + "/ai/revise", json={"proposal_id": first, "include_checks": False})
    assert r.status_code == 409 and r.json()["error_code"] == "NOTHING_TO_REVISE"
    job = wait(client, client.post(board + "/ai/revise", json={
        "proposal_id": first, "include_checks": False, "review": False, "instructions": "LED 상태 표시 추가"}).json())
    assert job["status"] == "succeeded"


def test_check_problems_become_revision_inputs():
    from backend.proposals import Proposal, ProposalEvidence
    from backend.routes_design import proposal_check_findings

    ev = ProposalEvidence(checked_at="t", ir_issues=[{"severity": "warning", "error_code": "UNCONNECTED_PIN",
                                                      "message": "U1 pin 5 is not connected."}],
                          datasheets=[{"reference": "U1", "reviewed": False, "pin_map": "match", "ratings": "warning",
                                       "external": "warning", "external_missing": ["저항 120Ω (CANH ~ CANL)"],
                                       "ratings_problems": ["VCC +5V 5.0 V: outside_recommended"]}])
    p = Proposal(id="0001", project_id="p", board_id="a", title="t", source="ai_design", base_revision=None, ops=[],
                 status="ready", created_at="t", updated_at="t", diff={}, evidence=ev,
                 ai={"remaining_problems": ["U1.5 (Vref): 어느 Net에도 없고 no_connect도 아닙니다."]})
    titles = [f["title"] for f in proposal_check_findings(p)]
    assert titles == ["회로 규칙: UNCONNECTED_PIN", "데이터시트 전압 점검 (U1)", "데이터시트 외부 부품 (U1)",
                      "AI 연결 검사에서 남은 문제"]


def test_stacked_pins_join_the_net_of_the_visible_pin():
    """An MCU's extra GND pins are stacked under the visible one: connecting pin 3 is enough."""
    from ai_engine.design import ResolvedPart

    mcu = ResolvedPart(ref="U1", role="mcu", category="ic", value="ATmega328P-A",
                       symbol_id="MCU_Microchip_ATmega:ATmega328P-A",
                       pins=[("3", "GND", "power_in"), ("5", "GND", "passive"), ("21", "GND", "passive"),
                             ("4", "VCC", "power_in")], how="", stacks=[("3", "5", "21")])
    conn = ResolvedPart(ref="J1", role="power", category="connector", value="PWR",
                        symbol_id="Connector_Generic:Conn_01x02", pins=[("1", "Pin_1", "passive"), ("2", "Pin_2", "passive")],
                        how="")
    circuit, problems = build_circuit([mcu, conn], {"nets": [{"name": "GND", "pins": ["U1.3", "J1.2"]},
                                                             {"name": "+5V", "pins": ["U1.4", "J1.1"]}]},
                                      project_id="p", title="t", model="m")
    assert problems == []
    gnd = next(n for n in circuit.nets if n.name == "GND")
    assert {(n.component_id, n.pin) for n in gnd.nodes} == {("u1", "3"), ("u1", "5"), ("u1", "21"), ("j1", "2")}
    _, problems = build_circuit([mcu, conn], {"nets": [{"name": "GND", "pins": ["U1.3", "J1.2"]},
                                                       {"name": "+5V", "pins": ["U1.4", "U1.21", "J1.1"]}]},
                                project_id="p", title="t", model="m")
    assert any("겹친 핀" in p for p in problems)


TEMPLATE_PLAN = {
    "title": "CAN 노드 (템플릿)", "summary": "검토된 CAN 템플릿에 커넥터만 붙임.", "assumptions": [], "questions": [],
    "parts": [
        {"ref": "J1", "role": "전원 입력", "category": "connector", "value": "PWR", "pin_count": 2},
        {"ref": "J2", "role": "CAN 버스", "category": "connector", "value": "CAN", "pin_count": 2},
        {"ref": "J3", "role": "UART", "category": "connector", "value": "UART", "pin_count": 2},
    ],
    "templates": [{"id": "can-sn65hvd230", "params": [{"name": "vcc", "value": "+3V3"}], "why": "검토된 회로"}],
}
TEMPLATE_CONNECT = {
    "nets": [{"name": "+3V3", "pins": ["J1.1"]}, {"name": "GND", "pins": ["J1.2"]},
             {"name": "CANH", "pins": ["J2.1"]}, {"name": "CANL", "pins": ["J2.2"]},
             {"name": "TXD", "pins": ["J3.1"]}, {"name": "RXD", "pins": ["J3.2"]}],
    "no_connect": [],
}


def test_design_uses_a_template_and_marks_where_parts_came_from(library, resolver):
    calls = {"connect": 0}

    def reply(prompt):
        if "부품 목록을 정하세요" in prompt:
            assert "can-sn65hvd230" in prompt  # the catalog is offered
            return json.dumps(TEMPLATE_PLAN, ensure_ascii=False)
        calls["connect"] += 1
        assert "이미 연결이 끝난 블록" in prompt and "U1 SN65HVD230" in prompt
        if calls["connect"] == 1:  # the AI first tries to wire a template pin itself
            return json.dumps({**TEMPLATE_CONNECT, "nets": [*TEMPLATE_CONNECT["nets"][:5],
                                                            {"name": "RXD", "pins": ["J3.2", "U1.4"]}]})
        return json.dumps(TEMPLATE_CONNECT)

    pins_of = lambda s: [(p.number, p.name) for p in library.get(s).pins]  # noqa: E731
    result = design_circuit(Scripted(reply), DesignInput(requirements=REQUIREMENTS), resolver, project_id="can",
                            pins_of=pins_of)
    assert result.problems == [] and result.attempts == 2
    c = result.circuit
    origins = {comp.reference: comp.origin for comp in c.components}
    assert origins["J1"] == "ai" and origins["U1"] == origins["C1"] == origins["R1"] == "template:can-sn65hvd230@1"
    nets = {n.name: {(x.component_id, x.pin) for x in n.nodes} for n in c.nets}
    assert ("j1", "1") in nets["+3V3"] and ("can-u", "3") in nets["+3V3"]
    assert [b.ref for b in result.templates] == ["can-sn65hvd230@1"]
    assert generate_schematic(c, library).power_flags == ["+3V3", "GND"]


def test_rails_carry_a_consistent_voltage_range():
    from ai_engine.design import _apply_rails

    nets = [{"name": "VIN"}, {"name": "+5V"}, {"name": "+3V3"}]
    _apply_rails({"rails": [{"net": "VIN", "voltage": 5, "load_ma": 0, "voltage_min": 4.75, "voltage_max": 5.25},
                            {"net": "+5V", "voltage": 0, "load_ma": 0, "voltage_min": 4.5, "voltage_max": 5.5},
                            {"net": "+3V3", "voltage": 0, "load_ma": 30, "voltage_min": 4.0, "voltage_max": 5.0}]}, nets)
    vin, v5, v33 = nets
    assert (vin["voltage_min"], vin["voltage_max"]) == (4.75, 5.25)
    assert (v5["voltage_min"], v5["voltage_max"]) == (4.5, 5.5)  # nominal from the name
    assert "voltage_min" not in v33 and v33["load_current"] == 0.03  # 4..5 V does not contain 3.3 V: dropped


def test_resolver_uses_named_symbols_for_diodes_connectors_and_fuses(resolver):
    """Practical parts the generic symbols cannot draw: a TVS named by part number, a three-pin dual diode, a USB-C
    receptacle without a pin count, a PTC fuse; each gets a footprint from its package."""
    tvs, _ = resolver({"ref": "D1", "category": "diode", "value": "SMAJ5.0A", "mpn": "SMAJ5.0A", "package": "SMA"})
    assert (tvs.symbol_id, tvs.footprint_id) == ("Diode:SMAJ5.0A", "Diode_SMD:D_SMA")
    dual, _ = resolver({"ref": "D2", "category": "diode", "value": "BAT54S", "kicad_symbol": "Diode:BAT54S"})
    assert dual.symbol_id == "Diode:BAT54S" and len(dual.pins) == 3
    usb, why = resolver({"ref": "J1", "category": "connector", "value": "USB_C", "package": "GCT USB4125 6P",
                         "kicad_symbol": "Connector:USB_C_Receptacle_PowerOnly_6P"})
    assert usb.symbol_id == "Connector:USB_C_Receptacle_PowerOnly_6P", why
    assert usb.footprint_id.startswith("Connector_USB:USB_C_Receptacle_GCT_USB4125")
    ptc, _ = resolver({"ref": "F1", "category": "fuse", "value": "PTC 500mA", "package": "1206"})
    assert (ptc.symbol_id, ptc.footprint_id) == ("Device:Polyfuse", "Fuse:Fuse_1206_3216Metric")
    header, _ = resolver({"ref": "J2", "category": "connector", "value": "OUT", "pin_count": 3, "package": "2.54mm 헤더"})
    assert header.footprint_id == "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
    nothing, why = resolver({"ref": "J3", "category": "connector", "value": "X"})
    assert nothing is None and "pin_count" in why
    plain, _ = resolver({"ref": "R1", "category": "resistor", "value": "10k"})
    assert plain.footprint_id is None  # no package: no guess from the generic symbol


def test_one_sided_rail_ranges_are_kept():
    """An input that is 10 V normally but must survive 24 V has only a maximum; it is not dropped."""
    from ai_engine.design import _apply_rails

    nets = [{"name": "AIN"}, {"name": "X"}]
    _apply_rails({"rails": [{"net": "AIN", "voltage": 10, "load_ma": 0, "voltage_max": 24},
                            {"net": "X", "voltage": 5, "load_ma": 0, "voltage_min": 4.5}]}, nets)
    assert nets[0] == {"name": "AIN", "voltage": 10.0, "voltage_max": 24.0}
    assert nets[1] == {"name": "X", "voltage": 5.0, "voltage_min": 4.5}


# --- step screens: design progress and the automatic review loop -----------------------------------------------------

def finding(severity, title, basis="circuit", evidence="U1.1 TXD"):
    """A review finding; by default it points at a real pin, so its basis checks out."""
    return {"severity": severity, "title": title, "detail": f"{title} 설명", "references": ["U1"], "suggestion": None,
            "basis": basis, "evidence": evidence}


def reviews_in_turn(*reviews):
    """A scripted AI whose reviews come from ``reviews`` in order (the last one repeats)."""
    base, state = scripted(bad_first=False), {"n": 0}

    def reply(prompt):
        if "회로를 새로 설계하지 마세요" in prompt:
            return base(prompt)
        if "검토자" in prompt and "부품 목록을 정하세요" not in prompt and "핀 표만 써서" not in prompt:
            state["n"] += 1
            findings = reviews[min(state["n"], len(reviews)) - 1]
            return json.dumps({"summary": f"검토 {state['n']}", "findings": findings}, ensure_ascii=False)
        return base(prompt)
    return reply, state


def designed(tmp_path, reply):
    client, _, pid = design_client(tmp_path, reply)
    board = f"/api/projects/{pid}/boards/a"
    job = wait(client, client.post(board + "/ai/design", json={"review": False}).json())
    return client, board, job


def test_design_job_reports_its_steps(tmp_path):
    client, board, job = designed(tmp_path, scripted())
    steps = [s["step"] for s in job["progress"]]
    assert steps[0] == "parts" and "pins" in steps and "check" in steps and steps[-1] == "proposal"
    assert steps.index("parts") < steps.index("pins") < steps.index("check") < steps.index("proposal")
    texts = [s["text"] for s in job["progress"]]
    assert "부품 6개 선택" in texts and "연결 문제 3개" in texts and "연결 완료: Net 6개, 1회 고침" in texts
    assert all(s["at"] for s in job["progress"])
    # The job list keeps the steps too (the step screen reloads them after a restart).
    assert client.get(board + "/jobs").json()[0]["progress"] == job["progress"]


def test_auto_review_stops_at_once_when_the_condition_holds(tmp_path):
    reply, state = reviews_in_turn([finding("warning", "ESD 보호 없음"), finding("info", "참고")])
    client, board, job = designed(tmp_path, reply)
    pid = job["result"]["proposal_id"]
    auto = wait(client, client.post(board + "/ai/auto-review", json={"proposal_id": pid, "max_warnings": 1}).json())
    assert auto["status"] == "succeeded", auto
    r = auto["result"]
    assert r["stop"] == "met" and r["met"] and r["proposal_id"] == pid and state["n"] == 1
    assert r["rounds"] == [{"round": 1, "proposal_id": pid, "errors": 0, "warnings": 1, "check": r["rounds"][0]["check"],
                            "check_failed": False, "said": ["ESD 보호 없음"], "unverified": 0, "unverified_said": []}]
    assert client.get(f"{board}/proposals/{pid}").json()["ai"]["review"]["summary"] == "검토 1"
    assert auto["progress"][-1]["text"] == "멈춤: 조건 충족"


def test_auto_review_revises_until_the_condition_holds(tmp_path):
    reply, state = reviews_in_turn([finding("error", "모터 구동 회로 누락"), finding("warning", "LED 저항 발열")],
                                   [finding("warning", "VMOT 벌크 권장"), finding("warning", "ENABLE 풀업 권장")],
                                   [finding("warning", "12 V 입력에 TVS 권장")])
    client, board, job = designed(tmp_path, reply)
    first = job["result"]["proposal_id"]
    auto = wait(client, client.post(board + "/ai/auto-review", json={"proposal_id": first, "max_warnings": 1,
                                                                     "max_rounds": 3}).json())
    r = auto["result"]
    assert auto["status"] == "succeeded" and r["stop"] == "met", auto
    assert [(x["errors"], x["warnings"]) for x in r["rounds"]] == [(1, 1), (0, 2), (0, 1)]
    second, third = r["rounds"][1]["proposal_id"], r["rounds"][2]["proposal_id"]
    assert r["rounds"][0]["revised_to"] == second and r["rounds"][1]["revised_to"] == third and r["proposal_id"] == third
    assert r["rounds"][0]["said"] == ["모터 구동 회로 누락", "LED 저항 발열"]
    p3 = client.get(f"{board}/proposals/{third}").json()
    assert p3["source"] == "ai_revision" and p3["ai"]["parent"] == second
    assert [a["title"] for a in client.get(f"{board}/proposals/{second}").json()["ai"]["addressed"]] == [
        "모터 구동 회로 누락", "LED 저항 발열"]
    # Every round is its own proposal; the board itself is untouched until someone approves one.
    assert client.get(board + "/circuit").json()["circuit"] is None
    rounds = [s for s in auto["progress"] if s["step"] == "round"]
    assert [s["data"]["round"] for s in rounds] == [1, 2, 3]
    assert any(s["step"] == "pins" for s in auto["progress"])  # the revisions' design steps are logged too


def test_auto_review_stops_when_a_finding_comes_back(tmp_path):
    reply, _ = reviews_in_turn([finding("error", "역극성 보호 없음")], [finding("error", "역극성  보호 없음")])
    client, board, job = designed(tmp_path, reply)
    auto = wait(client, client.post(board + "/ai/auto-review", json={"proposal_id": job["result"]["proposal_id"],
                                                                     "max_rounds": 5}).json())
    r = auto["result"]
    assert r["stop"] == "repeated" and len(r["rounds"]) == 2 and r["rounds"][1]["repeated"] == ["역극성  보호 없음"]
    assert auto["progress"][-1]["text"] == "멈춤: 같은 의견이 다시 나옴"


def test_auto_review_limits_and_gates(tmp_path):
    reply, state = reviews_in_turn([finding("error", "결함")])
    client, board, job = designed(tmp_path, reply)
    pid = job["result"]["proposal_id"]
    r = wait(client, client.post(board + "/ai/auto-review", json={"proposal_id": pid, "max_rounds": 1}).json())["result"]
    assert r["stop"] == "max_rounds" and len(r["rounds"]) == 1 and "revised_to" not in r["rounds"][0]
    assert client.post(board + "/ai/auto-review", json={"proposal_id": pid, "max_rounds": 9}).status_code == 422
    client.post(f"{board}/proposals/{pid}/reject", json={"note": "다시"})
    assert client.post(board + "/ai/auto-review", json={"proposal_id": pid}).json()["error_code"] == "PROPOSAL_CLOSED"


# --- a part with no KiCad symbol: choose a way forward, then continue -----------------------------------------------

MISSING = {**PLAN, "parts": [*PLAN["parts"][:3], {"ref": "U1", "role": "CAN 트랜시버", "category": "ic", "value": "XYZ999",
                                                  "mpn": "XYZ999-Q1"}, *PLAN["parts"][4:]]}
ALTERNATIVES = {"options": [
    {"ref": "U1", "kind": "replace", "title": "SN65HVD230으로 대체", "detail": "3.3 V CAN 트랜시버, 핀 배치 동일 계열",
     "part": {"value": "SN65HVD230", "mpn": "SN65HVD230DR", "manufacturer": "Texas Instruments"}},
    {"ref": "U1", "kind": "replace", "title": "없는 부품", "detail": "지어낸 품번", "part": {"mpn": "ABC123ZZ"}},
    {"ref": "U1", "kind": "approach", "title": "CAN 대신 UART만 쓰기", "detail": "요구사항에서 CAN을 빼고 UART 헤더만 둠"},
    {"ref": "U1", "kind": "approach", "title": "CAN 모듈 헤더", "detail": "시판 CAN 모듈을 4핀 헤더로 연결",
     "part": {"category": "connector", "pin_count": 4, "value": "CAN 모듈"}},
    {"ref": "Q7", "kind": "replace", "title": "다른 부품", "detail": "없는 기호", "part": {"mpn": "SN65HVD230DR"}},
]}


def missing_part_ai(state):
    def reply(prompt):
        if "대안을 2~4개 제시하세요" in prompt:
            state["alternatives"] = state.get("alternatives", 0) + 1
            return json.dumps(ALTERNATIVES, ensure_ascii=False)
        if "부품 목록을 정하세요" in prompt:
            state.setdefault("plans", []).append(prompt)
            return json.dumps(MISSING if len(state["plans"]) == 1 else PLAN, ensure_ascii=False)
        return scripted(bad_first=False)(prompt)
    return reply


def stopped_at_missing_part(tmp_path):
    state: dict = {}
    client, _, pid = design_client(tmp_path, missing_part_ai(state))
    board = f"/api/projects/{pid}/boards/a"
    job = wait(client, client.post(board + "/ai/design", json={"instructions": "커넥터는 JST-XH"}).json())
    return client, pid, board, job, state


def test_a_missing_part_stops_with_checked_choices_not_a_dead_end(tmp_path):
    client, pid, board, job, state = stopped_at_missing_part(tmp_path)
    assert job["status"] == "failed" and job["error"]["error_code"] == "AI_DESIGN_INCOMPLETE"
    assert "진행할 방법을 고르면 이어서 설계" in job["error"]["message"]
    details = job["error"]["details"]
    assert details["plan"]["parts"][3]["value"] == "XYZ999" and details["instructions"] == "커넥터는 JST-XH"
    [choice] = details["choices"]
    assert (choice["ref"], choice["role"], choice["mpn"]) == ("U1", "CAN 트랜시버", "XYZ999-Q1")
    options = choice["options"]
    by_title = {o["title"]: o for o in options}
    # The AI's replacement is checked against the library; an invented part number is not offered at all.
    assert by_title["SN65HVD230으로 대체"]["executable"] and "SN65HVD230" in by_title["SN65HVD230으로 대체"]["symbol_id"]
    assert "없는 부품" not in by_title
    # A direction in words is kept (it starts a new design); the module header is checked like a part.
    assert not by_title["CAN 대신 UART만 쓰기"]["executable"] and by_title["CAN 대신 UART만 쓰기"]["part"] is None
    assert by_title["CAN 모듈 헤더"]["symbol_id"] == "Connector_Generic:Conn_01x04"
    assert {o["source"] for o in options} >= {"ai"} and len({o["id"] for o in options}) == len(options)
    assert state["alternatives"] == 1 and len(state["plans"]) == 1
    assert job["progress"][-1]["text"].startswith("대안 ")


def test_resume_with_a_replacement_continues_from_the_saved_plan(tmp_path):
    client, pid, board, job, state = stopped_at_missing_part(tmp_path)
    [choice] = job["error"]["details"]["choices"]
    pick = next(o for o in choice["options"] if o["title"] == "SN65HVD230으로 대체")
    question = job["error"]["details"]["questions"][0]
    r = client.post(board + "/ai/design/resume", json={"job_id": job["id"], "choices": [{"ref": "U1", "option_id": pick["id"]}],
                                                       "answers": [{"question": question, "answer": "2핀 터미널"}]})
    assert r.status_code == 202, r.json()
    done = wait(client, r.json())
    assert done["status"] == "succeeded", done
    assert len(state["plans"]) == 1  # no second parts plan: the design continued from the saved one
    assert done["progress"][0]["text"] == "고른 대안으로 부품 계획을 고쳐 이어서 설계"
    p = client.get(f"{board}/proposals/{done['result']['proposal_id']}").json()
    u1 = next(c for c in p["candidate"]["components"] if c["reference"] == "U1")
    assert "SN65HVD230" in u1["symbol_id"] and u1["manufacturer_part_number"] == "SN65HVD230DR"
    decision = next(d for d in p["candidate"]["decisions"] if "U1" in json.dumps(d, ensure_ascii=False))
    assert decision["by"] == "changed" and "XYZ999" in json.dumps(decision, ensure_ascii=False)
    req = client.get(f"/api/projects/{pid}/requirements").json()["text"]
    assert "## AI 질문에 대한 답" in req and "답: 2핀 터미널" in req


def test_resume_with_own_direction_starts_a_new_design_with_it(tmp_path):
    client, pid, board, job, state = stopped_at_missing_part(tmp_path)
    r = client.post(board + "/ai/design/resume", json={"job_id": job["id"],
                                                       "choices": [{"ref": "U1", "text": "TI 트랜시버 중 3.3 V 제품으로"}]})
    done = wait(client, r.json())
    assert done["status"] == "succeeded", done
    assert len(state["plans"]) == 2 and "TI 트랜시버 중 3.3 V 제품으로" in state["plans"][1]
    assert "커넥터는 JST-XH" in state["plans"][1]  # the original request is kept


def test_resume_gates(tmp_path):
    client, pid, board, job, _ = stopped_at_missing_part(tmp_path)
    r = client.post(board + "/ai/design/resume", json={"job_id": job["id"], "choices": []})
    assert r.status_code == 409 and r.json()["error_code"] == "CHOICE_MISSING"
    done = wait(client, client.post(board + "/ai/design/resume", json={
        "job_id": job["id"], "choices": [{"ref": "U1", "text": "TI 제품으로"}]}).json())
    r = client.post(board + "/ai/design/resume", json={"job_id": done["id"], "choices": []})
    assert r.status_code == 409 and r.json()["error_code"] == "NOT_RESUMABLE"


# --- an unreadable AI answer: say why, ask once more, keep the evidence ----------------------------------------------

def test_unreadable_answers_say_why():
    from ai_engine import AiError, LlmResult
    from ai_engine.design import _json

    def res(text, finish="STOP"):
        return LlmResult(text=text, model="m", usage={}, finish_reason=finish)

    assert _json(res('연결입니다:\n```json\n{"nets": []}\n```\n끝'), "연결") == {"nets": []}
    assert _json(res('다음과 같습니다 {"nets": [], "no_connect": []} 이상'), "연결")["no_connect"] == []
    with pytest.raises(AiError) as cut:
        _json(res('{"nets": [{"name": "GND", "pins": ["U1.', "MAX_TOKENS"), "연결")
    assert "출력 길이 제한" in cut.value.message and cut.value.details["finish_reason"] == "MAX_TOKENS"
    assert cut.value.details["tail"].endswith('"U1.') and cut.value.details["chars"] == 39
    with pytest.raises(AiError) as blocked:
        _json(res("", "RECITATION"), "연결")
    assert "막았습니다" in blocked.value.message and "원문 인용" in blocked.value.message
    with pytest.raises(AiError) as empty:
        _json(res(""), "검토")
    assert "비어 있습니다" in empty.value.message
    with pytest.raises(AiError) as prose:
        _json(res("죄송하지만 연결을 정할 수 없습니다."), "연결")
    assert "JSON이 아닙니다" in prose.value.message and "종료 이유: STOP" in prose.value.message


def unreadable_connections(times):
    state = {"connect": 0, "prompts": []}

    def reply(prompt):
        if "핀 표만 써서" in prompt and "부품 목록을 정하세요" not in prompt:
            state["connect"] += 1
            state["prompts"].append(prompt)
            if state["connect"] <= times:
                return '{"nets": [{"name": "+3V3", "pins": ["J1.1", "U1.'  # cut off mid-answer
        return scripted(bad_first=False)(prompt)
    return reply, state


def test_an_unreadable_answer_is_asked_again_once(tmp_path):
    reply, state = unreadable_connections(1)
    client, board, job = designed(tmp_path, reply)
    assert job["status"] == "succeeded", job
    assert state["connect"] == 2 and "[다시 요청]" in state["prompts"][1] and "[다시 요청]" not in state["prompts"][0]
    assert any("한 번 더 요청" in s["text"] for s in job["progress"])


def test_when_it_stays_unreadable_the_reason_and_answer_tail_are_kept(tmp_path):
    reply, state = unreadable_connections(5)
    client, board, job = designed(tmp_path, reply)
    assert job["status"] == "failed" and job["error"]["error_code"] == "AI_BAD_OUTPUT" and state["connect"] == 2
    assert "AI 응답(연결)이 JSON이 아닙니다" in job["error"]["message"]
    assert job["error"]["details"]["tail"].endswith('"U1.') and job["error"]["details"]["finish_reason"] == "STOP"
    pid = board.split("/")[3]
    runs = list((tmp_path / "projects" / pid / "boards" / "a" / "ai_runs").glob("*.json"))
    record = json.loads(runs[0].read_text(encoding="utf-8"))
    assert record["status"] == "failed" and record["error"]["details"]["what"] == "연결"


def test_auto_review_reports_why_a_revision_failed(tmp_path):
    base, rstate = reviews_in_turn([finding("error", "결함")])
    state = {"connect": 0}

    def reply(prompt):
        if "회로를 새로 설계하지 마세요" in prompt:
            return '{"edits": ['  # the edits never come back readable either: the revision falls back to a redesign
        if "핀 표만 써서" in prompt and "부품 목록을 정하세요" not in prompt:
            state["connect"] += 1
            if state["connect"] > 1:  # the revision's connections never come back readable
                return '{"nets": ['
        return base(prompt)
    client, board, job = designed(tmp_path, reply)
    auto = wait(client, client.post(board + "/ai/auto-review", json={"proposal_id": job["result"]["proposal_id"]}).json())
    r = auto["result"]
    assert r["stop"] == "revise_failed" and "AI 응답(연결)이 JSON이 아닙니다" in r["message"]
    assert r["why"]["what"] == "연결" and r["why"]["tail"] == '{"nets": ['


# --- grounded reviews: a finding needs a basis the app can check ------------------------------------------------------

def test_review_findings_must_point_at_something_real(tmp_path):
    from ai_engine.design import review_circuit

    findings = [
        finding("error", "자동 검사 근거", basis="evidence", evidence="E1 참고"),
        finding("error", "없는 근거 번호", basis="evidence", evidence="E99"),
        finding("warning", "실제 핀", basis="circuit", evidence="U1.7(CANH)이 R1과 연결"),
        finding("warning", "없는 핀", basis="circuit", evidence="U7.3이 떠 있음"),
        finding("warning", "Net 이름", basis="circuit", evidence="CANL 라인에 보호 없음"),
        finding("warning", "요구사항 인용", basis="requirement", evidence="CAN 버스 종단 저항 포함"),
        finding("warning", "지어낸 요구사항", basis="requirement", evidence="방수 등급 IP67"),
        finding("warning", "그냥 권고", basis="judgment", evidence=""),
        finding("info", "참고", basis="judgment", evidence=""),
    ]
    provider = Scripted(lambda prompt: json.dumps({"summary": "s", "findings": findings}, ensure_ascii=False))
    from circuit_core import CircuitIR

    client, _, pid = design_client(tmp_path)
    board = f"/api/projects/{pid}/boards/a"
    job = wait(client, client.post(board + "/ai/design", json={"review": False}).json())
    circuit = CircuitIR.model_validate(client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()["candidate"])
    review, _ = review_circuit(provider, requirements=REQUIREMENTS, spec=None, circuit=circuit,
                               evidence="KiCad 자동 검사: passed\n- IR warning: X 무엇", datasheet_notes="")
    verdict = {f["title"]: f["verified"] for f in review["findings"]}
    assert verdict == {"자동 검사 근거": True, "없는 근거 번호": False, "실제 핀": True, "없는 핀": False, "Net 이름": True,
                       "요구사항 인용": True, "지어낸 요구사항": False, "그냥 권고": False, "참고": None}
    assert [e["id"] for e in review["evidence"]][:2] == ["E1", "E2"]
    prompt = provider.calls[0]["prompt"]
    assert "E1 KiCad 자동 검사: passed" in prompt and "개수를 채우려고" in prompt and "항목을 통과했고" in prompt
    # Unverified ones are listed after the verified ones of the same severity.
    order = [(f["severity"], f["verified"]) for f in review["findings"]]
    assert order.index(("error", True)) < order.index(("error", False))


def test_unverified_findings_do_not_drive_the_loop(tmp_path):
    reply, state = reviews_in_turn([finding("error", "느낌상 위험", basis="judgment", evidence=""),
                                    finding("warning", "없는 핀 지적", basis="circuit", evidence="U9.9")])
    client, board, job = designed(tmp_path, reply)
    auto = wait(client, client.post(board + "/ai/auto-review", json={"proposal_id": job["result"]["proposal_id"],
                                                                     "max_warnings": 0}).json())
    [r1] = auto["result"]["rounds"]
    assert auto["result"]["stop"] == "met" and (r1["errors"], r1["warnings"], r1["unverified"]) == (0, 0, 2)
    assert r1["unverified_said"] == ["느낌상 위험", "없는 핀 지적"] and state["n"] == 1


def test_the_loop_stops_when_nothing_gets_better(tmp_path):
    reply, _ = reviews_in_turn([finding("error", "모터 전원 디커플링 부족")],
                               [finding("error", "게이트 저항 없음")])
    client, board, job = designed(tmp_path, reply)
    auto = wait(client, client.post(board + "/ai/auto-review", json={"proposal_id": job["result"]["proposal_id"],
                                                                     "max_rounds": 5}).json())
    r = auto["result"]
    assert r["stop"] in ("no_progress", "no_change") and len(r["rounds"]) <= 2


def test_similar_titles_count_as_the_same_finding():
    from backend.routes_design import similar

    assert similar("역극성 보호 없음", "역극성 보호 회로가 없음")
    assert similar("모터 전원 벌크 콘덴서 부족", "모터 전원의 벌크 콘덴서가 부족함")
    assert not similar("역극성 보호 없음", "I2C 풀업 저항 값 확인")


# --- revisions edit the current circuit ---------------------------------------------------------------------------------

def can_candidate(tmp_path):
    from circuit_core import CircuitIR

    client, _, pid = design_client(tmp_path)
    board = f"/api/projects/{pid}/boards/a"
    job = wait(client, client.post(board + "/ai/design", json={"review": False}).json())
    return CircuitIR.model_validate(client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()["candidate"])


def test_edits_change_only_what_they_name(tmp_path, resolver):
    from ai_engine.design import apply_edits

    base = can_candidate(tmp_path)
    edits = [{"action": "set_value", "ref": "R1", "value": "121", "why": "F1"},
             {"action": "add_part", "ref": "D1", "category": "diode", "value": "TVS", "role": "CANH 보호", "why": "F2"},
             {"action": "connect", "net": "CANH", "pins": ["D1.1"], "why": "F2"},
             {"action": "connect", "net": "GND", "pins": ["D1.2"], "why": "F2"}]
    circuit, problems, changes, added = apply_edits(base, edits, resolver)
    assert problems == [] and circuit is not None, problems
    assert changes == ["R1 값 120 → 121", "D1 TVS 추가", "D1.1 → CANH", "D1.2 → GND"] and list(added) == ["D1"]
    nets = {n.name: {(x.component_id, x.pin) for x in n.nodes} for n in circuit.nets}
    assert ("d1", "1") in nets["CANH"] and ("d1", "2") in nets["GND"]
    before = {c.reference: c for c in base.components}
    assert all(c.model_dump() == before[c.reference].model_dump() for c in circuit.components if c.reference not in ("R1", "D1"))
    assert any(d.id == "dec-d1" and d.by == "ai" and d.reason == "F2" for d in circuit.decisions)
    # A wrong pin, a part left floating and a pin pulled off its net are all reported, not half-applied.
    bad = [{"action": "add_part", "ref": "C9", "category": "capacitor", "value": "10uF", "why": "x"},
           {"action": "connect", "net": "+3V3", "pins": ["C9.1", "U1.99"], "why": "x"},
           {"action": "disconnect", "net": "TXD", "pins": ["J3.1"], "why": "x"}]
    circuit, problems, _, _ = apply_edits(base, bad, resolver)
    assert circuit is None
    assert any("U1.99" in p for p in problems) and any("C9.2" in p and "새 부품" in p for p in problems)
    assert any("J3.1" in p and "떴습니다" in p for p in problems)


def test_edit_problems_go_back_to_the_ai_with_the_real_pins(tmp_path, resolver):
    from ai_engine.design import DesignInput, revise_by_edits

    base = can_candidate(tmp_path)
    answers = iter([
        {"summary": "TVS", "addressed": ["F1"], "edits": [
            {"action": "add_part", "ref": "D1", "category": "diode", "value": "TVS", "why": "F1"},
            {"action": "connect", "net": "CANH", "pins": ["D1.K"], "why": "F1"}]},
        {"summary": "TVS", "addressed": ["F1"], "edits": [
            {"action": "add_part", "ref": "D1", "category": "diode", "value": "TVS", "why": "F1"},
            {"action": "connect", "net": "CANH", "pins": ["D1.1"], "why": "F1"},
            {"action": "connect", "net": "GND", "pins": ["D1.2"], "why": "F1"}]},
    ])
    provider = Scripted(lambda prompt: json.dumps(next(answers), ensure_ascii=False))
    inp = DesignInput(requirements=REQUIREMENTS, spec=None, board_name="A", instructions=None)
    result = revise_by_edits(provider, inp, base, [finding("warning", "ESD 보호 없음")], resolver)
    assert result.circuit is not None and result.attempts == 2 and result.changes[-1] == "D1.2 → GND"
    second = provider.calls[1]["prompt"]
    assert "[직전 답에서 생긴 문제" in second and "[새 부품의 실제 핀 표]" in second and "- D1 [Device:D] 핀: 1=K, 2=A" in second
    assert [f["title"] for f in result.addressed] == ["ESD 보호 없음"]


def test_when_the_ai_declines_every_finding_the_loop_says_so(tmp_path):
    base_reply, _ = reviews_in_turn([finding("error", "애매한 결함")])

    def reply(prompt):
        if "회로를 새로 설계하지 마세요" in prompt:
            return json.dumps({"summary": "고칠 것 없음", "edits": [], "addressed": [],
                               "not_addressed": [{"id": "F1", "why": "요구사항상 의도된 구성입니다"}]}, ensure_ascii=False)
        return base_reply(prompt)
    client, board, job = designed(tmp_path, reply)
    auto = wait(client, client.post(board + "/ai/auto-review", json={"proposal_id": job["result"]["proposal_id"]}).json())
    r = auto["result"]
    assert r["stop"] == "declined" and "요구사항상 의도된 구성입니다" in r["message"]
    assert auto["progress"][-1]["text"].startswith("멈춤: AI가 남은 의견을 고치지 않기로 함")


def test_edits_that_cannot_be_made_fall_back_to_a_redesign(tmp_path):
    state = {"plans": 0}
    base = scripted(bad_first=False)

    def reply(prompt):
        if "회로를 새로 설계하지 마세요" in prompt:
            return json.dumps({"summary": "x", "addressed": ["F1"],
                               "edits": [{"action": "connect", "net": "GND", "pins": ["U1.42"], "why": "F1"}]})
        if "부품 목록을 정하세요" in prompt:
            state["plans"] += 1
        return base(prompt)
    client, _, pid = design_client(tmp_path, reply)
    board = f"/api/projects/{pid}/boards/a"
    first = wait(client, client.post(board + "/ai/design", json={"review": False}).json())["result"]["proposal_id"]
    job = wait(client, client.post(board + "/ai/revise", json={"proposal_id": first, "instructions": "GND 보강",
                                                               "review": False}).json())
    assert job["status"] == "succeeded", job
    assert state["plans"] == 2 and job["result"]["attempts"] >= 1
    assert any("고칠 곳만으로는 해결하지 못해 회로를 다시 설계" in s["text"] for s in job["progress"])
    assert client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()["ai"]["method"] == "redesign"


def test_a_new_value_takes_its_part_number_and_notes_can_be_fixed(tmp_path, resolver):
    """Found with a real-model run: L1 10 µH → 33 µH kept the 10 µH part number, so the BOM ordered the old part;
    and the block note still said 2.1 MHz after the regulator was swapped."""
    from ai_engine.design import DesignInput, apply_edits, edit_prompt
    from circuit_core import CircuitIR

    data = can_candidate(tmp_path).model_dump(mode="json")
    r1 = next(c for c in data["components"] if c["reference"] == "R1")
    r1["manufacturer_part_number"] = "RC0603FR-07120RL"
    data["notes"] = [*(data.get("notes") or []), {"id": "note-x", "text": "CAN termination 120R", "components": [r1["id"]]}]
    base = CircuitIR.model_validate(data)
    n = next(i for i, nt in enumerate(base.notes) if nt.id == "note-x") + 1
    prompt = edit_prompt(DesignInput(requirements=REQUIREMENTS, spec=None, board_name="A", instructions=None), base, [])
    assert "- R1 120 품번 RC0603FR-07120RL [Device:R]" in prompt
    assert "[회로도 주석]" in prompt and f"- N{n} (R1): CAN termination 120R" in prompt and "set_note" in prompt

    # A new value without a part number drops the old one (it names the old value).
    circuit, problems, changes, _ = apply_edits(base, [{"action": "set_value", "ref": "R1", "value": "121", "why": "F1"}],
                                                resolver)
    assert problems == [] and next(c for c in circuit.components if c.reference == "R1").manufacturer_part_number is None
    assert "R1 품번 RC0603FR-07120RL 지움 (값이 바뀜)" in changes
    # With a part number it takes the new one; a note is rewritten; a wrong note number is a problem.
    circuit, problems, changes, _ = apply_edits(base, [
        {"action": "set_value", "ref": "R1", "value": "121", "mpn": "RC0603FR-07121RL", "why": "F1"},
        {"action": "set_note", "note": f"N{n}", "text": "CAN termination 121R", "why": "F1"}], resolver)
    assert problems == [] and next(c for c in circuit.components if c.reference == "R1").manufacturer_part_number == "RC0603FR-07121RL"
    assert next(nt for nt in circuit.notes if nt.id == "note-x").text == "CAN termination 121R"
    assert f"주석 N{n}: CAN termination 120R → CAN termination 121R" in changes
    _, problems, _, _ = apply_edits(base, [{"action": "set_note", "note": "N99", "text": "x", "why": "F1"}], resolver)
    assert problems and "N99" in problems[0]


def test_a_three_pin_diode_is_never_a_two_pin_generic(resolver):
    """Found in a real-flow run: SM712 (SOT-23, three pins) named with a wrong symbol became Device:D (two pins)."""
    tvs, _ = resolver({"ref": "D3", "category": "diode", "value": "SM712", "mpn": "SM712.TCT", "package": "SOT-23",
                       "kicad_symbol": "Power_Protection:SM712", "search_terms": ["SM712"]})
    assert tvs is not None and tvs.symbol_id == "Diode:SM712_SOT23" and len(tvs.pins) == 3
    unknown, why = resolver({"ref": "D9", "category": "diode", "value": "XYZ3PIN", "package": "SOT-23"})
    assert unknown is None and "2핀 일반 심볼" in why
    plain, _ = resolver({"ref": "D1", "category": "diode", "value": "1N4148", "package": "SOD-123"})
    assert plain is not None and plain.symbol_id == "Device:D"


def test_an_edit_can_set_the_package_and_footprint(tmp_path, resolver):
    from ai_engine.design import DesignInput, apply_edits, edit_prompt

    base = can_candidate(tmp_path)
    j2 = next(c for c in base.components if c.reference == "J2")
    prompt = edit_prompt(DesignInput(requirements=REQUIREMENTS, spec=None, board_name="A", instructions=None), base, [])
    assert f"- J2 CAN [{j2.symbol_id}] 핀:" in prompt and "· 풋프린트 PinHeader_1x02_P2.54mm_Vertical" in prompt
    circuit, problems, changes, _ = apply_edits(base, [{"action": "set_value", "ref": "J2",
                                                        "package": "5.08mm 단자대", "why": "F1"}], resolver)
    assert problems == [] and any("J2 풋프린트" in c for c in changes)
    assert next(c for c in circuit.components if c.reference == "J2").footprint_id.endswith("P5.08mm_Horizontal")
    _, problems, _, _ = apply_edits(base, [{"action": "set_value", "ref": "J2", "package": "모름", "why": "F1"}],
                                    resolver)
    assert problems and "풋프린트를 정하지 못했습니다" in problems[0]


def test_the_parts_plan_sees_inventory_ratings_and_notes():
    """Found in a real-flow run: the CSV's 정격 (a capacitor's voltage) and 비고 (a crystal's CL) never reached the AI."""
    from ai_engine.design import DesignInput, plan_prompt

    inv = [{"id": "inv-1", "category": "capacitor", "value": "10uF", "package": "1206", "mpn": "CL31B106KBHNNNE",
            "manufacturer": "Samsung", "stock": 300, "rating": "50V", "tolerance": "10%", "notes": None},
           {"id": "inv-2", "category": "crystal", "value": "16MHz", "package": "3225", "mpn": "ABM8", "manufacturer": "Abracon",
            "stock": 50, "rating": None, "tolerance": None, "notes": "CL 18pF"}]
    text = plan_prompt(DesignInput(requirements=REQUIREMENTS, inventory=inv))
    assert "재고 300 | 정격 50V | 오차 10%" in text and "재고 50 | 비고 CL 18pF" in text


def test_a_revision_that_changes_nothing_makes_no_proposal(tmp_path):
    base = scripted(bad_first=False)

    def reply(prompt):
        if "회로를 새로 설계하지 마세요" in prompt:  # an "edit" that sets the value R1 already has
            return json.dumps({"summary": "F1: R1 그대로", "addressed": ["F1"],
                               "edits": [{"action": "set_value", "ref": "R1", "value": "120", "why": "F1"}]},
                              ensure_ascii=False)
        return base(prompt)
    client, _, pid = design_client(tmp_path, reply)
    board = f"/api/projects/{pid}/boards/a"
    first = wait(client, client.post(board + "/ai/design", json={"review": False}).json())["result"]["proposal_id"]
    job = wait(client, client.post(board + "/ai/revise", json={"proposal_id": first, "instructions": "R1 확인",
                                                               "review": False}).json())
    assert job["status"] == "failed" and job["error"]["error_code"] == "AI_NO_CHANGE", job
    assert [p["id"] for p in client.get(board + "/proposals").json()] == [first]


def test_revision_titles_read_like_the_summary():
    from backend.routes_design import revision_title

    assert revision_title("F1, F2: 2.1 MHz 벅을 0.7 MHz 버전으로 바꿨습니다. 분압도 더했습니다.", ["U1 뺌"]) == \
        "수정: 2.1 MHz 벅을 0.7 MHz 버전으로 바꿨습니다."
    assert revision_title("", ["U1 뺌", "U5 추가", "R8 추가", "R9 추가"]) == "수정: U1 뺌, U5 추가, R8 추가 외"


# --- designs follow the design guides from the start ------------------------------------------------------------------

def test_the_connection_step_gets_the_guides_the_parts_bring(tmp_path):
    client, created, pid = design_client(tmp_path, scripted(bad_first=False))
    # The requirements never say "CAN"; the AI picks SN65HVD230, which brings its own guide (G-IF-02).
    client.put(f"/api/projects/{pid}/requirements", json={"text": "3.3V 전원 노드. MCU와 UART(TXD/RXD)로 연결하고 차동 버스로 내보낸다."})
    board = f"/api/projects/{pid}/boards/a"
    job = wait(client, client.post(board + "/ai/design", json={"review": False}).json())
    assert job["status"] == "succeeded", job
    calls = [c["prompt"] for provider in created if hasattr(provider, "calls") for c in provider.calls]
    plan = next(c for c in calls if "부품 목록을 정하세요" in c)
    connect = next(c for c in calls if "핀 표만 써서" in c and "부품 목록을 정하세요" not in c)
    assert "[G-IF-02]" not in plan  # not from the requirements text
    assert "[G-IF-02]" in connect  # the chosen transceiver brings it
    assert any(s["text"] == "고른 부품에 맞는 설계 지침으로 연결합니다" for s in job["progress"])
    p = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    assert "G-IF-02" in p["ai"]["guides"]["offered"]


def test_guide_violations_are_fixed_before_the_review(tmp_path):
    plan = {**PLAN, "parts": [x for x in PLAN["parts"] if x["ref"] != "C1"]}
    nets = {**GOOD, "nets": [{**n, "pins": [x for x in n["pins"] if not x.startswith("C1.")]} for n in GOOD["nets"]]}
    prompts = []

    def reply(prompt):
        if "회로를 새로 설계하지 마세요" in prompt:
            prompts.append(prompt)
            return json.dumps({"summary": "디커플링 추가", "addressed": ["F1", "F2"], "edits": [
                {"action": "add_part", "ref": "C1", "category": "capacitor", "value": "100nF", "package": "0603",
                 "role": "U1 디커플링", "why": "F1·F2: G-PWR-04/05"},
                {"action": "connect", "net": "+3V3", "pins": ["C1.1"], "why": "F1"},
                {"action": "connect", "net": "GND", "pins": ["C1.2"], "why": "F1"}]}, ensure_ascii=False)
        if "부품 목록을 정하세요" in prompt:
            return json.dumps(plan, ensure_ascii=False)
        if "핀 표만 써서" in prompt:
            return json.dumps(nets, ensure_ascii=False)
        return scripted(bad_first=False)(prompt)
    client, board, job = designed(tmp_path, reply)
    assert job["status"] == "succeeded", job
    texts = [s["text"] for s in job["progress"]]
    assert any(t.startswith("설계 지침 자동 점검: 위반 2개 (G-PWR-04, G-PWR-05)") for t in texts)
    assert any(t.startswith("지침 위반 고침: 2 → 0") for t in texts)
    # The check's own edit steps stay under "check" so the parts/pins rows keep their results.
    assert all(s["step"] == "check" for s in job["progress"] if s["text"].startswith(("고친 곳", "편집 ")))
    assert any(s["step"] == "parts" and "부품" in s["text"] and "선택" in s["text"] for s in job["progress"])
    assert "[G-PWR-04]" in prompts[0] and "F1 [error] G-PWR-04" in prompts[0]
    p = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    assert p["ai"]["guide_check"]["before"] == ["G-PWR-04", "G-PWR-05"] and p["ai"]["guide_check"]["after"] == []
    assert any(c["reference"] == "C1" and c["value"] == "100nF" for c in p["candidate"]["components"])


def test_jobs_keep_running_and_show_up_for_every_screen(tmp_path):
    client, _, pid = design_client(tmp_path)
    board = f"/api/projects/{pid}/boards/a"
    started = client.post(board + "/ai/design", json={"review": False}).json()
    # Nothing on the screen waits for it: the job runs in the backend and the status list sees it from anywhere.
    done = wait(client, started)
    active = client.get("/api/jobs/active").json()
    [row] = [j for j in active if j["id"] == started["id"]]
    assert row["status"] == "succeeded" and row["project_name"] == "CAN Node" and row["board_name"] == "보드 A"
    assert row["kind"] == "ai_design" and row["proposal_id"] == done["result"]["proposal_id"] and row["step"]
    assert client.get("/api/jobs/active", params={"recent_seconds": 0}).json() == []  # finished ones age out
