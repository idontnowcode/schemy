"""Review by perspective.

One review that looks at everything at once misses things: in a real-flow run the first review of a 0–10 V input
module did not see that 1 % divider resistors cannot give the required ±1 % of full scale; a second review did.
Here the same circuit and evidence are reviewed once per concern (requirements, power and protection, signals,
control), and for each concern the app gives a checklist the reviewer must answer item by item — checked and fine,
a problem, cannot tell, or not applicable — with the numbers. An item that asks for the error budget "in numbers"
cannot be skipped the way a general look skips it.

A pass returns what it checked (``checks``) as well as its findings, so a review with no findings still shows what
was looked at. Findings go through the same basis check as any review finding. Nothing here changes a circuit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from circuit_core import CircuitIR
from circuit_core.guides import topics_of_circuit, topics_of_text

from .design import (
    _KO,
    _STRS,
    LANGUAGE_RULE,
    REVIEW_SCHEMA,
    _ask_json,
    _strs,
    review_basis_rules,
    review_evidence,
    review_findings,
    review_history_blocks,
    review_material,
)
from .providers import LlmProvider, LlmResult

CHECK_RESULTS = ("ok", "issue", "unknown", "na")
"""ok: checked and fine. issue: a problem (also written as a finding). unknown: nothing to judge it by.
na: does not apply to this circuit."""

PASS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _KO,
        "checks": {"type": "array", "items": {"type": "object", "properties": {
            "item": {"type": "string", "description": "확인 항목 번호를 먼저 적고((1), (2) …) 무엇을 봤는지, 한국어로"},
            "result": {"type": "string", "enum": list(CHECK_RESULTS)},
            "note": {"type": "string", "description": "계산식·수치·본 핀과 Net, 한국어로 짧게"},
            "references": _STRS,
        }, "required": ["item", "result", "note"]}},
        "findings": REVIEW_SCHEMA["properties"]["findings"],
    },
    "required": ["summary", "checks", "findings"],
}


@dataclass(frozen=True)
class Group:
    """Checklist items that apply when the circuit or the requirements touch one of ``topics`` (empty: always)."""

    topics: frozenset[str]
    items: tuple[str, ...]


@dataclass(frozen=True)
class Perspective:
    key: str
    label: str
    focus: str
    groups: tuple[Group, ...]
    circuit_only: bool = False
    """Applies by what is on the board, not by what the requirements mention ("MCU와 UART로 연결" names an MCU
    that is on another board)."""

    def items_for(self, topics: set[str]) -> list[str]:
        return [item for g in self.groups if not g.topics or g.topics & topics for item in g.items]


def _g(topics: str, *items: str) -> Group:
    return Group(frozenset(topics.split()), items)


PERSPECTIVES: tuple[Perspective, ...] = (
    Perspective(
        "requirements", "요구사항 대조",
        "요구사항 글, 정리된 요구사항, AI 질문에 대한 답의 문장을 하나씩 회로와 맞춰 봅니다. 회로가 좋은지가 아니라 "
        "요구한 것이 모두 들어 있고 요구한 수치를 지키는지를 봅니다.",
        (_g("",
            "요구사항의 기능·인터페이스·커넥터 항목마다 회로의 어느 부품·Net이 그 요구를 맡는지 (항목 하나에 확인 하나, "
            "맡는 것이 없으면 issue)",
            "수치가 있는 요구(전압 범위, 전류·소비 전력, 정확도, 속도, 온도)마다 회로 값으로 계산해 만족하는지 — 숫자로",
            "「두지 않는다」「쓰지 않는다」 같은 제외·금지 요구를 어기지 않았는지",
            "부품 지정(사내 표준 부품, 보유 자재 우선, 특정 품번·패키지·커넥터 종류)이 지켜졌는지",
            "요구사항에 없는데 들어간 기능이나 부품이 있는지 (있으면 이유와 함께 참고로)"),),
    ),
    Perspective(
        "power", "전원·보호",
        "전원이 들어오는 곳부터 각 레일의 부하까지, 그리고 밖으로 나가는 모든 커넥터 핀의 보호를 봅니다.",
        (_g("power ldo dcdc",
            "입력 전압의 최소와 최대에서 각 레귤레이터의 입력 한계(권장·절대 최대)와 드롭아웃·최소 입력을 만족하는지 — 숫자로",
            "레일마다 부하 전류 합계와, 레귤레이터·인덕터·다이오드·퓨즈 전류 정격의 여유 — 숫자로",
            "레귤레이터의 손실과 발열 (리니어: (입력 − 출력) × 전류, 스위칭: 효율로 추정)",
            "레귤레이터 입력·출력 콘덴서의 용량·종류가 데이터시트 응용 회로와 맞는지, 정격 전압이 걸리는 최대 전압의 "
            "1.25~1.5배 이상인지 (정격을 모르면 unknown)",
            "IC 전원 핀마다 디커플링 콘덴서, 레일마다 벌크 콘덴서",
            "요구된 소비 전류·전력 한도를 지키는지, EN·ON/OFF 같은 전원 제어 핀의 처리"),
         _g("protection connector power",
            "밖으로 나가는 커넥터의 전원·신호 핀마다 역극성, 과전압·서지·ESD, 과전류 보호가 있는지 (없으면 없어도 되는 이유)",
            "보호 소자(TVS·제너·클램프)의 동작 전압 ≥ 정상 최대 전압이고, 클램프 전압 < 보호받는 부품의 절대 최대 정격인지 — 숫자로",
            "퓨즈·PTC의 유지 전류 > 정상 최대 전류, 정격 전압 ≥ 최대 입력인지",
            "요구된 오배선·오입력(예: 신호 입력에 전원 전압)에서 각 부품에 걸리는 전압과 전류 — 숫자로, 클램프 전류를 "
            "받아 낼 부하가 그 레일에 있는지",
            "역극성 보호 소자의 역전압·전류 정격과, 그 전압 강하가 뒤 회로의 최소 입력에 주는 영향")),
    ),
    Perspective(
        "signals", "인터페이스·아날로그",
        "보드 밖과 주고받는 통신 신호와, 측정·기준 전압 같은 아날로그 경로를 봅니다.",
        (_g("interface i2c",
            "버스마다 종단·바이어스·풀업의 유무와 값 (버스 규격과 속도 기준)",
            "트랜시버·상대 장치와 MCU 사이의 전압 레벨 호환 (5 V / 3.3 V, 입력 문턱)",
            "TX/RX, A/B, CANH/CANL 같은 방향·극성이 커넥터까지 맞는지",
            "방향 제어(DE·RE)·EN·모드 핀의 기본 상태 (리셋 중에도 버스를 잡지 않는지)",
            "요구된 통신 속도·케이블 길이에 부품이 맞는지, 커넥터 핀 배치와 접지 핀이 요구와 같은지"),
         _g("analog",
            "측정 경로의 오차 예산: 분압·증폭 저항 오차, 기준 전압 오차, ADC 오차, 누설·오프셋을 더해 요구 정확도와 비교 — 숫자로",
            "정상 입력 범위가 ADC·증폭기 입력 범위 안이고 분해능을 충분히 쓰는지 (최대 입력에서 기준 전압의 몇 %)",
            "신호원 임피던스와 ADC 샘플링 조건, 필터 시정수와 요구 응답 속도",
            "기준 전압의 동작 전류·부하·바이패스가 데이터시트와 맞는지, 아날로그 전원의 필터",
            "클램프·보호 소자의 누설 전류가 측정값에 주는 영향")),
    ),
    Perspective(
        "control", "MCU·부하 구동",
        "MCU가 켜지고, 프로그램을 받고, 맡은 핀 기능을 실제로 할 수 있는지와, MCU가 구동하는 부하를 봅니다.",
        (_g("mcu clock",
            "리셋 핀 처리(풀업·콘덴서)와 부트·모드 핀의 기본 상태",
            "프로그래밍·디버그 커넥터의 핀 연결 (전원·GND·리셋 포함)",
            "클럭: 크리스털 부하 콘덴서 값(C = 2 × (CL − 배선 용량)), 요구 통신 속도의 오차",
            "핀 기능 배정이 그 핀에서 실제로 가능한지 (ADC 채널, UART·SPI·I2C, 타이머·PWM) — 핀 이름으로 확인",
            "MCU 전원·접지 핀이 모두 연결됐는지, 쓰지 않는 핀과 미연결 표시가 적절한지",
            "GPIO가 직접 구동하는 부하의 전류가 핀·포트 한도 안인지 (LED, 트랜지스터 베이스 등)"),
         _g("motor load isolation",
            "스위치 소자(MOSFET·BJT·릴레이·드라이버)의 전압·전류 정격과 손실 — 숫자로",
            "유도성 부하의 환류 다이오드·스너버",
            "게이트·베이스 저항과 풀다운(리셋 중 꺼짐), 구동 전압으로 충분히 켜지는지",
            "절연 소자의 전류 제한 저항·전달비와 절연 양쪽 접지 분리, 모터 전원의 벌크 콘덴서")),
        circuit_only=True,
    ),
)


_ANALOG_WORDS = re.compile(r"아날로그|analog|정확도|분해능|측정|0\s*~\s*10\s*V|4\s*~\s*20\s*mA", re.I)


def circuit_review_topics(circuit: CircuitIR) -> set[str]:
    """Guide topics of the circuit, plus what the guide topics do not look for: an analog path that is only a
    divider into an MCU's ADC with a voltage reference."""
    topics = topics_of_circuit(circuit)
    for comp in circuit.components:
        if comp.symbol_id.split(":")[0] == "Reference_Voltage":
            topics.add("analog")
    names = " ".join(n.name.upper() for n in circuit.nets)
    if any(w in names for w in ("ADC", "AREF", "AIN", "VREF")):
        topics.add("analog")
    return topics


def select_perspectives(circuit: CircuitIR, requirements: str) -> list[tuple[Perspective, list[str]]]:
    """The perspectives that apply to this circuit, each with the checklist items that apply."""
    on_board = circuit_review_topics(circuit)
    asked = on_board | topics_of_text(requirements)
    if _ANALOG_WORDS.search(requirements):  # the guide topics know "ADC" and "센서", not "아날로그 입력"
        asked.add("analog")
    out = []
    for p in PERSPECTIVES:
        items = p.items_for(on_board if p.circuit_only else asked)
        if items:
            out.append((p, items))
    return out


def pass_prompt(perspective: Perspective, items: list[str], *, requirements: str, spec: dict | None,
                circuit: CircuitIR, evidence_items: list[tuple[str, str]], passed: int, guides: str,
                previous: list[dict] | None, decided: list[dict] | None, changes: list[str] | None) -> str:
    others = ", ".join(p.label for p in PERSPECTIVES if p.key != perspective.key)
    # Numbered "(1)", not "K1": a letter and a number is a part reference on some board (K1 is a relay).
    checklist = "\n".join(f"- ({i + 1}) {t}" for i, t in enumerate(items))
    changed = ""
    if changes:
        changed = ("\n[이번 수정안에서 바뀐 것 — 바뀐 곳과 그 영향을 먼저 보세요]\n"
                   + "\n".join(f"- {c}" for c in changes[:30]) + "\n")
    return f"""당신은 회로 설계 검토자입니다. 이번에는 아래 회로를 「{perspective.label}」 관점 하나로만 깊게 검토하세요.
다른 관점({others})은 따로 검토하니 여기서는 적지 마세요. 회로를 고치지 말고, 확인한 것(checks)과 지적 사항(findings)만 적으세요.

[이번 관점: {perspective.label}]
{perspective.focus}

[확인 항목 — 하나도 빼지 말고 모두 답하세요]
{checklist}

[답하는 법]
- 확인 항목마다 checks에 한 줄 이상 적으세요. item은 항목 번호로 시작하고(예: "(3) U4 손실") 무엇을 봤는지, note에는 계산식·수치·본 핀과 Net을 짧게, references에는 부품 기호를 적으세요.
- result: ok(확인했고 문제없음) / issue(문제 있음) / unknown(판단할 자료가 없음 — 무엇이 있으면 판단할 수 있는지 note에) / na(이 회로에 해당 없음).
- 「숫자로」라고 적힌 항목은 반드시 계산해서 note에 숫자를 적으세요(예: "분압비 오차 0.75 × 2 % = 1.5 % > 요구 1 %"). 계산 없이 ok로 답하지 마세요.
- 항목 하나가 여러 부품·요구에 걸리면 나눠서 여러 줄로 적으세요(예: 요구사항 항목마다 한 줄).
- result가 issue인 것은 findings에도 근거와 함께 적으세요. 목록에 없어도 이 관점의 결함을 찾으면 findings에 적으세요.
- 확실한 결함은 error(수정 필요), 고치는 것이 좋은 것은 warning(수정 권장), 참고는 info입니다.

{review_basis_rules(passed)}
{review_history_blocks(decided, previous)}{changed}
{LANGUAGE_RULE}

{review_material(requirements, spec, circuit, evidence_items, guides)}

정해진 JSON 형식으로만 답하세요."""


def review_perspective(provider: LlmProvider, perspective: Perspective, items: list[str], *, requirements: str,
                       spec: dict | None, circuit: CircuitIR, evidence: str, datasheet_notes: str = "",
                       guides: str = "", guide_ids: set[str] | None = None, max_output_tokens: int = 16384,
                       previous: list[dict] | None = None, decided: list[dict] | None = None,
                       changes: list[str] | None = None) -> tuple[dict, LlmResult]:
    """One pass: ``{"key", "label", "summary", "asked", "checks", "findings"}``. ``asked`` is how many checklist items
    were given; ``checks`` is what the reviewer answered (cleaned), ``findings`` are verified like any review's."""
    evidence_items, passed = review_evidence(circuit, evidence, datasheet_notes)
    prompt = pass_prompt(perspective, items, requirements=requirements, spec=spec, circuit=circuit,
                         evidence_items=evidence_items, passed=passed, guides=guides, previous=previous,
                         decided=decided, changes=changes)
    data, result = _ask_json(provider, prompt=prompt, schema=PASS_SCHEMA, what=f"{perspective.label} 검토",
                             max_output_tokens=max_output_tokens)
    refs = {c.reference for c in circuit.components}
    checks = []
    for c in data.get("checks") or []:
        if not isinstance(c, dict) or not str(c.get("item", "")).strip():
            continue
        checks.append({"item": str(c["item"]).strip()[:300],
                       "result": c.get("result") if c.get("result") in CHECK_RESULTS else "unknown",
                       "note": str(c.get("note") or "").strip()[:600],
                       "references": [r for r in _strs(c.get("references")) if r.upper() in refs]})
    findings = review_findings(data, circuit=circuit, items=evidence_items, requirements=requirements, spec=spec,
                               guide_ids=guide_ids)
    for f in findings:
        f["perspective"] = perspective.key
    answered = {n for c in checks if (n := _item_number(c["item"])) is not None}
    return {"key": perspective.key, "label": perspective.label,
            "summary": str(data.get("summary", "")).strip()[:1000], "asked": len(items),
            # What the reviewer was asked and did not answer: said plainly, or a short answer reads as full cover.
            "skipped": [f"({i + 1}) {t}"[:200] for i, t in enumerate(items) if i + 1 not in answered],
            "checks": checks, "findings": findings, "model": result.model}, result


def _item_number(text: str) -> int | None:
    m = re.match(r"\s*[(\[]?\s*(\d{1,2})\s*[)\].:]", text)
    return int(m.group(1)) if m else None
