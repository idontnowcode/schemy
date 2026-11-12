"""AI circuit design from requirements, and AI design review (Phase 2b).

The AI never writes a circuit file. It answers three narrow questions in strict JSON, and code does
everything that must be right:

1. **plan**: which parts (role, value, package, maker part number, a KiCad symbol guess) and why;
2. **resolve** (code, see backend.design_resolver): each part becomes a symbol that really exists in
   the installed KiCad library, with its real pin numbers, names and electrical types;
3. **connect**: given exactly those pins, which pins share a net and which are intentionally unused.
   Code checks every pin reference; if anything is wrong, the AI gets the list of problems and may
   try again (at most ``max_repairs`` times).

The result is a Circuit IR that becomes a *proposal*; a person approves it after the code-computed
diff, KiCad ERC, netlist comparison and datasheet checks. :func:`review_circuit` asks the AI for
findings about a circuit (it changes nothing). :func:`structure_requirements` turns free text into
a requirements sheet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from circuit_core import CircuitIR, validate_circuit
from circuit_core.export import natural_key
from circuit_core.guides import CHECKS as _GUIDE_CHECKS
from circuit_core.guides import TOPICS as _GUIDE_TOPICS
from circuit_core.models import Component, Note
from circuit_core.parts import category_of, package_footprint
from circuit_core.templates import TEMPLATES, PinsOf, TemplateError, catalog_text, instantiate, template_sources

from .providers import AiError, LlmProvider, LlmResult

CATEGORIES = ["resistor", "capacitor", "capacitor_polarized", "inductor", "led", "diode", "fuse", "connector", "ic",
              "transistor", "crystal", "switch", "other"]
MAX_PARTS = 60
GUIDE_TOPICS = list(_GUIDE_TOPICS)
GUIDE_CHECKS = list(_GUIDE_CHECKS)

_STR = {"type": "string"}
_KO = {"type": "string", "description": "한국어로"}
_KOS = {"type": "array", "items": _KO}
_STRS = {"type": "array", "items": _STR}

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "한두 문장 요약, 한국어로"},
        "functions": _KOS,
        "power": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR, "source": _STR, "voltage": _STR, "current": _STR, "notes": _STR},
            "required": ["name", "source", "voltage"]}},
        "interfaces": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR, "kind": _STR, "details": _STR}, "required": ["name", "kind"]}},
        "parts_preferences": _KOS,
        "constraints": _KOS,
        "open_questions": _KOS,
    },
    "required": ["summary", "functions", "power", "interfaces", "parts_preferences", "constraints", "open_questions"],
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "설계안 제목, 한국어로"},
        "summary": {"type": "string", "description": "설계 요약과 근거, 한국어로"},
        "assumptions": _KOS,
        "questions": _KOS,
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "ref": {"type": "string", "description": "R1, C1, U1, J1, D1 ..."},
            "role": {"type": "string", "description": "부품의 역할, 한국어로"},
            "category": {"type": "string", "enum": CATEGORIES},
            "value": _STR,
            "package": _STR,
            "manufacturer": _STR,
            "mpn": _STR,
            "kicad_symbol": {"type": "string", "description": "KiCad 10 library id if known, e.g. "
                             "Regulator_Linear:AMS1117-3.3. Empty if unsure."},
            "pin_count": {"type": "integer", "description": "connectors only"},
            "search_terms": _STRS,
            "inventory_part_id": _STR,
            "reason": {"type": "string", "description": "이 부품·값을 고른 이유, 한국어로"},
            "basis": {"type": "string", "enum": ["requirement", "changed", "ai"],
                      "description": "requirement: 요구사항에 있는 그대로 / changed: 요구사항과 다르게 정함 / ai: 요구사항에 없어 직접 정함"},
            "request": {"type": "string", "description": "근거가 된 요구사항 문장 원문 (requirement·changed일 때)"},
            "calc": {"type": "string", "description": "값의 계산식, 예: (5V - 2V) / 1k = 3mA"},
        }, "required": ["ref", "role", "category", "value"]}},
        "templates": {"type": "array", "description": "검토된 회로 템플릿을 쓸 블록", "items": {
            "type": "object", "properties": {
                "id": {"type": "string", "enum": list(TEMPLATES)},
                "params": {"type": "array", "items": {"type": "object", "properties": {"name": _STR, "value": _STR},
                                                      "required": ["name", "value"]}},
                "why": _KO,
            }, "required": ["id", "params"]}},
        "guides_applied": {"type": "array", "items": _STR, "description": "따른 설계 지침 id (예: G-PWR-02)"},
    },
    "required": ["title", "summary", "assumptions", "questions", "parts"],
}

CONNECT_SCHEMA = {
    "type": "object",
    "properties": {
        "nets": {"type": "array", "items": {"type": "object", "properties": {
            "name": _STR,
            "pins": {"type": "array", "items": {"type": "string", "description": "REF.PIN_NUMBER, e.g. U1.3"}},
        }, "required": ["name", "pins"]}},
        "no_connect": {"type": "array", "items": {"type": "string", "description": "REF.PIN_NUMBER"}},
        "values": {"type": "array", "items": {"type": "object", "properties": {"ref": _STR, "value": _STR},
                                              "required": ["ref", "value"]}},
        "citations": {"type": "array", "items": {"type": "object", "properties": {
            "ref": _STR, "page": {"type": "integer"}, "text": _KO}, "required": ["ref", "text"]}},
        "notes": _KO,
        "schematic_notes": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string", "description": "English, at most 40 characters"},
            "refs": {"type": "array", "items": {"type": "string", "description": "part reference, e.g. U1"}},
            "net": {"type": "string", "description": "net name for a net note; empty for a block note"},
        }, "required": ["text", "refs", "net"]}},
        "rails": {"type": "array", "items": {"type": "object", "properties": {
            "net": _STR,
            "voltage": {"type": "number", "description": "volts; 0 when the net name already says it"},
            "load_ma": {"type": "number", "description": "mA the loads draw from this net; 0 when unknown"},
            "voltage_min": {"type": "number", "description": "lowest volts (e.g. 4.75 for 5 V -5%); 0 when unknown"},
            "voltage_max": {"type": "number", "description": "highest volts (e.g. 5.25); 0 when unknown"},
        }, "required": ["net", "voltage", "load_ma"]}},
        "guides_applied": {"type": "array", "items": _STR, "description": "연결에서 따른 설계 지침 id"},
    },
    "required": ["nets", "no_connect"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _KO,
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "severity": {"type": "string", "enum": ["error", "warning", "info"]},
            "title": _KO, "detail": _KO, "references": _STRS, "suggestion": _KO,
            "guide": {"type": "string", "description": "지키지 않은 설계 지침 id (해당할 때만, 예: G-MCU-02)"},
            "basis": {"type": "string", "enum": ["evidence", "guide", "requirement", "circuit", "judgment"],
                      "description": "이 지적의 근거 종류"},
            "evidence": {"type": "string", "description": "근거: 근거 목록 번호(E1, C2, D3), 요구사항 문장, 또는 실제 핀·Net"},
        }, "required": ["severity", "title", "detail", "basis"]}},
    },
    "required": ["summary", "findings"],
}

GUIDE_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "지침 제목, 한국어로, 40자 이내의 명령문 (예: 모터 전원에 벌크 콘덴서)"},
        "rule": {"type": "string", "description": "무엇을 해야 하는지, 한국어로, 구체적인 값·식 포함"},
        "why": {"type": "string", "description": "이유, 한국어로"},
        "strength": {"type": "string", "enum": ["must", "should", "consider"]},
        "topics": {"type": "array", "items": {"type": "string", "enum": GUIDE_TOPICS}},
        "parts": {"type": "array", "items": _STR, "description": "특정 부품에만 해당하면 품번·심볼의 일부 (예: DRV88)"},
        "keywords": {"type": "array", "items": _STR, "description": "요구사항에 나오면 이 지침을 적용할 단어"},
        "check": {"type": "string", "enum": ["", *GUIDE_CHECKS], "description": "같은 내용을 보는 앱 자동 검사, 없으면 빈 문자열"},
    },
    "required": ["title", "rule", "why", "strength", "topics"],
}


@dataclass
class DesignInput:
    requirements: str
    spec: dict | None = None
    board_name: str = "보드 A"
    instructions: str | None = None
    previous: str | None = None
    """Text summary of an existing design to revise (keep what works)."""
    findings: list[dict] | None = None
    """Review findings the new design must address."""
    inventory: list[dict] = field(default_factory=list)
    guides: str = ""
    """The adopted design-guide entries that apply (circuit_core.guides.prompt_text)."""


@dataclass
class ResolvedPart:
    ref: str
    role: str
    category: str
    value: str
    symbol_id: str
    pins: list[tuple[str, str, str]]
    """(number, name, electrical type) from the KiCad library."""
    how: str
    footprint_id: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    description: str | None = None
    datasheet_notes: str | None = None
    stacks: list[tuple[str, ...]] = field(default_factory=list)
    """Pin numbers stacked on one connection point in the symbol (extra GND/VCC pins): KiCad joins them."""
    reason: str = ""
    basis: str = "ai"
    request: str = ""
    calc: str = ""
    """The plan's rationale for the part: why, who decided (requirement / changed / ai), the asked words, the math."""
    specs: dict | None = None
    """Ratings the stock list gives for the stocked part (PartSpecs fields)."""


Resolver = Callable[[dict], tuple[ResolvedPart | None, str]]


@dataclass
class DesignResult:
    circuit: CircuitIR | None
    plan: dict
    parts: list[ResolvedPart] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    attempts: int = 0
    calls: list[LlmResult] = field(default_factory=list)
    connections: dict = field(default_factory=dict)
    templates: list[TemplateBlock] = field(default_factory=list)
    unresolved_parts: list[dict] = field(default_factory=list)
    """Planned parts with no usable KiCad symbol (the plan's own entry plus ``why``), for choosing alternatives."""


_CUT = {"MAX_TOKENS", "LENGTH", "MAX_OUTPUT_TOKENS"}
_BLOCKED = {"SAFETY": "안전 필터", "RECITATION": "원문 인용(학습 자료를 그대로 옮긴 문장으로 판단)",
            "BLOCKLIST": "금지어", "PROHIBITED_CONTENT": "금지 내용", "SPII": "개인 정보", "LANGUAGE": "언어",
            "OTHER": "공급자 사정", "MALFORMED_FUNCTION_CALL": "형식 오류"}


def _json(result: LlmResult, what: str) -> dict:
    """The answer's JSON object. Tolerates a ``` fence or a sentence around it. When it cannot be read, the error
    says why — cut off at the output limit, stopped by the provider, empty, or not JSON — and keeps how the answer
    ended and its last characters in ``details`` so a failure can be looked into."""
    raw = result.text or ""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(text)
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"): text.rindex("}") + 1])
    data = None
    for c in candidates:
        try:
            data = json.loads(c)
            break
        except ValueError:
            continue
    if isinstance(data, dict):
        return data
    finish = (result.finish_reason or "").upper().replace("FINISHREASON.", "")
    details = {"finish_reason": result.finish_reason, "chars": len(raw), "tail": raw[-300:], "what": what}
    if data is not None:
        raise AiError("AI_BAD_OUTPUT", f"AI 응답({what})의 형식이 맞지 않습니다 (JSON 객체가 아님).", "다시 시도하세요.", details)
    if finish in _CUT:
        message = f"AI 응답({what})이 출력 길이 제한에 걸려 중간에 잘렸습니다 (응답 {len(raw):,}자)."
        hint = "설정에서 최대 출력 토큰을 늘리거나, 부품이 적어지게 보드를 나누세요."
    elif finish in _BLOCKED:
        message = f"AI 공급자가 응답({what})을 중간에 막았습니다 (이유: {_BLOCKED[finish]})."
        hint = "다시 시도하거나 다른 모델을 고르세요."
    elif not text:
        message = f"AI 응답({what})이 비어 있습니다" + (f" (종료 이유: {result.finish_reason})." if result.finish_reason else ".")
        hint = "다시 시도하세요."
    else:
        message = f"AI 응답({what})이 JSON이 아닙니다 (응답 {len(raw):,}자, 종료 이유: {result.finish_reason or '알 수 없음'})."
        hint = "다시 시도하세요."
    raise AiError("AI_BAD_OUTPUT", message, hint, details)


def _retry_note(exc: AiError) -> str:
    return (f"\n\n[다시 요청] 앞 응답을 읽지 못했습니다: {exc.message}\n"
            "- JSON 객체 하나만 답하세요. 앞뒤에 설명이나 ``` 를 붙이지 마세요.\n"
            "- 짧게 답하세요: citations는 3개 이하로, 데이터시트 문장을 그대로 옮기지 말고 짧게 풀어 쓰세요. "
            "notes·detail·summary는 2문장 이하로.")


def _ask_json(provider: LlmProvider, *, prompt: str, schema: dict, what: str, max_output_tokens: int,
              calls: list | None = None, on_retry: Callable[[str], None] | None = None) -> tuple[dict, LlmResult]:
    """One JSON answer; an unreadable one is asked again once, shorter, before giving up."""
    call = provider.generate(prompt=prompt, json_schema=schema, max_output_tokens=max_output_tokens)
    if calls is not None:
        calls.append(call)
    try:
        return _json(call, what), call
    except AiError as exc:
        if exc.code != "AI_BAD_OUTPUT":
            raise
        if on_retry is not None:
            on_retry(f"AI 응답({what})을 읽지 못해 한 번 더 요청: {exc.message}")
        again = provider.generate(prompt=prompt + _retry_note(exc), json_schema=schema, max_output_tokens=max_output_tokens)
        if calls is not None:
            calls.append(again)
        return _json(again, what), again


def _strs(value) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


# --- prompts --------------------------------------------------------------------------------------

LANGUAGE_RULE = """[언어]
- 사람이 읽는 문장은 모두 한국어로 쓰세요: title, summary, assumptions, questions, role, notes, citations의 text, 검토의 title·detail·suggestion, 요구사항 정리의 모든 항목.
- 부품 기호(R1, U1), 핀 번호, Net 이름, 제조사 품번, KiCad 심볼·풋프린트 이름, 단위(V, mA, Ω, uF)는 원래 표기 그대로 둡니다. 영어 데이터시트를 인용할 때도 설명은 한국어로 옮겨 쓰세요."""

_RULES_COMMON = """[공통 규칙]
- 아래 요구사항만 근거로 삼으세요. 요구사항에 없는 기능을 추가하지 마세요. 정보가 부족하면 가정(assumptions)이나 질문(questions)으로 남기세요.
- 실제로 존재하는 부품만 쓰세요. 제조사 품번(mpn)을 모르면 비워 두세요. 지어내지 마세요.
- 전원 Net 이름에는 전압을 넣으세요(+5V, +3V3, +12V, GND). 이름은 영문·숫자·_·+·-만 씁니다.
- 외부와 연결되는 전원·신호는 커넥터(J)로 보드에 들어오고 나가게 하세요."""
_RULES_COMMON += "\n" + LANGUAGE_RULE


def _requirements_block(inp: DesignInput) -> str:
    parts = [f"[보드] {inp.board_name}", "[요구사항]", inp.requirements.strip() or "(없음)"]
    if inp.spec:
        parts += ["[정리된 요구사항(JSON)]", json.dumps(inp.spec, ensure_ascii=False)]
    if inp.instructions:
        parts += ["[이번 요청]", inp.instructions.strip()]
    if inp.previous:
        parts += ["[현재 설계(가능하면 유지하고 필요한 곳만 고치세요)]", inp.previous]
    if inp.findings:
        parts += ["[반영해야 할 검토 의견]"] + [f"- [{f.get('severity')}] {f.get('title')}: {f.get('detail')} "
                                             f"→ {f.get('suggestion') or ''}" for f in inp.findings]
    return "\n".join(parts)


def _guides_block(text: str, step: str) -> str:
    if not text.strip():
        return ""
    return (f"\n[설계 지침 — 이 조직이 채택한 규칙입니다. 「필수」는 반드시, 「권장」은 이유가 없으면 따르고, 「검토」는 판단하세요.\n"
            f" {step}에서 따른 지침의 id를 guides_applied에 적고, 따르지 못한 「필수」·「권장」 지침은 이유를 assumptions(또는 notes)에 적으세요.]\n"
            + text + "\n")


def plan_prompt(inp: DesignInput) -> str:
    inv = ""
    if inp.inventory:
        # The rating (a capacitor's voltage) and notes (a crystal's load capacitance) decide whether a part fits.
        rows = "\n".join(f"- id={r['id']} | {r.get('category') or '?'} | {r.get('value') or ''} | "
                         f"{r.get('package') or ''} | {r.get('mpn') or ''} | {r.get('manufacturer') or ''} | 재고 {r.get('stock')}"
                         + (f" | 정격 {r['rating']}" if r.get("rating") else "")
                         + (f" | 오차 {r['tolerance']}" if r.get("tolerance") else "")
                         + (f" | 비고 {str(r['notes'])[:60]}" if r.get("notes") else "")
                         for r in inp.inventory)
        inv = ("\n[보유 자재 — 조건이 맞으면 우선 쓰고 inventory_part_id에 id를 적으세요]\n" + rows)
    return f"""당신은 전자회로 설계 엔지니어입니다. 아래 요구사항을 만족하는 회로의 부품 목록을 정하세요.
이 단계에서는 연결을 정하지 않습니다. 다음 단계에서 각 부품의 실제 KiCad 핀 표를 받아 연결합니다.

{_RULES_COMMON}
- 부품마다 ref(R1, C1, U1, J1, D1 …), 역할, 종류(category), 값을 정하세요. 저항·콘덴서 값은 계산 근거를 summary에 적으세요.
- IC·트랜지스터는 제조사 품번(mpn)과, 알면 KiCad 10 심볼(kicad_symbol, 예: Regulator_Linear:AMS1117-3.3)을 적으세요. search_terms에는 심볼을 찾을 검색어(품번, 기능)를 넣으세요.
- 칩 저항·콘덴서·LED의 package는 0402/0603/0805/1206 중에서 쓰세요. 커넥터는 pin_count와 package(2.54mm 헤더, 5.0mm 단자대)를,
  다이오드·IC·트랜지스터는 package(SOD-123, SMA, SOT-23, SOT-23-5, SOIC-8 …)를, 전해 콘덴서는 크기(예: 6.3x5.4)를 적으세요(풋프린트가 됩니다).
- TVS·쇼트키·2소자 다이오드(BAT54S, SM712 등), USB-C 같은 특수 커넥터, PTC 퓨즈는 KiCad 심볼(kicad_symbol)을 적으세요.
  적지 않으면 2핀 일반 심볼로 바뀌어 핀이 맞지 않습니다.
- 데이터시트가 요구하는 주변 부품(디커플링 콘덴서, 풀업 저항 등)을 빠뜨리지 마세요.
- 부품은 {MAX_PARTS}개 이하로 하세요.
- 사람이 검토할 수 있게 부품마다 선택의 근거를 남기세요: reason(왜 이 부품·값인지), basis(requirement = 요구사항에 있는 그대로,
  changed = 요구사항과 다르게 정함, ai = 요구사항에 없어 직접 정함), request(근거가 된 요구사항 문장 원문), calc(값의 계산식).
  요구사항과 다르게 정했다면 basis를 changed로 하고, 무엇이 요구되었고 왜 바꿨는지 reason에 분명히 적으세요.

[검토된 회로 템플릿 — 요구사항의 한 블록에 맞으면 직접 고르지 말고 우선 쓰세요]
- 쓸 템플릿은 templates에 id와 params(name/value)를 적으세요. params는 그 블록이 연결될 Net 이름입니다(예: vin=+12V).
- 템플릿의 부품은 parts에 다시 넣지 마세요. 템플릿에 없는 부품(커넥터, LED 등)만 parts에 넣으세요.
- 맞는 템플릿이 없으면 templates를 비워 두세요. 요구 조건이 템플릿의 「한계」를 넘으면 그 템플릿은 쓰지 마세요.
{catalog_text()}
{_guides_block(inp.guides, "부품 선정")}
{_requirements_block(inp)}{inv}

정해진 JSON 형식으로만 답하세요."""


def _pin_table(part: ResolvedPart) -> str:
    pins = "; ".join(f"{n}={name if name and name != '~' else '-'}({etype})" for n, name, etype in part.pins)
    head = f"- {part.ref} [{part.symbol_id}] {part.value} — {part.role}"
    lines = [head, f"  핀: {pins}"]
    if part.datasheet_notes:
        lines.append("  데이터시트 요약: " + part.datasheet_notes.replace("\n", " / "))
    return "\n".join(lines)


@dataclass
class TemplateBlock:
    """A reviewed template placed in an AI design: its parts and connections are fixed by code."""

    ref: str
    title: str
    params: dict[str, str]
    components: list[Component]
    nets: dict[str, list[tuple[str, str]]]
    ports: list[str]
    note: Note | None = None
    why: str = ""
    """Why the plan chose this template (the AI's words)."""
    sources: str = ""
    """The template's datasheet basis (document - section; ...)."""


def _template_text(blocks: list[TemplateBlock]) -> str:
    if not blocks:
        return ""
    lines = ["[템플릿 — 이미 연결이 끝난 블록. 이 부품들의 핀은 다시 쓰지 마세요]"]
    internal = sorted({n for b in blocks for n in b.nets if n not in b.ports})
    for b in blocks:
        parts = ", ".join(f"{c.reference} {c.value}({c.description or ''})" for c in b.components)
        lines.append(f"- {b.title} [{b.ref}]: {parts}")
        lines.append(f"  단자 Net(다른 부품을 이 블록에 연결하려면 같은 이름을 쓰세요): {', '.join(b.ports)}")
    if internal:
        lines.append(f"- 템플릿 내부 Net(쓰지 마세요): {', '.join(internal)}")
    return "\n".join(lines) + "\n"


def connect_prompt(inp: DesignInput, plan: dict, parts: list[ResolvedPart],
                   blocks: list[TemplateBlock] | None = None) -> str:
    return f"""당신은 전자회로 설계 엔지니어입니다. 부품이 정해졌습니다. 아래 핀 표만 써서 회로의 연결(Net)을 정하세요.

[연결 규칙]
- 핀은 반드시 "기호.핀번호" 형식(예: U1.3, R1.2)으로 쓰고, 아래 핀 표에 있는 번호만 쓰세요. 핀 이름으로 쓰지 마세요.
- 모든 핀은 정확히 한 Net에 들어가거나, 일부러 비워 두는 핀이면 no_connect에 넣어야 합니다. 빠뜨리지 마세요.
- 한 핀을 두 Net에 넣지 마세요. 극성 부품(LED, 다이오드, 극성 콘덴서)은 핀 이름(A/K, +/-)을 보고 방향을 맞추세요.
- 전원(power_in) 핀은 반드시 전원 Net에 연결하세요. 출력(power_out) 핀끼리 연결하지 마세요.
- 데이터시트 요약에 있는 외부 부품 연결을 지키고, 근거가 된 쪽을 citations에 적으세요.
- 값을 바꿔야 하면 values에 적으세요.
- schematic_notes: 회로도에 그대로 적힐 짧은 주석입니다. **이것만은 영어로** 40자 이내로 쓰세요.
  블록마다 하나씩 그 블록의 부품 기호(refs)와 함께 무엇을 하는 부분인지와 핵심 사양을 적고(예: "5V input, <100 mA",
  "Status LED, ~3 mA"), 중요한 신호 Net에는 net 이름과 함께 그 Net이 무엇을 나르는지 적으세요(예: "Timer output, 1 Hz").
  템플릿 블록에는 이미 주석이 있으니 적지 마세요.
- rails: 전원 Net마다 전압(V)과 부하 전류(mA)를 적으세요. 앱이 레귤레이터 발열, 다이오드 전류 같은 기술 검토를 이 값으로
  계산합니다. Net 이름에 전압이 있으면(+5V) voltage는 0, 이름에 전압이 없는 입력(PWR_IN 등)은 실제 전압을 적으세요.
  부하 전류는 요구사항의 값, 없으면 가정한 값(assumptions에 적은 것과 같게), 모르면 0입니다.
  입력 전원처럼 전압이 변하는 Net은 voltage_min·voltage_max(예: 5 V ±5% → 4.75, 5.25, 배터리 3.0~4.2)를 적으세요.
  앱이 드롭아웃은 최저, 정격·발열은 최고 전압으로 계산합니다. 모르면 0입니다.
{_RULES_COMMON}

[설계 요약]
{plan.get('summary', '')}

{_template_text(blocks or [])}
[부품과 핀 표 — (번호=이름(전기적 종류))]
{chr(10).join(_pin_table(p) for p in parts)}
{_guides_block(inp.guides, "연결")}
{_requirements_block(inp)}

정해진 JSON 형식으로만 답하세요."""


def repair_prompt(previous: dict, problems: list[str]) -> str:
    return ("직전 답의 연결에 아래 문제가 있습니다. 같은 핀 표와 규칙으로, 문제를 모두 고친 전체 연결을 다시 답하세요.\n"
            "[문제]\n" + "\n".join(f"- {p}" for p in problems) +
            "\n[직전 답]\n" + json.dumps(previous, ensure_ascii=False))


# --- building -----------------------------------------------------------------------------------

def _net_name(raw: str, used: set[str]) -> str:
    name = re.sub(r"[^A-Za-z0-9_+\-]+", "_", raw.strip()).strip("_")[:60] or "N"
    base, n = name, 1
    while name in used:
        n += 1
        name = f"{base}_{n}"
    used.add(name)
    return name


def build_circuit(parts: list[ResolvedPart], connections: dict, *, project_id: str, title: str,
                  model: str, blocks: list[TemplateBlock] | None = None) -> tuple[CircuitIR | None, list[str]]:
    """Turn the AI's pin lists into an IR. Returns (circuit or None, problems for the AI to fix).

    Template ``blocks`` come in already connected; the AI's nets join their port nets by name."""
    blocks = blocks or []
    problems: list[str] = []
    by_ref = {p.ref: p for p in parts}
    pin_sets = {p.ref: {n for n, _, _ in p.pins} for p in parts}

    def parse(token: str) -> tuple[str, str] | None:
        m = re.fullmatch(r"\s*([A-Za-z]+[0-9]+)\s*[.:]\s*([A-Za-z0-9_+\-]+)\s*", str(token))
        if not m:
            problems.append(f"'{token}': 'U1.3'처럼 기호.핀번호로 써야 합니다.")
            return None
        ref, pin = m.group(1).upper(), m.group(2)
        if ref in template_refs:
            problems.append(f"{ref}: 템플릿 부품입니다. 템플릿 핀은 이미 연결되어 있으니 단자 Net 이름으로 연결하세요.")
            return None
        if ref not in by_ref:
            problems.append(f"{ref}: 부품 목록에 없는 기호입니다.")
            return None
        if pin not in pin_sets[ref]:
            names = {nm.upper(): n for n, nm, _ in by_ref[ref].pins if nm and nm != "~"}
            hint = f" (핀 이름 {pin}은(는) {names[pin.upper()]}번입니다)" if pin.upper() in names else ""
            problems.append(f"{ref}.{pin}: {ref}에 {pin}번 핀이 없습니다{hint}. 핀 번호: "
                            f"{', '.join(sorted(pin_sets[ref], key=natural_key))}")
            return None
        return ref, pin

    owner: dict[tuple[str, str], str] = {}
    nets: list[dict] = []
    used_names: set[str] = set()
    ports = {n for b in blocks for n in b.ports}
    internal = {n for b in blocks for n in b.nets if n not in b.ports}
    template_refs = {c.reference for b in blocks for c in b.components}
    for raw in connections.get("nets") or []:
        if not isinstance(raw, dict):
            continue
        raw_name = re.sub(r"[^A-Za-z0-9_+\-]+", "_", str(raw.get("name") or "N").strip()).strip("_")
        if raw_name in internal:
            problems.append(f"'{raw_name}'은(는) 템플릿 내부 Net 이름이라 쓸 수 없습니다. 다른 이름을 쓰세요.")
            continue
        if raw_name in ports and raw_name in used_names:
            name = raw_name  # two AI nets naming the same template port: one net
        else:
            name = _net_name(str(raw.get("name") or "N"), used_names)
        nodes = []
        for token in raw.get("pins") or []:
            key = parse(token)
            if key is None:
                continue
            if key in owner and owner[key] != name:
                problems.append(f"{key[0]}.{key[1]}: '{owner[key]}'와 '{name}' 두 Net에 들어 있습니다.")
                continue
            if key not in owner:
                owner[key] = name
                nodes.append({"component_id": key[0].lower(), "pin": key[1]})
        if nodes:
            existing = next((x for x in nets if x["name"] == name), None)
            if existing is not None:
                existing["nodes"].extend(nodes)
            else:
                nets.append({"id": f"net-{len(nets) + 1:03d}", "name": name, "nodes": nodes})
    no_connect: set[tuple[str, str]] = set()
    for token in connections.get("no_connect") or []:
        key = parse(token)
        if key is None:
            continue
        if key in owner:
            problems.append(f"{key[0]}.{key[1]}: no_connect인데 '{owner[key]}' Net에도 들어 있습니다.")
        no_connect.add(key)
    # Stacked pins (an MCU's extra GND/VCC pins hidden under the visible one) are one point in KiCad: when the
    # AI connected one of them, the others join the same net instead of counting as missing.
    for p in parts:
        for stack in p.stacks:
            names = {owner[(p.ref, n)] for n in stack if (p.ref, n) in owner}
            if len(names) > 1:
                problems.append(f"{p.ref}.{'/'.join(stack)}: 한 점에 겹친 핀인데 서로 다른 Net({', '.join(sorted(names))})에 "
                                "들어 있습니다. 같은 Net이어야 합니다.")
            elif names:
                (name,) = names
                net = next(x for x in nets if x["name"] == name)
                for n in stack:
                    if (p.ref, n) not in owner:
                        owner[(p.ref, n)] = name
                        no_connect.discard((p.ref, n))
                        net["nodes"].append({"component_id": p.ref.lower(), "pin": n})
    for p in parts:
        for n, nm, etype in p.pins:
            if (p.ref, n) not in owner and (p.ref, n) not in no_connect:
                label = f" ({nm})" if nm and nm != "~" else ""
                problems.append(f"{p.ref}.{n}{label}: 어느 Net에도 없고 no_connect도 아닙니다.")
            if (p.ref, n) in no_connect and etype == "power_in":
                problems.append(f"{p.ref}.{n} ({nm}): 전원 입력 핀은 비워 둘 수 없습니다.")
    values = {str(v.get("ref", "")).upper(): str(v.get("value", "")).strip()
              for v in connections.get("values") or [] if isinstance(v, dict)}
    components = []
    for p in parts:
        components.append({
            "id": p.ref.lower(), "reference": p.ref, "value": values.get(p.ref) or p.value or "?",
            "symbol_id": p.symbol_id, "footprint_id": p.footprint_id, "manufacturer": p.manufacturer,
            "manufacturer_part_number": p.mpn, "specs": p.specs,
            "pins": [{"number": n, "name": nm if nm and nm != "~" else None, "no_connect": (p.ref, n) in no_connect}
                     for n, nm, _ in p.pins],
            "pin_map_evidence": {"status": "unverified", "source": f"KiCad 라이브러리 {p.symbol_id} ({p.how})",
                                 "notes": None},
            "description": p.role[:200] or None,
            "origin": "ai",
        })
    for b in blocks:
        components += [c.model_dump(mode="json") for c in b.components]
        for name, pins in b.nets.items():
            net = next((x for x in nets if x["name"] == name), None)
            if net is None:
                net = {"id": f"net-{len(nets) + 1:03d}", "name": name, "nodes": []}
                nets.append(net)
            net["nodes"] += [{"component_id": c, "pin": p} for c, p in pins]
    notes = _schematic_notes(connections, components, nets, blocks)
    _apply_rails(connections, nets)
    data = {"project_id": project_id, "title": title[:200] or None, "components": components, "nets": nets,
            "notes": notes, "decisions": _decisions(parts, blocks, values),
            "validation": {"status": "unverified", "notes": f"AI 설계안 ({model}). 사람 검토 전."}}
    try:
        circuit = CircuitIR.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        problems.append(f"IR 규칙 위반: {'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
        return None, problems
    for issue in validate_circuit(circuit).errors:
        refs = ", ".join(c.reference for c in circuit.components if c.id in issue.affected_component_ids)
        problems.append(f"{issue.error_code}: {issue.message}" + (f" ({refs})" if refs else ""))
    return circuit, problems


def _decisions(parts: list[ResolvedPart], blocks: list[TemplateBlock], values: dict[str, str]) -> list[dict]:
    """A design decision per AI-chosen part (with the AI's reason, basis and calculation) and per template used. A
    part without a reason says so, so the reviewer sees what was left unexplained."""
    out: list[dict] = []
    for p in parts:
        value = values.get(p.ref) or p.value or "?"
        choice = f"{value}" + (f" ({p.mpn})" if p.mpn and p.mpn != value else "")
        out.append({"id": f"dec-{p.ref.lower()}", "topic": f"{p.ref} {p.role}"[:120] or p.ref, "choice": choice[:300],
                    "by": p.basis if p.basis in ("requirement", "changed", "ai") else "ai",
                    "reason": (p.reason or "AI가 이 선택의 이유를 남기지 않았습니다.")[:2000],
                    "request": p.request[:1000] or None, "calc": p.calc[:1000] or None,
                    "source": f"KiCad 심볼 {p.symbol_id} ({p.how})"[:500], "components": [p.ref.lower()]})
    for b in blocks:
        params = ", ".join(f"{k}={v}" for k, v in b.params.items())
        out.append({"id": f"dec-tpl-{b.components[0].id}"[:64], "topic": f"템플릿: {b.title}"[:120],
                    "choice": f"{b.ref} ({params})"[:300], "by": "template",
                    "reason": (b.why or "AI 설계가 검토된 템플릿을 골랐습니다. 템플릿의 부품과 연결은 코드가 정합니다.")[:2000],
                    "request": None, "calc": None, "source": b.sources[:500] or None,
                    "components": [c.id for c in b.components]})
    return out


def _apply_rails(connections: dict, nets: list[dict]) -> None:
    """The AI's supply voltages and load currents onto the nets, for the technical review. Values out of range or on
    unknown nets are left out (they are inputs to calculations, not connectivity)."""
    by_name = {n["name"]: n for n in nets}
    for raw in connections.get("rails") or []:
        if not isinstance(raw, dict) or str(raw.get("net") or "").strip() not in by_name:
            continue
        net = by_name[str(raw["net"]).strip()]
        try:
            volts, milli = float(raw.get("voltage") or 0), float(raw.get("load_ma") or 0)
        except (TypeError, ValueError):
            continue
        if volts and -1000 <= volts <= 1000:
            net["voltage"] = volts
        if 0 < milli <= 100_000:
            net["load_current"] = round(milli / 1000, 6)
        try:
            lo, hi = float(raw.get("voltage_min") or 0), float(raw.get("voltage_max") or 0)
        except (TypeError, ValueError):
            continue
        nominal = net.get("voltage")
        if nominal is None:
            from circuit_core.datasheets import net_voltage
            nominal = net_voltage(net["name"])
        # A range is kept only when it is consistent (min <= nominal <= max); otherwise it is left out. One side alone
        # is kept too (a 0-10 V input that may see 24 V by mistake has only a maximum worth writing).
        if lo and hi and lo <= hi and (nominal is None or lo <= nominal <= hi):
            net["voltage_min"], net["voltage_max"] = lo, hi
        elif hi and not lo and (nominal is None or hi >= nominal):
            net["voltage_max"] = hi
        elif lo and not hi and (nominal is None or lo <= nominal):
            net["voltage_min"] = lo


def _schematic_notes(connections: dict, components: list[dict], nets: list[dict],
                     blocks: list[TemplateBlock]) -> list[dict]:
    """English notes for the drawing: each template's caption, then the AI's notes. A note with an unknown part
    or net is left out rather than sent back for repair: notes are text on the drawing, not connectivity."""
    id_of = {c["reference"]: c["id"] for c in components}
    names = {n["name"] for n in nets}
    out: list[dict] = [b.note.model_dump(mode="json") for b in blocks if b.note is not None]
    for raw in connections.get("schematic_notes") or []:
        if not isinstance(raw, dict):
            continue
        text = " ".join(str(raw.get("text") or "").split())[:120]
        net = str(raw.get("net") or "").strip()
        refs = [str(r).strip().upper() for r in raw.get("refs") or []]
        if not text:
            continue
        if net:
            if net not in names:
                continue
            note = {"text": text, "components": [], "net": net}
        else:
            ids = [id_of[r] for r in refs if r in id_of]
            if not ids:
                continue
            note = {"text": text, "components": ids, "net": None}
        out.append({"id": f"note-{len(out) + 1:03d}", **note})
    return out


# --- the pipeline --------------------------------------------------------------------------------

def place_templates(plan: dict, pins_of: PinsOf | None, taken_refs: set[str]) -> tuple[list[TemplateBlock], list[str]]:
    """Expand the templates the plan chose into fixed blocks. Returns (blocks, problems)."""
    blocks: list[TemplateBlock] = []
    problems: list[str] = []
    reserved = (set(), set(taken_refs), set())
    for raw in plan.get("templates") or []:
        if not isinstance(raw, dict):
            continue
        t = TEMPLATES.get(str(raw.get("id", "")))
        if t is None:
            problems.append(f"템플릿 '{raw.get('id')}'은(는) 없습니다.")
            continue
        if pins_of is None:
            problems.append(f"{t.title}: KiCad 라이브러리가 없어 템플릿을 쓸 수 없습니다.")
            continue
        values = {str(p.get("name")): str(p.get("value")) for p in raw.get("params") or [] if isinstance(p, dict)}
        values = {k: v for k, v in values.items() if k in {p.name for p in t.params}}
        try:
            inst = instantiate(t, values, None, pins_of, reserved=reserved)
        except TemplateError as exc:
            problems.append(str(exc))
            continue
        blocks.append(TemplateBlock(t.ref, t.title, inst.params, inst.components, inst.nets,
                                    [inst.params[p.name] for p in t.params if p.kind == "net"], inst.note,
                                    why=str(raw.get("why") or "")[:1500], sources=template_sources(t)))
    return blocks, problems


def design_circuit(provider: LlmProvider, inp: DesignInput, resolve: Resolver, *, project_id: str,
                   max_repairs: int = 2, max_output_tokens: int = 32768,
                   pins_of: PinsOf | None = None, on_step: Callable[[str, str], None] | None = None,
                   plan: dict | None = None,
                   guides_for_parts: Callable[[list[ResolvedPart], list[TemplateBlock]], str] | None = None,
                   after_parts: Callable[[list[ResolvedPart], list[TemplateBlock]], None] | None = None) -> DesignResult:
    """``on_step(step, text)`` reports progress: step "parts" (choosing parts) then "pins" (connecting them).
    ``plan`` continues from a parts plan already made (after the person chose alternatives) without asking again.
    ``after_parts`` runs once every part is resolved and before the pins are connected: the caller may fetch what
    the connection should know about the parts (their datasheet summaries, set on ``datasheet_notes``)."""
    step = on_step or (lambda _step, _text: None)
    if plan is None:
        step("parts", "AI에 부품 계획 요청")
        calls: list[LlmResult] = []
        plan, _ = _ask_json(provider, prompt=plan_prompt(inp), schema=PLAN_SCHEMA, what="부품 계획",
                            max_output_tokens=max_output_tokens, calls=calls, on_retry=lambda t: step("parts", t))
        result = DesignResult(circuit=None, plan=plan, calls=calls)
    else:
        step("parts", "고른 대안으로 부품 계획을 고쳐 이어서 설계")
        result = DesignResult(circuit=None, plan=plan, calls=[])
    raw_parts = [p for p in plan.get("parts") or [] if isinstance(p, dict)]
    if not raw_parts and not plan.get("templates"):
        raise AiError("AI_BAD_OUTPUT", "AI가 부품을 하나도 정하지 않았습니다.", "요구사항을 더 구체적으로 적어 보세요.")
    if len(raw_parts) > MAX_PARTS:
        raise AiError("AI_BAD_OUTPUT", f"부품이 {len(raw_parts)}개로 너무 많습니다(최대 {MAX_PARTS}).",
                      "보드를 나누거나 요구사항을 줄여 보세요.")
    seen: set[str] = set()
    for raw in raw_parts:
        ref = str(raw.get("ref", "")).strip().upper()
        if not re.fullmatch(r"[A-Z]+[0-9]+", ref) or ref in seen:
            result.unresolved.append(f"{ref or '?'}: 기호가 잘못되었거나 중복입니다.")
            continue
        seen.add(ref)
        resolved, message = resolve({**raw, "ref": ref})
        if resolved is None:
            result.unresolved.append(f"{ref} ({raw.get('role', '')}, {raw.get('mpn') or raw.get('value', '')}): {message}")
            result.unresolved_parts.append({**raw, "ref": ref, "why": message})
        else:
            result.parts.append(resolved)
    blocks, template_problems = place_templates(plan, pins_of, seen)
    result.templates = blocks
    result.unresolved += template_problems
    if result.unresolved:
        result.problems = list(result.unresolved)
        step("parts", f"부품 {len(result.unresolved)}개를 KiCad 심볼로 찾지 못함")
        return result
    used = f", 검토된 템플릿 {', '.join(b.title for b in blocks)}" if blocks else ""
    step("parts", f"부품 {len(result.parts) + sum(len(b.components) for b in blocks)}개 선택{used}")
    if after_parts is not None:
        after_parts(result.parts, blocks)
    if guides_for_parts is not None:
        # The parts decide which guides apply (an MCU brings its decoupling rules): connect with those.
        chosen = guides_for_parts(result.parts, blocks)
        if chosen.strip() and chosen != inp.guides:
            inp = DesignInput(**{**inp.__dict__, "guides": chosen})
            step("parts", "고른 부품에 맞는 설계 지침으로 연결합니다")

    prompt = connect_prompt(inp, plan, result.parts, blocks)
    title = str(plan.get("title") or "AI 설계안")
    for attempt in range(max_repairs + 1):
        step("pins", "KiCad 심볼의 핀 표로 연결 요청" if attempt == 0 else f"연결 문제를 고쳐 다시 요청 ({attempt}회째)")
        connections, call = _ask_json(provider, prompt=prompt, schema=CONNECT_SCHEMA, what="연결",
                                      max_output_tokens=max_output_tokens, calls=result.calls,
                                      on_retry=lambda t: step("pins", t))
        result.attempts = attempt + 1
        result.connections = connections
        circuit, problems = build_circuit(result.parts, connections, project_id=project_id, title=title,
                                          model=call.model, blocks=blocks)
        result.circuit, result.problems = circuit, problems
        if not problems:
            fixed = f", {attempt}회 고침" if attempt else ""
            step("pins", f"연결 완료: Net {len(circuit.nets)}개{fixed}" if circuit else "연결 완료")
            break
        step("pins", f"연결 문제 {len(problems)}개")
        prompt = connect_prompt(inp, plan, result.parts, blocks) + "\n\n" + repair_prompt(connections, problems)
    return result


ALTERNATIVES_SCHEMA = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "kind": {"type": "string", "enum": ["replace", "approach"]},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "part": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"}, "value": {"type": "string"}, "mpn": {"type": "string"},
                            "manufacturer": {"type": "string"}, "package": {"type": "string"},
                            "kicad_symbol": {"type": "string"}, "pin_count": {"type": "integer"},
                            "search_terms": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "required": ["ref", "kind", "title", "detail"],
            },
        },
    },
    "required": ["options"],
}


def alternatives_prompt(inp: DesignInput, plan: dict, unresolved: list[dict], library: dict[str, list[str]]) -> str:
    lines = []
    for u in unresolved:
        lines.append(f"- {u['ref']} ({u.get('role', '')}): {u.get('value', '')} {u.get('mpn') or ''} — 찾지 못한 이유: {u.get('why', '')}")
        hits = library.get(u["ref"]) or []
        lines.append("  라이브러리에 있는 비슷한 심볼: " + ("; ".join(hits) if hits else "(없음)"))
    others = ", ".join(f"{p.get('ref')} {p.get('value')}" for p in plan.get("parts") or []
                       if isinstance(p, dict) and p.get("ref") not in {u["ref"] for u in unresolved})
    return f"""당신은 회로 설계를 돕는 엔지니어입니다. 아래 부품은 이 컴퓨터의 KiCad 라이브러리에 심볼이 없어 설계를 이어갈 수 없습니다.
사람이 고를 수 있게, 부품마다 대안을 2~4개 제시하세요.

[대안의 종류]
- replace: 다른 부품으로 대체. 가능하면 아래 「라이브러리에 있는 비슷한 심볼」 중에서 고르고, part.kicad_symbol에 그 이름을 그대로 적으세요.
  요구사항의 기능·전압·인터페이스를 만족해야 하고, 바꾸면 무엇이 달라지는지(성능·핀 수·주변 회로) detail에 적으세요.
- approach: 다른 방법으로 진행. 예: 부품을 별도 모듈(개발 보드 등)에 두고 이 보드에는 헤더만 두기(part.category = "connector",
  part.pin_count = 필요한 신호·전원 핀 수), 기능을 다른 부품 조합으로 나누기, 요구사항의 일부를 바꾸기.
- 지어낸 품번이나 KiCad 심볼 이름을 쓰지 마세요. 확실하지 않으면 approach로 설명만 하세요.

{LANGUAGE_RULE}

[요구사항]
{inp.requirements.strip() or '(없음)'}
{('[추가 요청]' + chr(10) + inp.instructions) if inp.instructions else ''}

[다른 부품 (이미 정해짐)]
{others or '(없음)'}

[심볼을 찾지 못한 부품]
{chr(10).join(lines)}
"""


def suggest_alternatives(provider: LlmProvider, inp: DesignInput, plan: dict, unresolved: list[dict],
                         library: dict[str, list[str]], *, max_output_tokens: int = 8192) -> tuple[list[dict], LlmResult]:
    """The AI's alternatives for parts with no KiCad symbol. The caller checks each one against the library."""
    data, call = _ask_json(provider, prompt=alternatives_prompt(inp, plan, unresolved, library), schema=ALTERNATIVES_SCHEMA,
                           what="대안", max_output_tokens=max_output_tokens)
    refs = {u["ref"] for u in unresolved}
    options = [o for o in data.get("options") or []
               if isinstance(o, dict) and str(o.get("ref", "")).upper() in refs and o.get("kind") in ("replace", "approach")]
    return options, call


# --- revisions as edits of the current circuit ---------------------------------------------------------------------

EDIT_ACTIONS = ("set_value", "add_part", "remove_part", "connect", "disconnect", "no_connect", "set_note")

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _KO,
        "edits": {"type": "array", "items": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(EDIT_ACTIONS)},
            "ref": _STR, "value": _STR, "category": _STR, "role": _KO, "mpn": _STR, "package": _STR,
            "kicad_symbol": _STR, "pin_count": {"type": "integer"}, "net": _STR, "pins": _STRS,
            "note": {"type": "string", "description": "set_note: 고칠 회로도 주석 번호 (N1 …)"},
            "text": {"type": "string", "description": "set_note: 새 주석, English, at most 40 characters; 빈 값이면 지움"},
            "why": {"type": "string", "description": "어느 의견(F 번호) 때문인지와 이유, 한국어로"},
        }, "required": ["action", "why"]}},
        "addressed": {"type": "array", "items": _STR, "description": "고친 의견 번호 (F1 …)"},
        "not_addressed": {"type": "array", "items": {"type": "object", "properties": {"id": _STR, "why": _KO},
                                                     "required": ["id", "why"]}},
        "guides_applied": {"type": "array", "items": _STR, "description": "편집에서 따른 설계 지침 id"},
    },
    "required": ["summary", "edits", "addressed"],
}


def _circuit_pins(circuit: CircuitIR) -> str:
    lines = ["[부품과 핀 표]"]
    for c in sorted(circuit.components, key=lambda c: natural_key(c.reference)):
        pins = ", ".join(f"{p.number}={p.name}" if p.name else p.number for p in c.pins)
        nc = [p.number for p in c.pins if p.no_connect]
        mpn = f" 품번 {c.manufacturer_part_number}" if c.manufacturer_part_number else ""
        fp = f" · 풋프린트 {c.footprint_id.split(':')[-1]}" if c.footprint_id else " · 풋프린트 없음"
        rated = specs_text(c.specs)
        lines.append(f"- {c.reference} {c.value}{mpn} [{c.symbol_id}] 핀: {pins}" + (f" (NC: {','.join(nc)})" if nc else "")
                     + fp + (f" · {rated}" if rated else ""))
    lines.append("[Net]")
    names = {c.id: c.reference for c in circuit.components}
    for n in circuit.nets:
        lines.append(f"- {n.name}: " + ", ".join(f"{names.get(x.component_id, x.component_id)}.{x.pin}" for x in n.nodes))
    if circuit.notes:
        lines.append("[회로도 주석]")
        for i, nt in enumerate(circuit.notes):
            where = ", ".join(names.get(x, x) for x in nt.components) or (f"Net {nt.net}" if nt.net else "페이지")
            lines.append(f"- N{i + 1} ({where}): {nt.text}")
    return "\n".join(lines)


def edit_prompt(inp: DesignInput, base: CircuitIR, findings: list[dict]) -> str:
    asked = "\n".join(f"- F{i + 1} [{f.get('severity')}] {f.get('title')}: {f.get('detail') or ''}"
                      + (f" → {f['suggestion']}" if f.get("suggestion") else "")
                      + (f" (근거: {f['evidence']})" if f.get("evidence") else "") for i, f in enumerate(findings))
    head = [f"[보드] {inp.board_name}", "[요구사항]", inp.requirements.strip() or "(없음)"]
    if inp.spec:
        head += ["[정리된 요구사항(JSON)]", json.dumps(inp.spec, ensure_ascii=False)]
    if inp.instructions:
        head += ["[이번 요청]", inp.instructions.strip()]
    return f"""당신은 회로 설계 엔지니어입니다. 아래 [현재 회로]에서 [고칠 의견]과 [이번 요청]을 해결하는 데 필요한 곳만 고치세요.
회로를 새로 설계하지 마세요.

[규칙]
- 의견마다 고칠지 정하세요. 고친 의견 번호(F1 …)는 addressed에, 고치지 않는 의견은 not_addressed에 이유와 함께 적으세요.
  근거가 약하거나, 요구사항과 맞지 않거나, 사람이 정해야 하는 의견은 고치지 말고 이유를 적는 것이 맞습니다.
- 의견과 상관없는 부품·값·연결은 바꾸지 마세요. 있는 부품의 기호(ref)도 바꾸지 마세요.
- summary와 why는 사람이 읽습니다. 의견 번호(F1 …) 대신 의견 제목을 짧게 쓰세요. F1 같은 번호는 퓨즈 기호(F1)와 헷갈립니다.
- edits 종류 (모든 edit에 why: 어느 의견 때문인지와 이유):
  · set_value: ref, value, 품번이 있으면 mpn, 풋프린트를 바꿀 때 package — 값·품번·패키지를 바꿈 (예: R1을 4.7k로,
    J4를 package "2.54mm 2x3 헤더"로). 값이 바뀌는데 mpn을 적지 않으면 이전 품번은 지워집니다(이전 값의 품번이므로).
  · add_part: 새 ref(겹치지 않게), category(resistor, capacitor, capacitor_polarized, inductor, led, diode, fuse,
    crystal, connector, ic …), value, role, 알면 mpn·kicad_symbol·package, connector면 pin_count
  · remove_part: ref
  · connect: net, pins — 핀을 그 Net으로 옮김(원래 Net에서는 빠짐). 없는 Net 이름이면 새로 만듦.
  · disconnect: net, pins
  · no_connect: pins — 쓰지 않는 핀
  · set_note: note(N 번호), text — 회로도 주석을 고침(영어, 40자 이내, 빈 text면 지움). 부품·값을 바꿔 그 블록의
    주석(부품 이름, 주파수, 전압 등)이 틀리게 되면 함께 고치세요.
- 핀은 'U1.3'처럼 기호.핀번호로 쓰세요. 있는 부품의 핀 번호는 [현재 회로]의 핀 표를 따르세요.
- 새 부품의 핀: 저항·콘덴서·인덕터·퓨즈 1, 2 / 극성 콘덴서 1(+), 2(−) / 다이오드·LED 1(K), 2(A).
  그 밖의 새 부품은 핀 표를 모르면 연결을 비워 두세요. 다음 답에서 앱이 실제 핀 표를 줍니다.
- 새 부품의 모든 핀은 어느 Net에 연결하거나 no_connect로 하세요. 핀을 옮겨 원래 Net이 비지 않게 하세요.

{LANGUAGE_RULE}

{chr(10).join(head)}

[현재 회로]
{_circuit_pins(base)}

[고칠 의견]
{asked or '(없음 — 이번 요청만)'}
{_guides_block(inp.guides, "수정") if inp.guides.strip() else ''}
- 편집이 위 설계 지침을 어기지 않게 하세요 (예: 새 IC를 넣으면 지침대로 디커플링 콘덴서도 함께 넣기).

정해진 JSON 형식으로만 답하세요."""


_PIN = re.compile(r"\s*([A-Za-z]+[0-9]+)\s*[.:]\s*([A-Za-z0-9_+\-~]+)\s*")


def apply_edits(base: CircuitIR, edits: list[dict], resolve: Resolver) -> tuple[CircuitIR | None, list[str], list[str],
                                                                             dict[str, ResolvedPart]]:
    """Apply the AI's edits to a copy of ``base``: (circuit or None, problems for the AI, what changed, new parts).
    Everything not edited — other parts, nets, notes, decisions, layout, power specs — stays as it was."""
    data = base.model_dump(mode="json")
    comps = {c["reference"].upper(): c for c in data["components"]}
    nets = data["nets"]
    problems: list[str] = []
    changes: list[str] = []
    added: dict[str, ResolvedPart] = {}
    used_ids = {c["id"] for c in data["components"]}
    net_ids = {n["id"] for n in nets}

    def pin(token) -> tuple[dict, str] | None:
        m = _PIN.fullmatch(str(token))
        if not m:
            problems.append(f"'{token}': 'U1.3'처럼 기호.핀번호로 써야 합니다.")
            return None
        ref, number = m.group(1).upper(), m.group(2)
        c = comps.get(ref)
        if c is None:
            problems.append(f"{ref}: 회로에 없는 기호입니다.")
            return None
        numbers = [p["number"] for p in c["pins"]]
        if number not in numbers:
            named = [p["number"] for p in c["pins"] if (p.get("name") or "").upper() == number.upper()]
            if len(named) != 1:
                problems.append(f"{ref}.{number}: {ref}에 {number}번 핀이 없습니다. 핀 번호: {', '.join(numbers)}")
                return None
            number = named[0]
        return c, number

    def unplug(cid: str, number: str) -> str | None:
        was = None
        for n in nets:
            before = len(n["nodes"])
            n["nodes"] = [x for x in n["nodes"] if not (x["component_id"] == cid and x["pin"] == number)]
            if len(n["nodes"]) != before:
                was = n["name"]
        return was

    def set_nc(c: dict, number: str, on: bool) -> None:
        for p in c["pins"]:
            if p["number"] == number:
                p["no_connect"] = on

    def decide(c: dict, topic: str, choice: str, why: str, keep_calc: bool = False) -> None:
        """Record the part's choice (replacing its earlier record, which named the old part). The earlier topic says
        what the part is for, so it stays; the calculation stays only when the value it worked out did not change."""
        did = f"dec-{c['id']}"
        old = next((d for d in data.get("decisions") or [] if d["id"] == did), None) or {}
        entry = {"id": did, "topic": (old.get("topic") or topic)[:120], "choice": choice[:300] or "?", "by": "ai",
                 "reason": (why or "AI 수정안: 이유를 남기지 않았습니다.")[:2000], "components": [c["id"]]}
        if keep_calc and old.get("calc"):
            entry["calc"] = old["calc"]
        data["decisions"] = [d for d in data.get("decisions") or [] if d["id"] != did] + [entry]

    for e in edits:
        action, ref, why = e.get("action"), str(e.get("ref") or "").strip().upper(), str(e.get("why") or "").strip()
        if action == "set_value":
            c, value = comps.get(ref), str(e.get("value") or "").strip()
            if c is not None and not value and (e.get("package") or "mpn" in e):
                value = c["value"]  # only the package or part number changes
            if c is None or not value:
                problems.append(f"set_value {ref or '?'}: 회로에 없는 기호이거나 값이 비었습니다.")
                continue
            before = (c["value"], c.get("manufacturer_part_number"), c.get("footprint_id"))
            if c["value"] != value:
                changes.append(f"{ref} 값 {c['value']} → {value}")
                c["value"] = value
                if "mpn" not in e and c.get("manufacturer_part_number"):
                    # The old part number names the old value: keep it and the BOM orders the wrong part.
                    changes.append(f"{ref} 품번 {c['manufacturer_part_number']} 지움 (값이 바뀜)")
                    c["manufacturer_part_number"] = None
                    c["specs"] = None
            if "mpn" in e:
                mpn = str(e.get("mpn") or "").strip() or None
                if mpn != c.get("manufacturer_part_number"):
                    changes.append(f"{ref} 품번 {c.get('manufacturer_part_number') or '없음'} → {mpn or '없음'}")
                    c["manufacturer_part_number"] = mpn
                    # The ratings belonged to the old part: take the new one's from stock, or none (unknown).
                    c["specs"] = stock_specs(resolve, mpn)
            if str(e.get("package") or "").strip():
                # A new package sets the footprint (a 2x3 header named as 1x6, a part with none).
                category = category_of(c["symbol_id"], c["reference"])
                text = str(e["package"]).strip()
                fp = package_footprint(category, f"{text} {c['symbol_id']}" if category == "switch" else text,
                                       len(c.get("pins") or []))
                if fp is None:
                    problems.append(f"set_value {ref}: 패키지 「{text}」로 풋프린트를 정하지 못했습니다 "
                                    "(0603, SOT-23, SOIC-8, 2.54mm 2x3 헤더, 5.08mm 단자대처럼 적으세요).")
                elif fp != c.get("footprint_id"):
                    changes.append(f"{ref} 풋프린트 {c.get('footprint_id') or '없음'} → {fp}")
                    c["footprint_id"] = fp
            if (c["value"], c.get("manufacturer_part_number"), c.get("footprint_id")) != before:
                # The decision names the part (value and part number): a new part number alone changes it too,
                # or the rationale report keeps naming the part that was replaced.
                mpn = c.get("manufacturer_part_number")
                decide(c, f"{ref} 값", f"{c['value']} ({mpn})" if mpn else c["value"], why,
                       keep_calc=c["value"] == before[0])
        elif action == "set_note":
            notes = data.get("notes") or []
            m = re.fullmatch(r"N?(\d+)", str(e.get("note") or "").strip().upper())
            k = int(m.group(1)) - 1 if m else -1
            if not 0 <= k < len(base.notes):
                problems.append(f"set_note {e.get('note') or '?'}: 없는 주석 번호입니다.")
                continue
            target = next((nt for nt in notes if nt["id"] == base.notes[k].id), None)
            if target is None:
                continue  # its parts were removed with it
            text = " ".join(str(e.get("text") or "").split())[:120]
            if not text:
                data["notes"] = [nt for nt in notes if nt is not target]
                changes.append(f"주석 N{k + 1} 지움: {target['text']}")
            elif text != target["text"]:
                changes.append(f"주석 N{k + 1}: {target['text']} → {text}")
                target["text"] = text
        elif action == "remove_part":
            c = comps.pop(ref, None)
            if c is None:
                problems.append(f"remove_part {ref or '?'}: 회로에 없는 기호입니다.")
                continue
            data["components"] = [x for x in data["components"] if x["id"] != c["id"]]
            for n in nets:
                n["nodes"] = [x for x in n["nodes"] if x["component_id"] != c["id"]]
            data["decisions"] = [d for d in data.get("decisions") or [] if list(d.get("components") or []) != [c["id"]]]
            for d in data["decisions"]:
                d["components"] = [x for x in d.get("components") or [] if x != c["id"]]
            data["notes"] = [nt for nt in data.get("notes") or [] if list(nt.get("components") or []) != [c["id"]]]
            for nt in data["notes"]:
                nt["components"] = [x for x in nt.get("components") or [] if x != c["id"]]
            (data.get("layout") or {}).pop(c["id"], None)
            changes.append(f"{ref} 뺌")
        elif action == "add_part":
            if not re.fullmatch(r"[A-Z]+[0-9]+", ref) or ref in comps:
                problems.append(f"add_part {ref or '?'}: 새 기호가 잘못되었거나 이미 있습니다.")
                continue
            part = {k: e.get(k) for k in ("category", "value", "mpn", "package", "kicad_symbol", "pin_count", "role")
                    if e.get(k) not in (None, "")}
            resolved, message = resolve({**part, "ref": ref, "reason": why, "basis": "ai"})
            if resolved is None:
                problems.append(f"add_part {ref} ({part.get('value') or part.get('mpn') or '?'}): {message}")
                continue
            cid = ref.lower()
            while cid in used_ids:
                cid += "x"
            used_ids.add(cid)
            c = {"id": cid, "reference": ref, "value": resolved.value or "?", "symbol_id": resolved.symbol_id,
                 "footprint_id": resolved.footprint_id, "manufacturer": resolved.manufacturer,
                 "manufacturer_part_number": resolved.mpn, "specs": resolved.specs,
                 "pins": [{"number": n, "name": nm if nm and nm != "~" else None, "no_connect": False}
                          for n, nm, _ in resolved.pins],
                 "pin_map_evidence": {"status": "unverified", "source": f"KiCad 라이브러리 {resolved.symbol_id} ({resolved.how})",
                                      "notes": None},
                 "description": resolved.role[:200] or None, "origin": "ai"}
            data["components"].append(c)
            comps[ref] = c
            added[ref] = resolved
            decide(c, f"{ref} {resolved.role}".strip(), c["value"], why)
            changes.append(f"{ref} {c['value']} 추가")
        elif action in ("connect", "disconnect"):
            name = re.sub(r"[^A-Za-z0-9_+\-]+", "_", str(e.get("net") or "").strip()).strip("_")[:60]
            if not name:
                problems.append(f"{action}: Net 이름이 없습니다.")
                continue
            net = next((n for n in nets if n["name"] == name), None)
            if net is None and action == "disconnect":
                problems.append(f"disconnect: '{name}' Net이 없습니다.")
                continue
            if net is None:
                nid, i = f"net-e{len(nets) + 1:03d}", len(nets) + 1
                while nid in net_ids:
                    i += 1
                    nid = f"net-e{i:03d}"
                net_ids.add(nid)
                net = {"id": nid, "name": name, "nodes": []}
                nets.append(net)
            for token in e.get("pins") or []:
                got = pin(token)
                if got is None:
                    continue
                c, number = got
                if action == "connect":
                    was = unplug(c["id"], number)
                    set_nc(c, number, False)
                    net["nodes"].append({"component_id": c["id"], "pin": number})
                    if was != name:
                        changes.append(f"{c['reference']}.{number} → {name}" + (f" (원래 {was})" if was else ""))
                else:
                    before = len(net["nodes"])
                    net["nodes"] = [x for x in net["nodes"] if not (x["component_id"] == c["id"] and x["pin"] == number)]
                    if len(net["nodes"]) != before:
                        changes.append(f"{c['reference']}.{number} {name}에서 뺌")
        elif action == "no_connect":
            for token in e.get("pins") or []:
                got = pin(token)
                if got is not None:
                    c, number = got
                    unplug(c["id"], number)
                    set_nc(c, number, True)
                    changes.append(f"{c['reference']}.{number} NC")
        else:
            problems.append(f"알 수 없는 edit: {action}")
    data["nets"] = [n for n in nets if n["nodes"]]
    connected = {(x["component_id"], x["pin"]) for n in data["nets"] for x in n["nodes"]}
    was_connected = {(x.component_id, x.pin) for n in base.nets for x in n.nodes}
    for c in data["components"]:
        for p in c["pins"]:
            key = (c["id"], p["number"])
            if key in connected or p.get("no_connect"):
                continue
            label = f" ({p['name']})" if p.get("name") else ""
            if c["reference"].upper() in added:
                problems.append(f"{c['reference']}.{p['number']}{label}: 새 부품의 핀이 어느 Net에도 없고 no_connect도 아닙니다.")
            elif key in was_connected:
                problems.append(f"{c['reference']}.{p['number']}{label}: 원래 연결돼 있던 핀이 이번 수정으로 떴습니다.")
    if problems:
        return None, problems, changes, added
    try:
        circuit = CircuitIR.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        return None, [f"IR 규칙 위반: {'.'.join(str(x) for x in err['loc'])}: {err['msg']}"], changes, added
    errors = [f"{i.error_code}: {i.message}" for i in validate_circuit(circuit).errors]
    return (None if errors else circuit), errors, changes, added


@dataclass
class EditResult:
    circuit: CircuitIR | None
    summary: str = ""
    edits: list[dict] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    addressed: list[dict] = field(default_factory=list)
    not_addressed: list[dict] = field(default_factory=list)
    added: list[ResolvedPart] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    calls: list[LlmResult] = field(default_factory=list)
    attempts: int = 0
    declined: bool = False
    """The AI decided none of the findings should be changed (each with a reason)."""


_FINDING_ID = re.compile(r"(?<![A-Za-z0-9])F(\d{1,2})(?![0-9])")


def name_findings(text: str, by_id: dict[str, dict], refs: set[str] = frozenset()) -> str:
    """The finding numbers the prompt used (F1, F2) written as the findings' titles: the summary and the edit reasons
    are read later by people (the proposal, the design decisions, the rationale report) who never saw those numbers.
    A number that is also a part on the board (fuse F1) is left alone: it may mean the fuse."""
    def title(m: re.Match) -> str:
        f = by_id.get(f"F{m.group(1)}")
        if m.group(0) in refs or not f or not f.get("title"):
            return m.group(0)
        return f"「{f['title']}」"
    return _FINDING_ID.sub(title, text)


def revise_by_edits(provider: LlmProvider, inp: DesignInput, base: CircuitIR, findings: list[dict], resolve: Resolver, *,
                    max_output_tokens: int = 32768, max_repairs: int = 2,
                    on_step: Callable[[str, str], None] | None = None) -> EditResult:
    """A revision that edits the current circuit instead of designing it again: the AI lists edits for the findings it
    accepts (and says why for the ones it does not), the app applies and checks them, and problems go back to the AI
    with the real pin tables of any new parts. ``circuit`` is None when it could not be done this way."""
    step = on_step or (lambda _s, _t: None)
    by_id = {f"F{i + 1}": f for i, f in enumerate(findings)}
    refs = {c.reference.upper() for c in base.components}
    base_prompt = edit_prompt(inp, base, findings)
    prompt = base_prompt
    out = EditResult(circuit=None)
    for attempt in range(max_repairs + 1):
        step("parts", "AI에 고칠 곳만 요청 (회로를 새로 설계하지 않음)" if attempt == 0 else f"편집 문제를 고쳐 다시 요청 ({attempt}회째)")
        answer, _ = _ask_json(provider, prompt=prompt, schema=EDIT_SCHEMA, what="수정 편집",
                              max_output_tokens=max_output_tokens, calls=out.calls, on_retry=lambda t: step("parts", t))
        out.attempts = attempt + 1
        out.summary = name_findings(str(answer.get("summary") or "").strip(), by_id, refs)[:2000]
        out.edits = [{**e, "why": name_findings(str(e.get("why") or ""), by_id, refs)} if e.get("why") else e
                     for e in answer.get("edits") or [] if isinstance(e, dict) and e.get("action") in EDIT_ACTIONS]
        ids = [str(x).strip().upper() for x in answer.get("addressed") or []]
        out.addressed = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
        out.not_addressed = [{"title": by_id[str(x.get("id", "")).upper()].get("title"), "why": str(x.get("why") or "")[:500]}
                             for x in answer.get("not_addressed") or []
                             if isinstance(x, dict) and str(x.get("id", "")).upper() in by_id]
        if not out.edits:
            out.declined = bool(out.not_addressed) and not out.addressed
            out.problems = ["AI가 고칠 곳(edits)을 하나도 적지 않았습니다."]
            step("parts", "AI가 고칠 곳이 없다고 판단" if out.declined else "편집이 비어 있음")
            return out
        step("parts", f"편집 {len(out.edits)}개 · 고친 의견 {len(out.addressed)} · 고치지 않은 의견 {len(out.not_addressed)}")
        circuit, problems, changes, added = apply_edits(base, out.edits, resolve)
        out.changes, out.added, out.problems = changes, list(added.values()), problems
        if circuit is not None:
            out.circuit = circuit
            step("pins", "고친 곳: " + ", ".join(changes[:6]) + (f" 외 {len(changes) - 6}개" if len(changes) > 6 else ""))
            return out
        step("pins", f"편집 문제 {len(problems)}개")
        pins = "\n".join(f"- {r} [{p.symbol_id}] 핀: " + ", ".join(f"{n}={nm}" if nm and nm != "~" else n for n, nm, _ in p.pins)
                         for r, p in added.items())
        prompt = (base_prompt + "\n\n[직전 답에서 생긴 문제 — 모두 고친 편집 목록 전체를 다시 답하세요]\n"
                  + "\n".join(f"- {x}" for x in problems[:30])
                  + (f"\n[새 부품의 실제 핀 표]\n{pins}" if pins else "")
                  + "\n[직전 답]\n" + json.dumps(answer, ensure_ascii=False)[:8000])
    return out


def specs_text(specs) -> str:
    """A part's known ratings in a few words ("정격 50V · C0G · 오차 1%"), empty when none are known."""
    if specs is None:
        return ""
    d = specs if isinstance(specs, dict) else specs.model_dump(exclude_none=True)
    out = []
    if d.get("voltage_rating"):
        out.append(f"정격 {d['voltage_rating']:g}V")
    if d.get("current_rating"):
        out.append(f"{d['current_rating']:g}A")
    if d.get("power_rating"):
        out.append(f"{d['power_rating']:g}W")
    if d.get("dielectric"):
        out.append(str(d["dielectric"]))
    if d.get("tolerance"):
        out.append(f"오차 {d['tolerance']:g}%")
    return " · ".join(out)


def stock_specs(resolve, mpn: str | None) -> dict | None:
    """The stock list's ratings for a part number, through the resolver's inventory (None when it is not stocked)."""
    inventory = getattr(resolve, "inventory", None)
    if not mpn or inventory is None:
        return None
    from circuit_core.inventory import normalize_mpn, specs_from_stock

    key = normalize_mpn(mpn)
    part = next((p for p in inventory.parts if p.mpn and normalize_mpn(p.mpn) == key), None)
    return specs_from_stock(part) if part is not None else None


def circuit_text(circuit: CircuitIR) -> str:
    """A compact, readable description of a circuit for review prompts."""
    lines = ["[부품]"]
    for c in sorted(circuit.components, key=lambda c: natural_key(c.reference)):
        nc = [p.number for p in c.pins if p.no_connect]
        rated = specs_text(c.specs)
        lines.append(f"- {c.reference} {c.value} [{c.symbol_id}] {c.manufacturer_part_number or ''} "
                     f"{c.footprint_id or ''}" + (f" ({rated})" if rated else "") + (f" NC핀 {','.join(nc)}" if nc else ""))
    lines.append("[연결]")
    names = {c.id: c.reference for c in circuit.components}
    pin_names = {(c.id, p.number): p.name for c in circuit.components for p in c.pins}
    for net in circuit.nets:
        nodes = ", ".join(f"{names[n.component_id]}.{n.pin}" + (f"({pin_names[(n.component_id, n.pin)]})"
                                                                  if pin_names.get((n.component_id, n.pin)) else "")
                          for n in net.nodes)
        lines.append(f"- {net.name}: {nodes}")
    return "\n".join(lines)


REVIEW_BASES = ("evidence", "guide", "requirement", "circuit", "judgment")


def review_evidence(circuit: CircuitIR, evidence: str, datasheet_notes: str = "") -> tuple[list[tuple[str, str]], int]:
    """What a review may point at, numbered: E (automated checks, IR issues, datasheet comparison lines), C (the app's
    own calculated review — only the items it flagged) and D (datasheet summary notes). Also returns how many
    calculated checks passed, so the reviewer knows the rest was checked."""
    from circuit_core.engineering import engineering_review

    skip = {"(문제 없음)", "(검사 전)", "(없음)", ""}
    items: list[tuple[str, str]] = []
    for line in evidence.splitlines():
        line = line.strip().lstrip("-").strip()
        if line not in skip:
            items.append((f"E{sum(1 for i, _ in items if i[0] == 'E') + 1}", line))
    passed, n = 0, 0
    for f in engineering_review(circuit):
        if f.severity in ("fail", "warn"):
            n += 1
            label = "실패" if f.severity == "fail" else "주의"
            items.append((f"C{n}", f"[앱 계산 {label}] {f.title} — {f.detail}" + (f" (계산: {f.calc})" if f.calc else "")))
        elif f.severity == "ok":
            passed += 1
    d = 0
    for line in datasheet_notes.splitlines():
        if line.strip():
            d += 1
            items.append((f"D{d}", line.strip()))
    return items, passed


def _squash(text: str) -> str:
    return re.sub(r"[\s\"'“”‘’「」『』<>()\[\].,·:;!?~-]+", "", text).lower()


def verify_finding(f: dict, *, ids: set[str], requirements: str, circuit: CircuitIR, guide_ok: bool) -> bool:
    """Whether a finding points at something real: a numbered evidence item, an adopted guide, a sentence that is in
    the requirements, or a pin or net that is in the circuit."""
    basis, ev = f.get("basis") or "", str(f.get("evidence") or "")
    cited = set(re.findall(r"\b([ECD][0-9]+)\b", ev)) & ids
    if cited:
        return True
    if basis == "guide":
        return guide_ok
    if basis == "requirement":
        quote = _squash(ev)
        return len(quote) >= 6 and quote in _squash(requirements)
    if basis == "circuit":
        pins = {(c.reference.upper(), p.number.upper()) for c in circuit.components for p in c.pins}
        pins |= {(c.reference.upper(), p.name.upper()) for c in circuit.components for p in c.pins if p.name}
        for ref, pin in re.findall(r"\b([A-Za-z]+[0-9]+)\s*[.:]\s*([A-Za-z0-9_+\-]+)", ev):
            if (ref.upper(), pin.upper()) in pins:
                return True
        nets = {n.name for n in circuit.nets}
        return any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", ev) for n in nets if len(n) >= 2)
    return False


def review_basis_rules(passed: int) -> str:
    """The rule every review prompt shares: a 수정 필요/권장 names a basis the app can check."""
    return f"""[근거 원칙 — 근거 없는 지적은 설계를 오히려 망칩니다]
- error와 warning에는 basis와 evidence를 반드시 적으세요.
  · basis="evidence": 아래 [근거 목록]의 번호(E1, C2, D3 …)를 evidence에 적으세요.
  · basis="guide": 지키지 않은 설계 지침 id를 guide에 적으세요.
  · basis="requirement": 지키지 못한 요구사항 문장을 evidence에 그대로 옮겨 적으세요.
  · basis="circuit": [회로]에 실제로 있는 핀·Net을 evidence에 적고(예: "U1.5 VM이 +12V와 연결되지 않음"), 왜 결함인지 detail에 쓰세요.
  · 위 어느 것에도 해당하지 않는 일반 권고·추측·취향은 basis="judgment", severity="info"로 하세요.
- 앱 계산 검토는 {passed}개 항목을 통과했고, 위반은 [근거 목록]의 C 번호뿐입니다. 계산 근거 없이 통과 항목을 뒤집지 마세요.
- 문제가 없으면 findings를 비워 두는 것이 정상입니다. 개수를 채우려고 사소한 개선을 warning으로 올리지 마세요."""


def review_history_blocks(decided: list[dict] | None, previous: list[dict] | None) -> str:
    """What a reviewer must know before raising something again: what a person decided to keep as designed, and
    what the revision under review tried to fix."""
    out = ""
    if decided:
        lines = "\n".join(f"- {d.get('title')}: {str(d.get('reason') or '')[:200]}" for d in decided[:20])
        out += f"""
[사람이 정한 것 — 엔지니어가 의도한 설계로 결정했습니다]
{lines}
- 같은 지적을 다시 적지 마세요. 회로가 바뀌어 그 결정의 이유가 더는 맞지 않을 때만 근거와 함께 적으세요.
"""
    if previous:
        lines = "\n".join(f"- P{k + 1} [{f.get('severity')}] {f.get('title')}: {str(f.get('detail') or '')[:200]}"
                          for k, f in enumerate(previous))
        out += f"""
[이전 회차 의견 — 이번 수정안이 고치려 한 것]
{lines}
- 해결됐으면 다시 적지 마세요. 아직 남았으면 같은 제목으로 다시 적고, evidence에 아직 남은 곳을 적으세요.
- 그 밖의 새 지적은 이번 수정으로 생긴 문제이거나 근거가 확실한 결함일 때만 error·warning으로 하세요.
"""
    return out


def review_material(requirements: str, spec: dict | None, circuit: CircuitIR, items: list[tuple[str, str]],
                    guides: str) -> str:
    """Requirements, the circuit, the numbered evidence and the adopted guides, as every review prompt shows them."""
    listing = "\n".join(f"{i} {t}" for i, t in items) or "(없음)"
    return f"""[요구사항]
{requirements.strip() or '(없음)'}
{('[정리된 요구사항]' + chr(10) + json.dumps(spec, ensure_ascii=False)) if spec else ''}

[회로]
{circuit_text(circuit)}

[근거 목록 — 자동 검사(E), 앱 계산 검토의 위반(C), 데이터시트 요약(D)]
{listing}
{('[설계 지침 — 이 조직이 채택한 규칙. 지키지 않은 것을 찾으면 그 finding의 guide에 지침 id를 적으세요]' + chr(10) + guides) if guides.strip() else ''}"""


def review_findings(data: dict, *, circuit: CircuitIR, items: list[tuple[str, str]], requirements: str,
                    spec: dict | None, guide_ids: set[str] | None) -> list[dict]:
    """The findings of a review answer, cleaned and with ``verified`` set by the code check of each basis."""
    refs = {c.reference for c in circuit.components}
    ids = {i for i, _ in items}
    req_text = requirements + ("\n" + json.dumps(spec, ensure_ascii=False) if spec else "")
    findings = []
    for f in data.get("findings") or []:
        if not isinstance(f, dict) or not str(f.get("title", "")).strip():
            continue
        severity = f.get("severity") if f.get("severity") in ("error", "warning", "info") else "info"
        guide = str(f.get("guide") or "").strip()
        guide = guide if guide_ids and guide in guide_ids else None
        basis = f.get("basis") if f.get("basis") in REVIEW_BASES else ("guide" if guide else "judgment")
        out = {"severity": severity, "title": str(f["title"]).strip()[:200],
               "detail": str(f.get("detail", "")).strip()[:2000],
               "references": [r for r in _strs(f.get("references")) if r.upper() in refs],
               "suggestion": (str(f.get("suggestion")).strip()[:1000] or None) if f.get("suggestion") else None,
               "guide": guide, "basis": basis, "evidence": str(f.get("evidence") or "").strip()[:500] or None}
        out["verified"] = (None if severity == "info" else
                           verify_finding(out, ids=ids, requirements=req_text, circuit=circuit, guide_ok=guide is not None))
        findings.append(out)
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["verified"] is False))
    return findings


def review_circuit(provider: LlmProvider, *, requirements: str, spec: dict | None, circuit: CircuitIR,
                   evidence: str, datasheet_notes: str = "", guides: str = "", guide_ids: set[str] | None = None,
                   max_output_tokens: int = 16384, previous: list[dict] | None = None,
                   decided: list[dict] | None = None) -> tuple[dict, LlmResult]:
    """AI review. Every 수정 필요/권장 must name its basis; code checks it (``verified``). A finding nothing backs is
    kept for people to see but marked, so it does not drive automatic revisions. ``previous``: the findings a
    revision tried to fix, so the review says what is still open instead of starting over."""
    items, passed = review_evidence(circuit, evidence, datasheet_notes)
    prompt = f"""당신은 회로 설계 검토자입니다. 아래 회로가 요구사항을 만족하는지, 전기적으로 문제가 없는지 검토하세요.
회로를 고치지 말고, 지적 사항(findings)만 적으세요.

[검토 기준]
- 요구사항 충족(전원, 인터페이스, 기능), 극성·핀 연결, 전원 전압과 부품 정격, 디커플링·풀업·종단 같은 주변 부품, 보호 회로.
- 확실한 결함은 error(수정 필요), 고치는 것이 좋은 것은 warning(수정 권장), 참고는 info입니다. references에는 관련 부품 기호를 적으세요.

{review_basis_rules(passed)}
{review_history_blocks(decided, previous)}
{LANGUAGE_RULE}

{review_material(requirements, spec, circuit, items, guides)}

정해진 JSON 형식으로만 답하세요."""
    data, result = _ask_json(provider, prompt=prompt, schema=REVIEW_SCHEMA, what="검토", max_output_tokens=max_output_tokens)
    findings = review_findings(data, circuit=circuit, items=items, requirements=requirements, spec=spec,
                               guide_ids=guide_ids)
    return {"summary": str(data.get("summary", "")).strip()[:2000], "findings": findings, "model": result.model,
            "evidence": [{"id": i, "text": t[:300]} for i, t in items], "calc_passed": passed}, result


def counted(findings: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(수정 필요, 수정 권장, 근거 확인 안 됨) — the first two only with a basis that checked out (older reviews,
    without the check, count as before). A finding a person decided to keep as designed (``decided``) is in none."""
    open_ = [f for f in findings if not f.get("decided")]
    needs = [f for f in open_ if f.get("severity") == "error" and f.get("verified") is not False]
    recommended = [f for f in open_ if f.get("severity") == "warning" and f.get("verified") is not False]
    unverified = [f for f in open_ if f.get("severity") in ("error", "warning") and f.get("verified") is False]
    return needs, recommended, unverified


def structure_requirements(provider: LlmProvider, text: str, *, max_output_tokens: int = 8192) -> tuple[dict, LlmResult]:
    prompt = f"""당신은 전자회로 설계 엔지니어입니다. 아래 자유 형식의 요구사항을 정해진 항목으로 정리하세요.
요구사항에 없는 내용은 지어내지 말고, 설계에 꼭 필요한데 빠진 정보는 open_questions에 질문으로 적으세요.
전압·전류는 단위를 붙여 쓰세요(예: 5 V, 500 mA).

{LANGUAGE_RULE}

[요구사항]
{text.strip()}

정해진 JSON 형식으로만 답하세요."""
    data, result = _ask_json(provider, prompt=prompt, schema=SPEC_SCHEMA, what="요구사항 정리",
                             max_output_tokens=max_output_tokens)
    spec = {
        "summary": str(data.get("summary", "")).strip(),
        "functions": _strs(data.get("functions")),
        "power": [{k: str(p.get(k, "")).strip() for k in ("name", "source", "voltage", "current", "notes")}
                  for p in data.get("power") or [] if isinstance(p, dict)],
        "interfaces": [{k: str(i.get(k, "")).strip() for k in ("name", "kind", "details")}
                       for i in data.get("interfaces") or [] if isinstance(i, dict)],
        "parts_preferences": _strs(data.get("parts_preferences")),
        "constraints": _strs(data.get("constraints")),
        "open_questions": _strs(data.get("open_questions")),
    }
    return spec, result


def draft_guide(provider: LlmProvider, *, feedback: str, context: str = "", existing: str = "",
                max_output_tokens: int = 4096) -> tuple[dict, LlmResult]:
    """Rephrase a person's feedback as one design-guide entry (a draft; a person edits and adopts it)."""
    prompt = f"""당신은 회로 설계 팀의 설계 가이드를 정리하는 엔지니어입니다. 아래 피드백을, 다음에 같은 종류의 회로를 그릴 때
적용할 설계 지침 하나로 정리하세요.

[규칙]
- 피드백에 있는 내용만 쓰세요. 없는 수치·데이터시트 쪽 번호·부품을 지어내지 마세요. 값이 필요하지만 피드백에 없으면 rule에 "데이터시트 값"처럼 쓰세요.
- rule은 다음 설계에서 그대로 따를 수 있는 명령문으로 쓰세요. why에는 이유를 적으세요(피드백에 없으면 일반적인 이유를 짧게).
- strength: 지키지 않으면 결함이면 must, 보통 따르면 should, 상황에 따라 판단하면 consider.
- 특정 부품에만 해당하면 parts에 품번·심볼의 일부를, 특정 요구사항에서만 해당하면 keywords에 단어를 적으세요.
- 이미 있는 지침과 같은 내용이면 title을 그 지침과 같게 하고 rule에 무엇이 다른지 적으세요.
{LANGUAGE_RULE}

[피드백]
{feedback.strip()}

[관련 회로 (참고)]
{context or '(없음)'}

[이미 있는 지침 (제목만)]
{existing or '(없음)'}

정해진 JSON 형식으로만 답하세요."""
    result = provider.generate(prompt=prompt, json_schema=GUIDE_DRAFT_SCHEMA, max_output_tokens=max_output_tokens)
    data = _json(result, "지침 초안")
    topics = [t for t in _strs(data.get("topics")) if t in _GUIDE_TOPICS][:6] or ["general"]
    check = str(data.get("check") or "").strip()
    draft = {"title": str(data.get("title", "")).strip()[:120] or "제목 없음",
             "rule": str(data.get("rule", "")).strip()[:2000] or feedback.strip()[:2000],
             "why": str(data.get("why", "")).strip()[:2000],
             "strength": data.get("strength") if data.get("strength") in ("must", "should", "consider") else "should",
             "topics": topics,
             "scope": {"parts": _strs(data.get("parts"))[:10], "keywords": _strs(data.get("keywords"))[:10]},
             "check": check if check in _GUIDE_CHECKS else None}
    return draft, result
