"""Import "회로도 MD v0.1" (docs/prompts/pdf-schematic-to-markdown.md) into a Circuit IR.

The Markdown is what an AI assistant writes after reading a PDF schematic. Nothing
in it is trusted: every row is checked, pin names are turned into pin numbers only
through the KiCad library, the net table is cross-checked against the pin table,
and anything uncertain leaves the circuit in ``needs_review``.

Severity in the import report:
- ``error``   the IR cannot be built (no circuit is returned);
- ``warning`` the IR is built but something must be checked or fixed before generating;
- ``info``    a recorded decision (e.g. "D1 A -> pin 2 from Device:LED").
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from .md_tables import ImportIssue, Table, first_int, parse_table, read_sections
from .models import CircuitIR, VerificationStatus
from .parts import category_from_text, chip_footprint
from .validation import validate_circuit

FORMAT_NAME = "회로도 MD v0.1"

SECTION_COLUMNS: dict[int, tuple[str, ...]] = {
    1: ("항목", "값"),
    2: ("기호", "값", "종류", "패키지", "제조사 품번", "KiCad 심볼", "핀 수", "페이지", "비고"),
    3: ("기호", "핀 번호", "핀 이름", "Net", "비고"),
    4: ("Net", "연결", "종류", "비고"),
    5: ("기호", "핀 번호", "핀 이름", "근거"),
    6: ("Net", "페이지", "기호/라벨 이름", "비고"),
    7: ("번호", "페이지", "위치", "내용", "후보"),
    8: ("항목", "값"),
}
OPTIONAL_COLUMNS: dict[int, tuple[str, ...]] = {2: ("제조사",)}
"""Added after v0.1 was first published; files without them still import."""
REQUIRED_SECTIONS = (2, 3)

PinLookup = Callable[[str], list[tuple[str, str]] | None]
"""symbol id -> [(pin number, pin name), ...] from the KiCad library, or None if unavailable."""

_SYMBOL_BY_CATEGORY = {
    "resistor": "Device:R",
    "capacitor": "Device:C",
    "capacitor_polarized": "Device:C_Polarized",
    "inductor": "Device:L",
    "led": "Device:LED",
    "diode": "Device:D",
    "fuse": "Device:Fuse",
}
# Device:C_Polarized has unnamed pins; KiCad 10.0.8 draws the "+" mark on the pin 1 side.
_POLARITY_BY_SYMBOL = {"Device:C_Polarized": {"+": "1", "-": "2"}}
_NAME_ALIASES = {
    "a": {"a", "anode", "+", "양극", "애노드", "아노드"},
    "k": {"k", "cathode", "-", "c", "음극", "캐소드"},
}
_REF_RE = re.compile(r"^[A-Z]+[0-9]+$")


@dataclass
class MdImportResult:
    circuit: CircuitIR | None
    issues: list[ImportIssue]
    uncertain: list[dict]
    stats: dict

    @property
    def ok(self) -> bool:
        return self.circuit is not None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "circuit": self.circuit.model_dump(mode="json") if self.circuit else None,
            "issues": [i.to_dict() for i in self.issues],
            "uncertain": self.uncertain,
            "stats": self.stats,
        }


_Table = Table


# --- reading -------------------------------------------------------------------------------

def _read_sections(text: str) -> tuple[str | None, dict[int, tuple[int, list[tuple[int, str]]]]]:
    """Title and, per numbered section, (heading line, [(line number, text), ...])."""
    return read_sections(text, r"^#\s+회로도\s*[:：]\s*(.+)$")


def _parse_table(section: int, lines: list[tuple[int, str]], issues: list[ImportIssue]) -> _Table | None:
    table, problems = parse_table(lines, SECTION_COLUMNS[section], OPTIONAL_COLUMNS.get(section, ()),
                                  keep_dash=("핀 이름",))
    if problems and problems.missing:
        issues.append(ImportIssue("error" if section in REQUIRED_SECTIONS else "warning", "MISSING_COLUMNS",
                                  f"{section}장 표에 열이 없습니다: {', '.join(problems.missing)}", line=problems.line))
    if problems and problems.extra:
        issues.append(ImportIssue("warning", "EXTRA_COLUMNS",
                                  f"{section}장 표의 알 수 없는 열은 무시합니다: {', '.join(problems.extra)}",
                                  line=problems.line))
    return table


# --- helpers ---------------------------------------------------------------------------------

def _norm_pin_name(name: str) -> str:
    s = name.strip().replace("~{", "").replace("}", "").lower()
    s = s.lstrip("/").rstrip("#")
    return s


def _names_match(lib_name: str, given: str) -> bool:
    a, b = _norm_pin_name(lib_name), _norm_pin_name(given)
    if not a or not b or a == "~":
        return False
    if a == b:
        return True
    return b in _NAME_ALIASES.get(a, set())


def _resolve_by_name(symbol_id: str, lib_pins: list[tuple[str, str]], name: str) -> list[str]:
    special = _POLARITY_BY_SYMBOL.get(symbol_id, {})
    if name.strip() in special:
        return [special[name.strip()]]
    return [num for num, lib_name in lib_pins if _names_match(lib_name, name)]


def _net_name(raw: str, used: dict[str, str], issues: list[ImportIssue], line: int) -> str:
    """Make a KiCad/IR-safe net name (A-Z a-z 0-9 _ + -), keeping it unique."""
    if raw in used:
        return used[raw]
    name = re.sub(r"[^A-Za-z0-9_+\-]+", "_", raw).strip("_") or f"N_UNNAMED_{len(used) + 1:03d}"
    base, n = name[:60], 1
    while name in used.values():
        n += 1
        name = f"{base}_{n}"
    if name != raw:
        issues.append(ImportIssue("info", "NET_RENAMED", f"Net '{raw}'의 이름을 '{name}'(으)로 바꿨습니다.", line=line))
    used[raw] = name
    return name


def _stable(text: str, prefix: str, used: set[str]) -> str:
    s = text.lower().replace("+", "p").replace("-", "n") if text.startswith(("+", "-")) else text.lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-._") or "x"
    candidate, n = f"{prefix}{s}"[:60], 1
    while candidate in used:
        n += 1
        candidate = f"{prefix}{s}"[:56] + f"-{n}"
    used.add(candidate)
    return candidate


def _connector_symbol(pin_count: int, hint: str) -> str:
    if re.search(r"2\s*[xX×]|2열|dual|double", hint) and pin_count % 2 == 0:
        return f"Connector_Generic:Conn_02x{pin_count // 2:02d}_Odd_Even"
    return f"Connector_Generic:Conn_01x{pin_count:02d}"


_int = first_int


# --- import ----------------------------------------------------------------------------------

def import_schematic_markdown(text: str, pin_lookup: PinLookup, *, project_id: str = "imported",
                              source_name: str = "schematic.md") -> MdImportResult:
    issues: list[ImportIssue] = []
    title, raw_sections = _read_sections(text)
    tables: dict[int, _Table | None] = {}
    for section in SECTION_COLUMNS:
        if section in raw_sections:
            tables[section] = _parse_table(section, raw_sections[section][1], issues)
    for section in REQUIRED_SECTIONS:
        if tables.get(section) is None:
            issues.append(ImportIssue("error", "MISSING_SECTION", f"{section}장 표를 찾지 못했습니다. "
                                      f"'## {section}.' 제목 아래에 표가 있어야 합니다."))
    if any(i.severity == "error" for i in issues):
        return MdImportResult(None, issues, [], {})

    info = {}
    if tables.get(1):
        info = {tables[1].get(r, "항목"): tables[1].get(r, "값") for _, r in tables[1].rows}
    if info.get("형식") and FORMAT_NAME not in info["형식"]:
        issues.append(ImportIssue("warning", "FORMAT_VERSION", f"형식이 '{info['형식']}'입니다. '{FORMAT_NAME}'로 읽습니다."))
    source = info.get("원본 파일") or source_name

    # Components ------------------------------------------------------------------------
    parts_table, pins_table = tables[2], tables[3]
    components: dict[str, dict] = {}
    review = False
    for line, row in parts_table.rows:
        ref = parts_table.get(row, "기호").upper()
        if not ref:
            continue
        if not _REF_RE.fullmatch(ref):
            issues.append(ImportIssue("error", "INVALID_REFERENCE",
                                      f"부품 기호 '{ref}'를 쓸 수 없습니다 (예: R1, U12).", [ref], line))
            continue
        if ref in components:
            issues.append(ImportIssue("error", "DUPLICATE_REFERENCE", f"부품 기호 {ref}가 두 번 나옵니다.", [ref], line))
            continue
        value = parts_table.get(row, "값") or "?"
        if value == "?":
            review = True
        components[ref] = {
            "line": line, "value": value, "kind": parts_table.get(row, "종류"),
            "package": parts_table.get(row, "패키지"), "mpn": parts_table.get(row, "제조사 품번"),
            "manufacturer": parts_table.get(row, "제조사"),
            "symbol": parts_table.get(row, "KiCad 심볼"), "pin_count": _int(parts_table.get(row, "핀 수")),
            "page": parts_table.get(row, "페이지"), "notes": parts_table.get(row, "비고"),
        }

    rows_by_ref: dict[str, list[tuple[int, list[str]]]] = defaultdict(list)
    for line, row in pins_table.rows:
        ref = pins_table.get(row, "기호").upper()
        if ref:
            rows_by_ref[ref].append((line, row))
    for ref in sorted(set(rows_by_ref) - set(components)):
        issues.append(ImportIssue("error", "UNKNOWN_REFERENCE",
                                  f"3장에 나오는 {ref}가 2장 부품 표에 없습니다.", [ref], rows_by_ref[ref][0][0]))

    # Symbols ---------------------------------------------------------------------------------
    for ref, comp in components.items():
        symbol = comp["symbol"]
        if symbol and not re.fullmatch(r"[A-Za-z0-9_.\-]+:[^:\s]+", symbol):
            issues.append(ImportIssue("warning", "INVALID_SYMBOL",
                                      f"{ref}의 KiCad 심볼 '{symbol}'을(를) 무시하고 종류로 추정합니다.", [ref], comp["line"]))
            symbol = ""
        if not symbol:
            category = category_from_text(comp["kind"])
            if category == "connector":
                count = comp["pin_count"] or len(rows_by_ref.get(ref, []))
                symbol = _connector_symbol(count, f"{comp['kind']} {comp['package']}") if count else ""
            else:
                symbol = _SYMBOL_BY_CATEGORY.get(category or "", "")
            if symbol:
                issues.append(ImportIssue("info", "SYMBOL_INFERRED",
                                          f"{ref}: 종류 '{comp['kind']}' → KiCad 심볼 {symbol}", [ref], comp["line"]))
        comp["symbol_id"] = symbol or None
        comp["footprint_id"] = None
        chip = chip_footprint(category_from_text(comp["kind"]), comp["package"])
        if chip and symbol in _SYMBOL_BY_CATEGORY.values():
            comp["footprint_id"] = chip
            issues.append(ImportIssue("info", "FOOTPRINT_INFERRED",
                                      f"{ref}: 패키지 '{comp['package']}' → 풋프린트 {comp['footprint_id']}", [ref], comp["line"]))

    # Pins and nets -----------------------------------------------------------------------------
    net_names: dict[str, str] = {}
    connections: dict[str, list[tuple[str, str]]] = defaultdict(list)  # net -> [(ref, number)]
    ir_components = []
    used_ids: set[str] = set()
    comp_ids: dict[str, str] = {}
    for ref in components:
        comp_ids[ref] = _stable(ref, "", used_ids)
    for ref, comp in components.items():
        symbol_id = comp["symbol_id"]
        lib_pins = pin_lookup(symbol_id) if symbol_id else None
        if symbol_id and lib_pins is None:
            issues.append(ImportIssue("warning", "SYMBOL_NOT_IN_LIBRARY",
                                      f"{ref}의 심볼 {symbol_id}을(를) KiCad 라이브러리에서 찾지 못해 핀 대조를 못 했습니다.",
                                      [ref], comp["line"]))
        lib_names = dict(lib_pins or [])
        pins: dict[str, str | None] = {}  # number -> name for the IR
        no_connect: set[str] = set()  # Net "NC": the drawing marks the pin as unconnected on purpose
        nets_of_pin: dict[str, str] = {}
        evidence: list[str] = []
        for line, row in rows_by_ref.get(ref, []):
            number = pins_table.get(row, "핀 번호")
            name = pins_table.get(row, "핀 이름")
            net_raw = pins_table.get(row, "Net")
            if number in ("", "?"):
                if lib_pins is None:
                    issues.append(ImportIssue("error", "PIN_NUMBER_UNKNOWN",
                                              f"{ref} 핀 '{name or '?'}': 번호가 없고 라이브러리 심볼이 없어 찾을 수 없습니다.",
                                              [ref], line))
                    continue
                found = _resolve_by_name(symbol_id, lib_pins, name) if name else []
                if len(found) != 1:
                    issues.append(ImportIssue("error", "PIN_NAME_UNRESOLVED",
                                              f"{ref} 핀 이름 '{name or '?'}'을(를) {symbol_id}에서 하나로 찾지 못했습니다.",
                                              [ref], line))
                    continue
                number = found[0]
                evidence.append(f"{name}→{number}")
                issues.append(ImportIssue("info", "PIN_RESOLVED",
                                          f"{ref}: 핀 이름 {name} → 번호 {number} ({symbol_id})", [ref], line))
            ir_name: str | None = None
            if lib_pins is not None:
                if number not in lib_names:
                    issues.append(ImportIssue("warning", "PIN_NOT_IN_SYMBOL",
                                              f"{ref} 핀 {number}이(가) {symbol_id}에 없습니다. 심볼 선택을 확인하세요.",
                                              [ref], line))
                    ir_name = name or None
                else:
                    lib_name = lib_names[number]
                    usable = bool(lib_name) and lib_name != "~"
                    if name and usable and not _names_match(lib_name, name):
                        issues.append(ImportIssue("warning", "PIN_NAME_CONFLICT",
                                                  f"{ref} 핀 {number}: PDF는 '{name}', {symbol_id}는 '{lib_name}'입니다. "
                                                  "극성이나 핀 번호를 원본과 대조하세요.", [ref], line))
                        ir_name = name  # keeps the conflict visible: generation refuses until it is fixed
                    else:
                        ir_name = lib_name if usable else None
            else:
                ir_name = name or None
            if number in pins and nets_of_pin.get(number) != (net_raw or None):
                issues.append(ImportIssue("error", "PIN_IN_TWO_NETS",
                                          f"{ref} 핀 {number}이(가) 서로 다른 Net에 두 번 나옵니다.", [ref], line))
                continue
            pins[number] = ir_name
            if net_raw and net_raw.upper() not in ("NC", "?"):
                net = _net_name(net_raw, net_names, issues, line)
                nets_of_pin[number] = net_raw
                connections[net].append((ref, number))
            elif net_raw.upper() == "NC":
                no_connect.add(number)
            elif net_raw == "?":
                review = True
                nets_of_pin[number] = "?"
        if lib_pins is not None:
            missing = [(n, nm) for n, nm in lib_pins if n not in pins]
            if missing:
                review = True
                listed = ", ".join(f"{n}({nm})" if nm and nm != "~" else n for n, nm in missing[:12])
                issues.append(ImportIssue("warning", "PIN_MISSING_IN_MD",
                                          f"{ref}: {symbol_id}의 핀 {listed}이(가) 3장에 없어 미연결로 추가했습니다.",
                                          [ref], comp["line"]))
                for n, nm in missing:
                    pins[n] = nm if nm and nm != "~" else None
        if not pins:
            issues.append(ImportIssue("error", "NO_PINS", f"{ref}의 핀을 하나도 만들지 못했습니다.", [ref], comp["line"]))
            continue
        if not symbol_id:
            review = True
            issues.append(ImportIssue("warning", "SYMBOL_REQUIRED",
                                      f"{ref}: KiCad 심볼을 정할 수 없습니다(종류 '{comp['kind'] or '?'}'). "
                                      "회로도 생성 전에 symbol_id를 지정하세요.", [ref], comp["line"]))
        evidence_note = ", ".join(evidence) if evidence else None
        ir_components.append({
            "id": comp_ids[ref],
            "reference": ref,
            "value": comp["value"],
            "symbol_id": symbol_id or f"Unknown:{ref}",
            "footprint_id": comp["footprint_id"],
            "manufacturer": comp["manufacturer"] if comp["manufacturer"] not in ("", "?") else None,
            "manufacturer_part_number": comp["mpn"] if comp["mpn"] not in ("", "?") else None,
            "pins": [{"number": n, "name": nm, "no_connect": n in no_connect} for n, nm in pins.items()],
            "pin_map_evidence": {
                "status": "unverified",
                "source": f"PDF 전사: {source}" + (f", p.{comp['page']}" if comp["page"] else ""),
                "notes": f"이름으로 찾은 핀: {evidence_note}" if evidence_note else None,
            },
            "description": "; ".join(x for x in (comp["kind"], comp["package"] and f"패키지 {comp['package']}",
                                                comp["notes"]) if x) or None,
        })

    if any(i.severity == "error" for i in issues):
        return MdImportResult(None, issues, _uncertain(tables.get(7)), {})

    used_net_ids: set[str] = set()
    ir_nets = [{"id": _stable(net, "net-", used_net_ids), "name": net,
                "nodes": [{"component_id": comp_ids[r], "pin": p} for r, p in nodes]}
               for net, nodes in connections.items()]

    # Pages -----------------------------------------------------------------------------------
    pages: dict[str, list[str]] = defaultdict(list)
    for ref, comp in components.items():
        if any(c["reference"] == ref for c in ir_components):
            pages[comp["page"] or ""].append(comp_ids[ref])
    sheets = []
    labelled = [p for p in pages if p]
    if len(labelled) > 1 or (labelled and "" in pages and len(pages) > 1):
        order = sorted(pages, key=lambda p: (_int(p) is None, _int(p) or 0, p))
        used_sheet_ids: set[str] = set()
        for label in order:
            number = _int(label)
            name = f"Page {number}" if number is not None and str(number) == label else (label or "Page ?")
            name = re.sub(r'[/\\:*?"<>|]', "_", name)[:60]
            sheets.append({"id": _stable(f"p{number}" if number is not None else label or "unpaged", "",
                                         used_sheet_ids),
                           "name": name, "components": pages[label]})

    # Cross-checks ------------------------------------------------------------------------------
    _check_net_table(tables.get(4), connections, net_names, components, issues)
    uncertain = _uncertain(tables.get(7))
    _check_self(tables.get(8), components, connections, pins_table, issues)
    if uncertain:
        review = True
    review = review or any(i.severity == "warning" for i in issues)

    notes = [f"{FORMAT_NAME} 가져오기: {source}"]
    if uncertain:
        notes.append(f"불확실 항목 {len(uncertain)}개 — 원본과 대조 필요")
    warnings = sum(1 for i in issues if i.severity == "warning")
    if warnings:
        notes.append(f"확인 필요 {warnings}건")
    data = {
        "project_id": project_id,
        "title": title or info.get("원본 파일") or None,
        "components": ir_components,
        "nets": ir_nets,
        "sheets": sheets,
        "validation": {
            "status": (VerificationStatus.NEEDS_REVIEW if review else VerificationStatus.UNVERIFIED).value,
            "notes": " / ".join(notes),
        },
    }
    try:
        circuit = CircuitIR.model_validate(data)
    except ValidationError as exc:
        issues.append(ImportIssue("error", "IR_INVALID", f"IR을 만들지 못했습니다: {exc.errors()[0]['msg']}"))
        return MdImportResult(None, issues, uncertain, {})
    for problem in validate_circuit(circuit).errors:
        issues.append(ImportIssue("error", problem.error_code, problem.message,
                                  [c.reference for c in circuit.components if c.id in problem.affected_component_ids]))
    if any(i.severity == "error" for i in issues):
        return MdImportResult(None, issues, uncertain, {})
    stats = {"components": len(ir_components), "nets": len(ir_nets), "pin_rows": len(pins_table.rows),
             "pages": max(1, len(sheets)), "uncertain": len(uncertain), "warnings": warnings}
    return MdImportResult(circuit, issues, uncertain, stats)


def _uncertain(table: _Table | None) -> list[dict]:
    if table is None:
        return []
    out = []
    for line, row in table.rows:
        item = {c: table.get(row, c) for c in SECTION_COLUMNS[7]}
        if any(item.values()):
            out.append({**item, "line": line})
    return out


def _check_net_table(table: _Table | None, connections, net_names, components, issues) -> None:
    if table is None:
        issues.append(ImportIssue("warning", "NO_NET_TABLE", "4장 Net 표가 없어 교차 확인을 하지 못했습니다."))
        return
    from_pins = {net: {f"{r}.{p}" for r, p in nodes} for net, nodes in connections.items()}
    pin_names: dict[tuple[str, str], str] = {}
    for net, nodes in connections.items():
        for r, p in nodes:
            pin_names[(r, p)] = net
    seen: set[str] = set()
    for line, row in table.rows:
        raw = table.get(row, "Net")
        if not raw:
            continue
        net = net_names.get(raw, raw)
        seen.add(net)
        tokens = [t.strip() for t in re.split(r"[,，;]", table.get(row, "연결")) if t.strip()]
        listed = set()
        for token in tokens:
            if "." not in token:
                continue
            ref, pin = token.split(".", 1)
            listed.add(f"{ref.strip().upper()}.{pin.strip()}")
        expected = from_pins.get(net, set())
        # Tokens may use pin names (D1.A); compare only numbered tokens strictly.
        numbered = {t for t in listed if t.split(".", 1)[1].isdigit()}
        named = listed - numbered
        if not expected and listed:
            issues.append(ImportIssue("warning", "NET_TABLE_MISMATCH",
                                      f"4장의 Net '{raw}'이(가) 3장 핀 표에 없습니다.", line=line))
        elif numbered - expected or (len(listed) != len(expected) and not named):
            issues.append(ImportIssue("warning", "NET_TABLE_MISMATCH",
                                      f"Net '{raw}': 4장({', '.join(sorted(listed))})과 3장"
                                      f"({', '.join(sorted(expected))})이 다릅니다.", line=line))
    for net in sorted(set(from_pins) - seen):
        issues.append(ImportIssue("warning", "NET_TABLE_MISMATCH", f"Net '{net}'이(가) 4장 Net 표에 없습니다."))


def _check_self(table: _Table | None, components, connections, pins_table: _Table, issues) -> None:
    if table is None:
        return
    claimed = {table.get(r, "항목"): table.get(r, "값") for _, r in table.rows}
    actual = {"부품 수": len(components), "Net 수": len(connections), "핀 연결 행 수": len(pins_table.rows)}
    for key, value in actual.items():
        number = _int(claimed.get(key, ""))
        if number is not None and number != value:
            issues.append(ImportIssue("warning", "SELF_CHECK_MISMATCH",
                                      f"8장 자체 점검의 {key}({number})와 실제({value})가 다릅니다."))
    for key in ("2장의 핀 수 합계 = 3장의 행 수", "3장과 4장의 연결 일치"):
        if claimed.get(key, "").startswith("아니오"):
            issues.append(ImportIssue("warning", "SELF_CHECK_FAILED", f"작성자가 자체 점검에서 '{key}: {claimed[key]}'로 표시했습니다."))
