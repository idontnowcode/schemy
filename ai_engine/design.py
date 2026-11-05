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
from circuit_core.models import Component, Note
from circuit_core.templates import TEMPLATES, PinsOf, TemplateError, catalog_text, instantiate, template_sources

from .providers import AiError, LlmProvider, LlmResult

CATEGORIES = ["resistor", "capacitor", "capacitor_polarized", "inductor", "led", "diode", "fuse", "connector", "ic",
              "transistor", "crystal", "switch", "other"]
MAX_PARTS = 60

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
        }, "required": ["severity", "title", "detail"]}},
    },
    "required": ["summary", "findings"],
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


def _json(result: LlmResult, what: str) -> dict:
    text = result.text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise AiError("AI_BAD_OUTPUT", f"AI 응답({what})이 JSON이 아닙니다.", "다시 시도하세요.") from exc
    if not isinstance(data, dict):
        raise AiError("AI_BAD_OUTPUT", f"AI 응답({what})의 형식이 맞지 않습니다.")
    return data


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


def plan_prompt(inp: DesignInput) -> str:
    inv = ""
    if inp.inventory:
        rows = "\n".join(f"- id={r['id']} | {r.get('category') or '?'} | {r.get('value') or ''} | "
                         f"{r.get('package') or ''} | {r.get('mpn') or ''} | {r.get('manufacturer') or ''} | 재고 {r.get('stock')}"
                         for r in inp.inventory)
        inv = ("\n[보유 자재 — 조건이 맞으면 우선 쓰고 inventory_part_id에 id를 적으세요]\n" + rows)
    return f"""당신은 전자회로 설계 엔지니어입니다. 아래 요구사항을 만족하는 회로의 부품 목록을 정하세요.
이 단계에서는 연결을 정하지 않습니다. 다음 단계에서 각 부품의 실제 KiCad 핀 표를 받아 연결합니다.

{_RULES_COMMON}
- 부품마다 ref(R1, C1, U1, J1, D1 …), 역할, 종류(category), 값을 정하세요. 저항·콘덴서 값은 계산 근거를 summary에 적으세요.
- IC·트랜지스터는 제조사 품번(mpn)과, 알면 KiCad 10 심볼(kicad_symbol, 예: Regulator_Linear:AMS1117-3.3)을 적으세요. search_terms에는 심볼을 찾을 검색어(품번, 기능)를 넣으세요.
- 칩 저항·콘덴서·LED의 package는 0402/0603/0805/1206 중에서 쓰세요. 커넥터는 pin_count를 적으세요.
- 데이터시트가 요구하는 주변 부품(디커플링 콘덴서, 풀업 저항 등)을 빠뜨리지 마세요.
- 부품은 {MAX_PARTS}개 이하로 하세요.
- 사람이 검토할 수 있게 부품마다 선택의 근거를 남기세요: reason(왜 이 부품·값인지), basis(requirement = 요구사항에 있는 그대로,
  changed = 요구사항과 다르게 정함, ai = 요구사항에 없어 직접 정함), request(근거가 된 요구사항 문장 원문), calc(값의 계산식).
  요구사항과 다르게 정했다면 basis를 changed로 하고, 무엇이 요구되었고 왜 바꿨는지 reason에 분명히 적으세요.

[검토된 회로 템플릿 — 요구사항의 한 블록에 맞으면 직접 고르지 말고 우선 쓰세요]
- 쓸 템플릿은 templates에 id와 params(name/value)를 적으세요. params는 그 블록이 연결될 Net 이름입니다(예: vin=+12V).
- 템플릿의 부품은 parts에 다시 넣지 마세요. 템플릿에 없는 부품(커넥터, LED 등)만 parts에 넣으세요.
- 맞는 템플릿이 없으면 templates를 비워 두세요.
{catalog_text()}

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
{_RULES_COMMON}

[설계 요약]
{plan.get('summary', '')}

{_template_text(blocks or [])}
[부품과 핀 표 — (번호=이름(전기적 종류))]
{chr(10).join(_pin_table(p) for p in parts)}

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
            "manufacturer_part_number": p.mpn,
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
                   pins_of: PinsOf | None = None) -> DesignResult:
    plan_call = provider.generate(prompt=plan_prompt(inp), json_schema=PLAN_SCHEMA, max_output_tokens=max_output_tokens)
    plan = _json(plan_call, "부품 계획")
    result = DesignResult(circuit=None, plan=plan, calls=[plan_call])
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
        else:
            result.parts.append(resolved)
    blocks, template_problems = place_templates(plan, pins_of, seen)
    result.templates = blocks
    result.unresolved += template_problems
    if result.unresolved:
        result.problems = list(result.unresolved)
        return result

    prompt = connect_prompt(inp, plan, result.parts, blocks)
    title = str(plan.get("title") or "AI 설계안")
    for attempt in range(max_repairs + 1):
        call = provider.generate(prompt=prompt, json_schema=CONNECT_SCHEMA, max_output_tokens=max_output_tokens)
        result.calls.append(call)
        result.attempts = attempt + 1
        connections = _json(call, "연결")
        result.connections = connections
        circuit, problems = build_circuit(result.parts, connections, project_id=project_id, title=title,
                                          model=call.model, blocks=blocks)
        result.circuit, result.problems = circuit, problems
        if not problems:
            break
        prompt = connect_prompt(inp, plan, result.parts, blocks) + "\n\n" + repair_prompt(connections, problems)
    return result


def circuit_text(circuit: CircuitIR) -> str:
    """A compact, readable description of a circuit for review prompts."""
    lines = ["[부품]"]
    for c in sorted(circuit.components, key=lambda c: natural_key(c.reference)):
        nc = [p.number for p in c.pins if p.no_connect]
        lines.append(f"- {c.reference} {c.value} [{c.symbol_id}] {c.manufacturer_part_number or ''} "
                     f"{c.footprint_id or ''}" + (f" NC핀 {','.join(nc)}" if nc else ""))
    lines.append("[연결]")
    names = {c.id: c.reference for c in circuit.components}
    pin_names = {(c.id, p.number): p.name for c in circuit.components for p in c.pins}
    for net in circuit.nets:
        nodes = ", ".join(f"{names[n.component_id]}.{n.pin}" + (f"({pin_names[(n.component_id, n.pin)]})"
                                                                  if pin_names.get((n.component_id, n.pin)) else "")
                          for n in net.nodes)
        lines.append(f"- {net.name}: {nodes}")
    return "\n".join(lines)


def review_circuit(provider: LlmProvider, *, requirements: str, spec: dict | None, circuit: CircuitIR,
                   evidence: str, datasheet_notes: str = "", max_output_tokens: int = 16384) -> tuple[dict, LlmResult]:
    prompt = f"""당신은 꼼꼼한 회로 설계 검토자입니다. 아래 회로가 요구사항을 만족하는지, 전기적으로 문제가 없는지 검토하세요.
회로를 고치지 말고, 지적 사항(findings)만 적으세요.

[검토 기준]
- 요구사항 충족(전원, 인터페이스, 기능), 극성·핀 연결, 전원 전압과 부품 정격, 디커플링·풀업·종단 같은 주변 부품, 보호 회로.
- 자동 검사 결과와 데이터시트 대조 결과가 있으면 그것을 근거로 쓰고, 근거가 없는 추측은 severity를 info로 하세요.
- 확실한 결함은 error, 고치는 것이 좋은 것은 warning, 참고는 info입니다. references에는 관련 부품 기호를 적으세요.
- 문제가 없으면 findings를 비워 두세요.

{LANGUAGE_RULE}

[요구사항]
{requirements.strip() or '(없음)'}
{('[정리된 요구사항]' + chr(10) + json.dumps(spec, ensure_ascii=False)) if spec else ''}

[회로]
{circuit_text(circuit)}

[자동 검사 결과]
{evidence or '(없음)'}

[데이터시트 요약]
{datasheet_notes or '(없음)'}

정해진 JSON 형식으로만 답하세요."""
    result = provider.generate(prompt=prompt, json_schema=REVIEW_SCHEMA, max_output_tokens=max_output_tokens)
    data = _json(result, "검토")
    refs = {c.reference for c in circuit.components}
    findings = []
    for f in data.get("findings") or []:
        if not isinstance(f, dict) or not str(f.get("title", "")).strip():
            continue
        severity = f.get("severity") if f.get("severity") in ("error", "warning", "info") else "info"
        findings.append({"severity": severity, "title": str(f["title"]).strip()[:200],
                         "detail": str(f.get("detail", "")).strip()[:2000],
                         "references": [r for r in _strs(f.get("references")) if r.upper() in refs],
                         "suggestion": (str(f.get("suggestion")).strip()[:1000] or None) if f.get("suggestion") else None})
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    return {"summary": str(data.get("summary", "")).strip()[:2000], "findings": findings, "model": result.model}, result


def structure_requirements(provider: LlmProvider, text: str, *, max_output_tokens: int = 8192) -> tuple[dict, LlmResult]:
    prompt = f"""당신은 전자회로 설계 엔지니어입니다. 아래 자유 형식의 요구사항을 정해진 항목으로 정리하세요.
요구사항에 없는 내용은 지어내지 말고, 설계에 꼭 필요한데 빠진 정보는 open_questions에 질문으로 적으세요.
전압·전류는 단위를 붙여 쓰세요(예: 5 V, 500 mA).

{LANGUAGE_RULE}

[요구사항]
{text.strip()}

정해진 JSON 형식으로만 답하세요."""
    result = provider.generate(prompt=prompt, json_schema=SPEC_SCHEMA, max_output_tokens=max_output_tokens)
    data = _json(result, "요구사항 정리")
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
