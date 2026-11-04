"""Available-parts inventory: import from CSV/XLSX, match against a circuit, apply to the IR.

"Prefer parts we already have" (보유 자재 우선 사용) works without AI:

1. The user uploads the parts list they keep (Excel or CSV, Korean or English headers).
2. Every component of the circuit is matched: exact manufacturer part number first,
   then category + value + package.
3. Each BOM line is reported as matched (보유), candidate (대체 가능: same value but the
   package differs or is unknown), or missing (미보유); stock is checked against quantity.
4. Applying copies the chosen inventory part's MPN and footprint into the IR as a new
   revision. Values, symbols and connections are never changed.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePath

from pydantic import BaseModel, ConfigDict, Field

from .export import natural_key
from .models import CircuitIR, Component
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


def match_inventory(circuit: CircuitIR, inventory: Inventory, boards: int = 1) -> list[LineMatch]:
    """Match every BOM line of ``circuit``; ``boards`` multiplies the quantity for the stock check."""
    groups: dict[tuple, list[Component]] = defaultdict(list)
    for c in circuit.components:
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
    new_components = []
    for comp in circuit.components:
        part = selected.get(comp.id)
        if part is None:
            new_components.append(comp)
            continue
        update: dict[str, str] = {}
        if part.mpn and part.mpn != comp.manufacturer_part_number:
            update["manufacturer_part_number"] = part.mpn
        if part.manufacturer and part.manufacturer != comp.manufacturer:
            update["manufacturer"] = part.manufacturer
        if part.footprint_id and part.footprint_id != comp.footprint_id:
            update["footprint_id"] = part.footprint_id
        for name, after in update.items():
            changes.append(Change(comp.id, comp.reference, name, getattr(comp, name), after))
        new_components.append(comp.model_copy(update=update) if update else comp)
    if not changes:
        return circuit, []
    updated = circuit.model_copy(update={"components": tuple(new_components)})
    return CircuitIR.model_validate(updated.model_dump()), changes
