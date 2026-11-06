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


class ScriptedAi:
    name = "gemini"

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
        else:
            raise RuntimeError("the e2e fake AI only answers agent prompts, research notes and the connection test")
        return LlmResult(text=text, model=self.model, usage={"total_token_count": 100}, finish_reason="STOP")

    def list_models(self):
        return [ModelInfo(self.model, "Fake", 1000, 1000)]


class MemoryKeys:
    available = True

    def get(self, provider):
        return "e2e-fake-key-0000", "keyring"

    def set(self, provider, key):
        pass

    def delete(self, provider):
        return False


def configure(settings) -> None:
    settings.keystore = MemoryKeys()
    settings.ai_provider_factory = lambda provider, key, model, timeout: ScriptedAi(model)


if __name__ == "__main__":
    raise SystemExit(main(configure=configure))
