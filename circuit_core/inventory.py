"""Available-parts inventory: import from CSV/XLSX, match against a circuit, apply to the IR.

"Prefer parts we already have" (보유 자재 우선 사용) works without AI:

1. The user uploads the parts list they keep (Excel or CSV, Korean or English headers).
2. Every component of the circuit is matched: exact manufacturer part number first,
   then category + value + package.
3. Each BOM line is reported as matched (보유), candidate (대체 가능: same value but the
   package differs or is unknown), or missing (미보유); stock is checked against quantity.
4. Applying copies the chosen inventory part's MPN and footprint into the IR as a new
   revision. Values, symbols and connections are never changed.

An AI design does part of this by itself: the AI picks stocked parts for its own parts, and the
parts a reviewed template placed are filled in from the stock list afterwards
(``fill_template_parts``: only 보유 lines, and never a template's footprint).
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field

from .export import natural_key
from .models import CircuitIR, Component, PartSpecs
from .parts import (
    CATEGORY_LABELS,
    category_from_text,
    category_of,
    normalize_mpn,
    package_key,
    packages_match,
    parse_value,
    values_equal,
)

INVENTORY_SCHEMA_VERSION = 1
MAX_ROWS = 50_000

# Header aliases (compared after lower-casing and removing spaces, underscores and dots).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "mpn": ("품번", "제조사품번", "부품번호", "모델명", "partnumber", "mpn", "p/n", "pn", "manufacturerpartnumber",
            "mfrpartnumber", "mfrpn"),
    "manufacturer": ("제조사", "메이커", "manufacturer", "maker", "mfr", "vendor", "브랜드"),
    "category": ("종류", "분류", "카테고리", "품목", "부품종류", "category", "type", "parttype"),
    "value": ("값", "용량", "저항값", "정수", "value", "val"),
    "package": ("패키지", "사이즈", "크기", "규격", "package", "case", "size"),
    "footprint_id": ("풋프린트", "kicad풋프린트", "footprint", "kicadfootprint"),
    "symbol_id": ("심볼", "kicad심볼", "symbol", "kicadsymbol"),
    "rating": ("정격", "전압", "전력", "내압", "rating", "voltage", "power"),
    "tolerance": ("허용오차", "오차", "tolerance", "tol"),
    "stock": ("재고", "수량", "보유수량", "재고수량", "stock", "qty", "quantity", "onhand"),
    "priority": ("우선순위", "순위", "priority"),
    "notes": ("비고", "메모", "설명", "notes", "note", "remark", "remarks", "description", "comment"),
}
COLUMN_LABELS = {
    "mpn": "제조사 품번", "manufacturer": "제조사", "category": "종류", "value": "값", "package": "패키지",
    "footprint_id": "KiCad 풋프린트", "symbol_id": "KiCad 심볼", "rating": "정격", "tolerance": "허용오차",
    "stock": "재고", "priority": "우선순위", "notes": "비고",
}


def _key(header: str) -> str:
    return "".join(ch for ch in header.strip().lower() if ch not in " _.\t-")


_ALIAS_LOOKUP = {_key(alias): field_name for field_name, aliases in COLUMN_ALIASES.items() for alias in aliases}


class InventoryPart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    mpn: str | None = None
    manufacturer: str | None = None
    category: str | None = Field(default=None, description="Canonical key, see parts.CATEGORY_LABELS")
    category_label: str | None = None
    value: str | None = None
    package: str | None = None
    footprint_id: str | None = None
    symbol_id: str | None = None
    rating: str | None = None
    tolerance: str | None = None
    stock: int | None = None
    priority: int = Field(default=100, description="Lower is preferred")
    notes: str | None = None
    source_row: int


class Inventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = INVENTORY_SCHEMA_VERSION
    source_file: str
    imported_at: str
    parts: list[InventoryPart]


@dataclass
class ImportMessage:
    row: int | None
    message: str


@dataclass
class ParsedInventory:
    parts: list[InventoryPart]
    mapped_columns: dict[str, str]
    """Original header -> field name."""
    unknown_columns: list[str]
    warnings: list[ImportMessage] = field(default_factory=list)
    errors: list[ImportMessage] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "part_count": len(self.parts),
            "mapped_columns": self.mapped_columns,
            "unknown_columns": self.unknown_columns,
            "warnings": [m.__dict__ for m in self.warnings],
            "errors": [m.__dict__ for m in self.errors],
            "parts": [p.model_dump() for p in self.parts],
        }


# --- reading tables ---------------------------------------------------------------------

def read_table(filename: str, data: bytes) -> list[list[str]]:
    """Rows of a .csv / .tsv / .txt / .xlsx file as strings (first sheet for Excel)."""
    suffix = PurePath(filename).suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx(data)
    if suffix in (".csv", ".tsv", ".txt"):
        return _read_csv(data)
    raise ValueError(f"지원하지 않는 파일 형식입니다: {suffix or filename} (CSV 또는 XLSX)")


def _read_csv(data: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp949", "latin-1"):  # Korean Excel saves CSV as CP949 by default
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]


def _read_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append(["" if v is None else _cell_text(v) for v in row])
        return rows
    finally:
        wb.close()


def _cell_text(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_inventory(filename: str, data: bytes) -> ParsedInventory:
    try:
        rows = read_table(filename, data)
    except Exception as exc:  # noqa: BLE001 - any reader failure is reported to the user
        return ParsedInventory([], {}, [], errors=[ImportMessage(None, f"파일을 읽지 못했습니다: {exc}")])

    header_index = next((i for i, r in enumerate(rows) if any(c for c in r)), None)
    if header_index is None:
        return ParsedInventory([], {}, [], errors=[ImportMessage(None, "빈 파일입니다.")])
    header = rows[header_index]
    columns: dict[int, str] = {}
    mapped, unknown = {}, []
    for i, name in enumerate(header):
        if not name:
            continue
        field_name = _ALIAS_LOOKUP.get(_key(name))
        if field_name and field_name not in columns.values():
            columns[i] = field_name
            mapped[name] = field_name
        else:
            unknown.append(name)
    result = ParsedInventory([], mapped, unknown)
    if not ({"value", "mpn"} & set(columns.values())):
        result.errors.append(ImportMessage(
            header_index + 1, "'값' 또는 '품번' 열이 필요합니다. 첫 줄에 열 이름이 있는지 확인하세요."))
        return result

    data_rows = rows[header_index + 1:]
    if len(data_rows) > MAX_ROWS:
        result.errors.append(ImportMessage(None, f"행이 너무 많습니다 ({len(data_rows)}행, 최대 {MAX_ROWS})."))
        return result
    for offset, row in enumerate(data_rows):
        line = header_index + 2 + offset
        values = {columns[i]: (row[i].strip() if i < len(row) else "") for i in columns}
        if not any(values.values()):
            continue
        if not values.get("value") and not values.get("mpn"):
            result.warnings.append(ImportMessage(line, "값과 품번이 모두 비어 있어 건너뜁니다."))
            continue
        stock = None
        if values.get("stock"):
            try:
                stock = int(float(values["stock"].replace(",", "")))
            except ValueError:
                result.warnings.append(ImportMessage(line, f"재고 '{values['stock']}'를 숫자로 읽지 못했습니다."))
        priority = 100
        if values.get("priority"):
            try:
                priority = int(float(values["priority"]))
            except ValueError:
                message = f"우선순위 '{values['priority']}'를 숫자로 읽지 못했습니다."
                result.warnings.append(ImportMessage(line, message))
        category = category_from_text(values.get("category")) or category_of(values.get("symbol_id"))
        if category is None:
            result.warnings.append(ImportMessage(line, "종류를 알 수 없어 품번으로만 대조합니다."))
        result.parts.append(InventoryPart(
            id=f"inv-{line:05d}",
            mpn=values.get("mpn") or None,
            manufacturer=values.get("manufacturer") or None,
            category=category,
            category_label=values.get("category") or None,
            value=values.get("value") or None,
            package=values.get("package") or None,
            footprint_id=values.get("footprint_id") or None,
            symbol_id=values.get("symbol_id") or None,
            rating=values.get("rating") or None,
            tolerance=values.get("tolerance") or None,
            stock=stock,
            priority=priority,
            notes=values.get("notes") or None,
            source_row=line,
        ))
    if not result.parts and not result.errors:
        result.errors.append(ImportMessage(None, "읽은 자재가 없습니다."))
    return result


# --- matching ---------------------------------------------------------------------------

class MatchStatus(StrEnum):
    MATCHED = "matched"        # 보유
    CANDIDATE = "candidate"    # 대체 가능 (확인 필요)
    MISSING = "missing"        # 미보유


@dataclass
class Candidate:
    part: InventoryPart
    reasons: list[str]
    exact: bool

    def to_dict(self) -> dict:
        return {"part": self.part.model_dump(), "reasons": self.reasons, "exact": self.exact}


@dataclass
class LineMatch:
    """One BOM line (components grouped by value, symbol, footprint, MPN)."""

    component_ids: list[str]
    references: list[str]
    quantity: int
    category: str | None
    value: str
    symbol_id: str
    footprint_id: str | None
    mpn: str | None
    status: MatchStatus
    chosen: InventoryPart | None
    candidates: list[Candidate]
    reasons: list[str]
    stock_short: bool | None
    """True when the chosen part's stock is below the quantity needed; None if stock is unknown."""

    def to_dict(self) -> dict:
        return {
            "component_ids": self.component_ids,
            "references": self.references,
            "quantity": self.quantity,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category or "", None),
            "value": self.value,
            "symbol_id": self.symbol_id,
            "footprint_id": self.footprint_id,
            "mpn": self.mpn,
            "status": self.status.value,
            "chosen": self.chosen.model_dump() if self.chosen else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "reasons": self.reasons,
            "stock_short": self.stock_short,
        }


_DIELECTRICS = {"C0G": "C0G", "NP0": "C0G", "NPO": "C0G", "X7R": "X7R", "X5R": "X5R", "X7S": "X7S", "Y5V": "Y5V"}


def specs_from_stock(part: InventoryPart) -> dict | None:
    """The ratings written in the stock list (정격·오차: "50V C0G", "1%", "40V 3A", "35V") as PartSpecs fields, so a
    stocked part brings what it is rated for into the circuit and the calculation review and the AI reviewer see it.
    Only what the text plainly says; an IC's "40V" (its supply range) is not a part rating and is left out."""
    category = _part_category(part)
    text = " ".join(x for x in (part.rating, part.tolerance) if x)
    if not text or category in (None, "ic", "connector", "switch", "crystal", "transistor"):
        return None
    out: dict = {}
    tol = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if tol and category in ("resistor", "capacitor", "capacitor_polarized", "inductor"):
        out["tolerance"] = float(tol.group(1))
    volt = re.search(r"(\d+(?:\.\d+)?)\s*V(?![a-zA-Z])", text)
    if volt and category in ("capacitor", "capacitor_polarized", "diode", "fuse"):
        out["voltage_rating"] = float(volt.group(1))
    amp = re.search(r"(\d+(?:\.\d+)?)\s*(m?)A(?![a-zA-Z])", text)
    if amp and category in ("diode", "fuse", "inductor"):
        out["current_rating"] = float(amp.group(1)) / (1000 if amp.group(2) else 1)
    watt = re.search(r"(\d+)\s*/\s*(\d+)\s*W|(\d+(?:\.\d+)?)\s*(m?)W(?![a-zA-Z])", text)
    if watt and category == "resistor":
        out["power_rating"] = (int(watt.group(1)) / int(watt.group(2)) if watt.group(1)
                               else float(watt.group(3)) / (1000 if watt.group(4) else 1))
    if category == "capacitor":
        d = next((v for k, v in _DIELECTRICS.items() if re.search(rf"(?i)\b{k}\b", text)), None)
        if d:
            out["dielectric"] = d
    elif category == "capacitor_polarized":
        tantalum = re.search(r"(?i)탄탈|tantal", f"{part.category_label} {text}")
        out["dielectric"] = "tantalum" if tantalum else "electrolytic"
    return {k: v for k, v in out.items() if v} or None


def _part_category(part: InventoryPart) -> str | None:
    return part.category or category_of(part.symbol_id)


def _rank(c: Candidate) -> tuple:
    return (0 if c.exact else 1, c.part.priority, -(c.part.stock or 0), c.part.source_row)


def _match_component(comp: Component, inventory: Inventory) -> tuple[MatchStatus, list[Candidate], list[str]]:
    mpn = normalize_mpn(comp.manufacturer_part_number)
    if mpn:
        exact = [p for p in inventory.parts if normalize_mpn(p.mpn) == mpn]
        if exact:
            return MatchStatus.MATCHED, sorted((Candidate(p, ["제조사 품번 일치"], True) for p in exact), key=_rank), []

    category = category_of(comp.symbol_id, comp.reference)
    if category is None:
        return MatchStatus.MISSING, [], ["부품 종류를 알 수 없어 값으로 대조하지 않았습니다."]
    comp_pkg = package_key(comp.footprint_id)
    candidates: list[Candidate] = []
    for part in inventory.parts:
        if _part_category(part) != category:
            continue
        if category == "connector":
            same_value = True  # connector "values" are names; the pin grid in the package decides
        else:
            same_value = values_equal(comp.value, part.value)
        if not same_value:
            continue
        part_pkg = package_key(part.footprint_id) or package_key(part.package)
        fit = packages_match(comp_pkg, part_pkg)
        if fit is True:
            candidates.append(Candidate(part, ["종류·값·패키지 일치"], True))
        elif fit is None:
            candidates.append(Candidate(part, ["종류·값 일치, 패키지 정보 부족"], False))
        elif category != "connector":
            other = part_pkg.size if part_pkg and part_pkg.size else (part.package or part.footprint_id or "?")
            candidates.append(Candidate(part, [f"종류·값 일치, 패키지 다름 ({other})"], False))
    candidates.sort(key=_rank)
    if candidates and candidates[0].exact:
        return MatchStatus.MATCHED, candidates, []
    if candidates:
        return MatchStatus.CANDIDATE, candidates, ["패키지를 확인하고 직접 고르세요."]
    value_note = "" if parse_value(comp.value) is not None or category == "connector" else " (값을 숫자로 읽지 못함)"
    label = CATEGORY_LABELS.get(category, category)
    return MatchStatus.MISSING, [], [f"보유 자재에 같은 {label} 값이 없습니다{value_note}."]


def match_inventory(circuit: CircuitIR, inventory: Inventory, boards: int = 1, *,
                    only: Collection[str] | None = None) -> list[LineMatch]:
    """Match every BOM line of ``circuit``; ``boards`` multiplies the quantity for the stock check.
    ``only`` limits the lines to the components with these ids."""
    groups: dict[tuple, list[Component]] = defaultdict(list)
    for c in circuit.components:
        if only is not None and c.id not in only:
            continue
        groups[(c.value, c.symbol_id, c.footprint_id or "", c.manufacturer_part_number or "")].append(c)

    lines: list[LineMatch] = []
    for (value, symbol_id, footprint, mpn), comps in groups.items():
        comps = sorted(comps, key=lambda c: natural_key(c.reference))
        status, candidates, reasons = _match_component(comps[0], inventory)
        chosen = candidates[0].part if status is MatchStatus.MATCHED else None
        need = len(comps) * boards
        stock_short = None
        if chosen is not None and chosen.stock is not None:
            stock_short = chosen.stock < need
            if stock_short:
                reasons = [*reasons, f"재고 부족: 필요 {need}, 재고 {chosen.stock}"]
        lines.append(LineMatch(
            component_ids=[c.id for c in comps],
            references=[c.reference for c in comps],
            quantity=len(comps),
            category=category_of(symbol_id, comps[0].reference),
            value=value,
            symbol_id=symbol_id,
            footprint_id=footprint or None,
            mpn=mpn or None,
            status=status,
            chosen=chosen,
            candidates=candidates[:10],
            reasons=reasons,
            stock_short=stock_short,
        ))
    lines.sort(key=lambda m: natural_key(m.references[0]))
    return lines


def summarize(lines: list[LineMatch]) -> dict:
    counts = {s.value: 0 for s in MatchStatus}
    for line in lines:
        counts[line.status.value] += 1
    counts["stock_short"] = sum(1 for line in lines if line.stock_short)
    return counts


# --- applying ---------------------------------------------------------------------------

@dataclass
class Change:
    component_id: str
    reference: str
    field: str
    before: str | None
    after: str | None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class InventoryApplyError(ValueError):
    pass


def apply_inventory(circuit: CircuitIR, inventory: Inventory, *, apply_matched: bool = True,
                    choices: dict[str, str] | None = None) -> tuple[CircuitIR, list[Change]]:
    """Copy MPN/footprint from inventory parts into components.

    ``choices`` maps component id -> inventory part id and overrides automatic matches,
    which is how a user confirms a candidate (대체 가능) part. Values, symbols and
    connections are left untouched.
    """
    choices = dict(choices or {})
    by_id = {p.id: p for p in inventory.parts}
    known_components = {c.id for c in circuit.components}
    for comp_id, part_id in choices.items():
        if comp_id not in known_components:
            raise InventoryApplyError(f"알 수 없는 부품 id: {comp_id}")
        if part_id not in by_id:
            raise InventoryApplyError(f"보유 자재 목록에 없는 항목: {part_id}")

    selected: dict[str, InventoryPart] = {}
    if apply_matched:
        for line in match_inventory(circuit, inventory):
            if line.status is MatchStatus.MATCHED and line.chosen is not None:
                for comp_id in line.component_ids:
                    selected[comp_id] = line.chosen
    for comp_id, part_id in choices.items():
        selected[comp_id] = by_id[part_id]

    changes: list[Change] = []
    updates: dict[str, dict] = {}
    for comp in circuit.components:
        part = selected.get(comp.id)
        update = _stock_update(comp, part) if part is not None else {}
        if update:
            updates[comp.id] = update
            changes += [Change(comp.id, comp.reference, name, getattr(comp, name), update[name])
                        for name in _IDENTITY_FIELDS if name in update]
    return _with_updates(circuit, updates), changes


_IDENTITY_FIELDS = ("manufacturer_part_number", "manufacturer", "footprint_id")


def _stock_update(comp: Component, part: InventoryPart, *, fill_only: bool = False) -> dict:
    """The fields of ``comp`` the stocked ``part`` sets: part number, maker, footprint, and the stock list's ratings
    when the part says nothing yet. ``fill_only`` sets only what the component leaves empty."""
    update: dict = {}
    for name, stocked in zip(_IDENTITY_FIELDS, (part.mpn, part.manufacturer, part.footprint_id), strict=True):
        current = getattr(comp, name)
        if stocked and stocked != current and not (fill_only and current):
            update[name] = stocked
    specs = specs_from_stock(part)
    if specs and comp.specs is None:
        update["specs"] = PartSpecs(**specs)
    return update


def _with_updates(circuit: CircuitIR, updates: dict[str, dict]) -> CircuitIR:
    """``circuit`` with the per-component field updates (the same object when there are none)."""
    if not updates:
        return circuit
    components = tuple(c.model_copy(update=updates[c.id]) if c.id in updates else c for c in circuit.components)
    return CircuitIR.model_validate(circuit.model_copy(update={"components": components}).model_dump())


# --- template parts of an AI design -----------------------------------------------------

def _rating_text(part: InventoryPart) -> str | None:
    return " ".join(x for x in (part.rating, part.tolerance) if x) or None


def _short(footprint_id: str | None) -> str:
    return footprint_id.split(":")[-1] if footprint_id else "없음"


def _package_text(part: InventoryPart) -> str | None:
    """The stocked part's package as its list says it: the package column, else the footprint's name."""
    return part.package or (_short(part.footprint_id) if part.footprint_id else None)


def fill_template_parts(circuit: CircuitIR, inventory: Inventory) -> tuple[CircuitIR, dict]:
    """Match the parts a reviewed template placed (``origin`` 'template:…') to the stock list, as an AI design's own
    parts are when the AI picks a stocked part. A template part the stock list has (보유: the same kind, value and
    package, or the template's own part number) gets the stocked part number, maker and ratings (정격·오차 as
    ``specs``), so the BOM names a part and the calculation review knows what it is rated for.

    A template fixes its parts, so only what it left empty is filled in:

    - its footprint is kept; a stock list that names another one for the part is noted, not followed;
    - a part number the template names is never replaced by another stocked one;
    - the same value in another or an unknown package (대체 가능) is not put in;
    - neither is a stocked part whose ratings the calculation review would fail in this circuit (too little margin,
      a 주의, is put in and flagged by the reviews like any other part).

    What the stock list has and was not put in is reported for a person to confirm in the 자재 tab, with what the
    calculation review would flag if it were used. Template parts someone has since chosen one by one (a decision
    about that single part: an AI revision's edit, an engineer) are left alone, so a stocked part a review had
    replaced does not come back with the next revision.

    Returns (circuit, report): ``report['applied']`` and ``report['unconfirmed']``, one entry per BOM line. The
    circuit is the same object when nothing was filled in."""
    chosen_since = {d.components[0] for d in circuit.decisions if len(d.components) == 1 and d.by != "template"}
    by_id = {c.id: c for c in circuit.components
             if (c.origin or "").startswith("template:") and c.id not in chosen_since}
    report: dict = {"applied": [], "unconfirmed": []}
    updates: dict[str, dict] = {}
    flagged_now: set[tuple[str, str]] | None = None
    for line in match_inventory(circuit, inventory, only=by_id) if by_id else []:
        if not line.candidates:
            continue  # 미보유: nothing the stock list could add
        first = by_id[line.component_ids[0]]
        part = line.chosen
        if flagged_now is None:
            flagged_now = _flagged(circuit)
        candidates = [{"id": c.part.id, "mpn": c.part.mpn, "manufacturer": c.part.manufacturer,
                       "value": c.part.value, "package": _package_text(c.part), "rating": _rating_text(c.part),
                       "stock": c.part.stock, "reason": c.reasons[0],
                       "if_used": _review_if_used(circuit, line.component_ids, c.part, flagged_now)}
                      for c in line.candidates[:3]]
        why = None
        if part is None:
            why = f"패키지가 템플릿 풋프린트({_short(first.footprint_id)})와 같은지 확인되지 않았습니다."
        elif part.mpn and first.manufacturer_part_number and \
                normalize_mpn(part.mpn) != normalize_mpn(first.manufacturer_part_number):
            why = f"템플릿이 정한 품번({first.manufacturer_part_number})과 다른 보유 품번입니다."
        elif any(f["severity"] == "fail" for f in candidates[0]["if_used"]):
            # The match does not compare ratings. A part the app's own calculation fails here (a 16 V capacitor on
            # a 30 V input) is not put in by the app; too little margin (주의) is put in and left to the reviews.
            why = "보유 부품의 정격이 이 회로에서는 부족합니다 (앱 계산 검토 실패)."
        if why is not None:
            report["unconfirmed"].append({
                "references": line.references, "value": line.value, "footprint_id": line.footprint_id,
                "mpn": line.mpn, "why": why, "candidates": candidates})
            continue
        filled: list[str] = []
        fields: set[str] = set()
        for cid in line.component_ids:
            update = _stock_update(by_id[cid], part, fill_only=True)
            if update:
                updates[cid] = update
                filled.append(by_id[cid].reference)
                fields |= set(update)
        if not filled:
            continue  # already the stocked part
        notes = [r for r in line.reasons if r.startswith("재고 부족")]
        stock_pkg = package_key(part.footprint_id) or package_key(part.package)
        if packages_match(package_key(first.footprint_id), stock_pkg) is False:
            # Only a part named by the template's own part number gets here with another package.
            notes.append(f"보유 목록의 패키지({_package_text(part)})가 템플릿 풋프린트({_short(first.footprint_id)})와 "
                         "다릅니다. 풋프린트는 바꾸지 않았습니다.")
        elif first.footprint_id and part.footprint_id and part.footprint_id != first.footprint_id:
            notes.append(f"풋프린트는 템플릿의 {_short(first.footprint_id)} 그대로입니다 "
                         f"(보유 목록: {_short(part.footprint_id)}).")
        report["applied"].append({
            "references": filled, "value": line.value, "mpn": part.mpn, "manufacturer": part.manufacturer,
            "package": _package_text(part), "rating": _rating_text(part), "how": line.candidates[0].reasons[0],
            "filled": [f for f in (*_IDENTITY_FIELDS, "specs") if f in fields], "notes": notes})
    return _with_updates(circuit, updates), report


def _flagged(circuit: CircuitIR) -> set[tuple[str, str]]:
    """(rule, title) of what the calculation review flags (실패·주의) on the circuit."""
    from .engineering import engineering_review

    return {(f.rule, f.title) for f in engineering_review(circuit) if f.severity in ("fail", "warn")}


def _review_if_used(circuit: CircuitIR, component_ids: list[str], part: InventoryPart,
                    flagged_now: set[tuple[str, str]]) -> list[dict]:
    """What the calculation review would newly flag (실패·주의) on these components if they were the stocked
    ``part``: its ratings on the same connections, nothing else changed. So a stocked part that was not put in
    (another package) still tells the person who may confirm it, and the reviewer, whether its ratings fit this
    circuit — a stocked 35 V capacitor on a 30 V input has 1.17 times the voltage, not the 1.5 times asked for."""
    from .engineering import engineering_review

    specs = specs_from_stock(part)
    ids = {cid for cid in component_ids if (c := circuit.component(cid)) is not None and c.specs is None}
    if not specs or not ids:
        return []
    trial = _with_updates(circuit, {cid: {"specs": PartSpecs(**specs)} for cid in ids})
    return [{"severity": f.severity, "title": f.title, "detail": f.detail, "calc": f.calc}
            for f in engineering_review(trial)
            if f.severity in ("fail", "warn") and ids & set(f.components) and (f.rule, f.title) not in flagged_now]


def _stocked_text(part: dict) -> str:
    """'EEU-FR1V681 (Panasonic, 35V, 10x20 래디얼)' from a report entry."""
    detail = ", ".join(str(x) for x in (part.get("manufacturer"), part.get("rating"), part.get("package")) if x)
    return f"{part.get('mpn') or part.get('value') or '?'}" + (f" ({detail})" if detail else "")


def _if_used_text(candidate: dict) -> str:
    flagged = "; ".join(f"{f['title']}" + (f" ({f['calc']})" if f.get("calc") else "")
                        for f in candidate.get("if_used") or [])
    return f" [이 부품을 쓰면 앱 계산 검토: {flagged}]" if flagged else ""


def stock_report_lines(report: dict | None, *, applied: bool = True) -> list[str]:
    """``fill_template_parts``' report as one sentence per BOM line, for the job's record and the AI reviewer's
    evidence. ``applied=False`` leaves out what was put in (the circuit already shows it)."""
    lines = []
    for a in ((report or {}).get("applied") or []) if applied else []:
        lines.append(f"보유 자재 대조: 템플릿 부품 {', '.join(a['references'])} ({a['value']}) → 보유 자재 "
                     f"{_stocked_text(a)} · {a['how']}." + "".join(f" {n}" for n in a.get("notes") or []))
    for u in (report or {}).get("unconfirmed") or []:
        has = "; ".join(f"{_stocked_text(c)} — {c['reason']}{_if_used_text(c)}" for c in u.get("candidates") or [])
        lines.append(f"보유 자재 대조: 템플릿 부품 {', '.join(u['references'])} ({u['value']})에는 보유 자재를 넣지 "
                     f"않았습니다 — {u['why']} 보유 목록: {has}. 쓰려면 자재 탭에서 직접 고릅니다.")
    return lines
