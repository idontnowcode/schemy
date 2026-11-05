"""AI design (Phase 2b) with a scripted fake AI: plan -> real KiCad symbols -> connections -> repair loop
-> proposal -> isolated check -> review. No real AI is called."""

import json

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
        {"ref": "J1", "role": "전원 입력", "category": "connector", "value": "PWR", "pin_count": 2},
        {"ref": "J2", "role": "CAN 버스", "category": "connector", "value": "CAN", "pin_count": 2},
        {"ref": "J3", "role": "UART", "category": "connector", "value": "UART", "pin_count": 2},
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


def scripted(bad_first=True):
    state = {"connect": 0}

    def reply(prompt):
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
    assert ai["diff_from_parent"]["summary"]["components_added"] == 0  # the scripted AI returns the same design
    calls = [c for provider in created if hasattr(provider, "calls") for c in provider.calls]
    plan_prompt = [c["prompt"] for c in calls if "부품 목록을 정하세요" in c["prompt"]][-1]
    assert "ESD 보호 없음" in plan_prompt and "JST-XH" in plan_prompt and "[현재 설계" in plan_prompt


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
