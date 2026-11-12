"""Test-only backend launcher for the desktop e2e tests: the real app with a scripted fake AI and an in-memory
key store, so the agent screen can be driven end to end without calling a real AI.

    python -m tests.e2e_backend --no-auth --workspace <dir> --ui-dir desktop/dist
"""

from __future__ import annotations

import json

from ai_engine import LlmResult, ModelInfo
from backend.__main__ import main

R1_TO_1K = {"op": "set_property", "component_id": "res-001", "field": "value", "expect": "330", "value": "1k"}
PLAN = {"summary": "R1을 1k로 바꿔 LED 전류를 줄입니다.", "steps": ["R1 값 변경", "회로도 생성과 ERC 확인"],
        "scope": [{"op": "set_property", "target": "res-001", "field": "value", "why": "LED 전류 제한"}],
        "risks": ["LED가 어두워짐"], "questions": []}
STEPS = [("read_project", {}), ("propose_ir_patch", {"ops": [R1_TO_1K]}), ("validate_ir", {}),
         ("generate_schematic", {"name": "candidate"}), ("run_erc", {"name": "candidate"}),
         ("finish", {"summary": "R1을 1k로 바꿨습니다."})]


NOTE = {"title": "5V→3.3V LDO 보드: 드롭아웃 여유", "summary": "최저 입력에서 LDO 드롭아웃 여유가 작다.",
        "background": "호스트 5V에서 LDO U1(LM1117-3.3)로 3.3V I2C 센서에 공급한다.", "parts": ["LM1117-3.3"],
        "topics": ["ldo"],
        "checks": [{"what": "최저 입력 드롭아웃", "how": "기술 검토 계산", "result": "여유가 작음", "status": "issue",
                    "evidence": ["E1"]}],
        "learnings": [{"text": "LDO 드롭아웃은 정격 입력이 아니라 최저 입력으로 계산해야 한다.", "evidence": ["E1"]},
                      {"text": "PTC 저항도 입력 전압을 낮춘다.", "evidence": []}],
        "apply_next": [{"text": "전원 조건에 입력 최저 전압을 적는다.", "why": "최악 조건 계산의 입력이다.", "evidence": ["E1"]}],
        "open_questions": [{"text": "호스트 5V의 실제 최저 전압을 측정한다.", "evidence": []}]}


# The step screens (아이디어 → 회로도): requirements, a CAN-node design on the test symbol library, and reviews that
# need one revision before 「수정 필요 0 · 수정 권장 ≤ 1」 holds.
SPEC = {"summary": "3.3V CAN 노드", "functions": ["CAN 통신", "MCU와 UART 연결"],
        "power": [{"name": "입력", "source": "외부", "voltage": "3.3 V", "current": "50 mA", "notes": ""}],
        "interfaces": [{"name": "CAN", "kind": "CAN", "details": "버스 끝단"}, {"name": "UART", "kind": "UART", "details": ""}],
        "parts_preferences": ["트랜시버 SN65HVD230"], "constraints": ["칩 부품 0603"], "open_questions": ["CAN 버스 속도는?"]}
REVIEWS = [
    [{"severity": "error", "title": "종단 저항 값 확인", "detail": "120Ω인지 확인", "references": ["R1"], "suggestion": None,
      "basis": "circuit", "evidence": "R1.1이 CANH에 연결"},
     {"severity": "warning", "title": "ESD 보호 없음", "detail": "CAN 라인에 TVS가 없습니다.", "references": ["J2"],
      "suggestion": "TVS 추가", "basis": "circuit", "evidence": "J2.1 CANH"}],
    [{"severity": "warning", "title": "CAN 라인 TVS 권장", "detail": "버스 쪽 서지 보호", "references": ["J2"], "suggestion": None,
      "basis": "circuit", "evidence": "J2.2 CANL"},
     {"severity": "info", "title": "디커플링 적절", "detail": "100nF가 VCC 가까이에 있습니다.", "references": ["C1"],
      "suggestion": None, "basis": "judgment"},
     {"severity": "warning", "title": "커넥터 방향 표시 권장", "detail": "취향 문제", "references": ["J1"], "suggestion": None,
      "basis": "judgment"}],
]


def design_reply(prompt: str, state: dict) -> str | None:
    from tests.test_design import ALTERNATIVES, GOOD, MISSING, PLAN

    if "정해진 항목으로 정리" in prompt:
        return json.dumps(SPEC, ensure_ascii=False)
    if "회로를 새로 설계하지 마세요" in prompt:  # a revision by edits: add the TVS the review asked for
        state["edits"] = state.get("edits", 0) + 1
        return json.dumps({"summary": "CAN 라인에 TVS 추가", "addressed": ["F1", "F2"], "not_addressed": [],
                           "edits": [{"action": "set_value", "ref": "R1", "value": f"12{state['edits']}",
                                      "why": "F1: 종단 저항 값 확인"}]}, ensure_ascii=False)
    if "대안을 2~4개 제시하세요" in prompt:
        return json.dumps(ALTERNATIVES, ensure_ascii=False)
    if "부품 목록을 정하세요" in prompt:
        # An idea naming XYZ999 gets a part this library has no symbol for (until the person says otherwise).
        missing = "XYZ999" in prompt and "사람이 정한 방향" not in prompt
        return json.dumps(MISSING if missing else PLAN, ensure_ascii=False)
    if "핀 표만 써서" in prompt:
        return json.dumps(GOOD, ensure_ascii=False)
    if "검토자" in prompt:
        findings = REVIEWS[min(state.setdefault("reviews", 0), len(REVIEWS) - 1)]
        state["reviews"] += 1
        return json.dumps({"summary": f"검토 {state['reviews']}", "findings": findings}, ensure_ascii=False)
    return None


class ScriptedAi:
    name = "gemini"
    state: dict = {}

    def __init__(self, model: str):
        self.model = model

    def generate(self, *, prompt, attachments=(), json_schema=None, max_output_tokens=8192, temperature=0.0):
        if prompt.startswith("Connection test from AI Circuit Studio"):
            text = "OK"
        elif "연구노트로 남기세요" in prompt:
            text = json.dumps(NOTE, ensure_ascii=False)
        elif "회로 변경을 계획하는 EDA 에이전트" in prompt:
            text = json.dumps(PLAN, ensure_ascii=False)
        elif "승인한 범위 안에서만" in prompt:
            tool, args = STEPS[min(prompt.count("→ <data>"), len(STEPS) - 1)]
            text = json.dumps({"thought": "다음 단계", "tool": tool, "args_json": json.dumps(args)}, ensure_ascii=False)
        elif "데이터시트 요약 MD v0.1" in prompt:  # the design's datasheet step: a summary of the fetched PDF
            from tests.conftest import FIXTURES

            text = (FIXTURES / "datasheets" / "sn65hvd230-test.md").read_text(encoding="utf-8")
        elif (reply := design_reply(prompt, ScriptedAi.state)) is not None:
            text = reply
        else:
            raise RuntimeError("the e2e fake AI only answers agent prompts, research notes, design steps and the connection test")
        return LlmResult(text=text, model=self.model, usage={"total_token_count": 100}, finish_reason="STOP")

    def list_models(self):
        return [ModelInfo(self.model, "Fake", 1000, 1000)]


class MemoryKeys:
    available = True

    def get(self, provider):
        # An AI key for whichever provider is asked, and nothing else. A distributor key (Mouser, DigiKey) must
        # read as unset, or the design's datasheet step would send part-number searches to the real services.
        from backend.datasheet_search import KEY_NAMES

        if any(provider in names for names in KEY_NAMES.values()):
            return None, None
        return "e2e-fake-key-0000", "keyring"

    def set(self, provider, key):
        pass

    def delete(self, provider):
        return False


def fetch_test_pdf(url: str):
    """Datasheet downloads never leave the machine in e2e runs: the one part the scripted design uses gets the
    repository's test PDF, anything else is refused the way a blocked site would be."""
    from backend.datasheets import DatasheetError
    from tests.pdfs import SN65HVD230_TEST_PDF_LINES, make_pdf

    if "sn65hvd230" in url.lower():
        return make_pdf(SN65HVD230_TEST_PDF_LINES), url
    raise DatasheetError("DATASHEET_HTTP_ERROR", "서버가 403으로 답했습니다.", "브라우저에서 받아 「PDF 올리기」로 넣으세요.")


def configure(settings) -> None:
    settings.keystore = MemoryKeys()
    settings.ai_provider_factory = lambda provider, key, model, timeout: ScriptedAi(model)
    settings.datasheet_fetcher = fetch_test_pdf
    settings.datasheet_searchers = lambda keystore: []  # no distributor searches from a test run, whatever the keys


if __name__ == "__main__":
    raise SystemExit(main(configure=configure))
