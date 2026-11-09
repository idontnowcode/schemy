"""Resolve the AI's planned parts to symbols that exist in the installed KiCad library (Phase 2b).

The AI may name a KiCad symbol, a maker part number, or only describe the part. Whatever it says,
the symbol used is one this machine's library really has and the generator supports (single unit),
and its pins come from the library. Generic parts use KiCad's generic symbols; stocked parts the AI
picked from the inventory bring their part number, maker and footprint.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from ai_engine.design import ResolvedPart
from circuit_core.datasheets import DatasheetFacts
from circuit_core.inventory import Inventory
from circuit_core.models import Component
from circuit_core.parts import package_footprint
from kicad_adapter import SymbolLibrary, SymbolLibraryError
from kicad_adapter.symbol_index import SymbolIndex

GENERIC_SYMBOLS = {
    "resistor": "Device:R", "capacitor": "Device:C", "capacitor_polarized": "Device:C_Polarized",
    "inductor": "Device:L", "led": "Device:LED", "diode": "Device:D", "fuse": "Device:Fuse",
    "crystal": "Device:Crystal",
}

_index_lock = threading.Lock()
_index_cache: dict[tuple, SymbolIndex] = {}


def symbol_index(symbol_dirs: list[Path], cache_file: Path | None) -> SymbolIndex:
    key = tuple(str(d) for d in symbol_dirs)
    with _index_lock:
        if key not in _index_cache:
            _index_cache[key] = SymbolIndex.build(symbol_dirs, cache_file)
        return _index_cache[key]


def facts_notes(facts: DatasheetFacts | None) -> str | None:
    """Short text of the datasheet summary the AI should follow when connecting."""
    if facts is None:
        return None
    out = []
    for e in facts.external_parts:
        out.append(f"{'필수' if e.requirement == 'required' else '권장'} {e.kind_label} {e.value or ''} "
                   f"{' ~ '.join(e.endpoints)} (p.{e.page or '?'})".strip())
    for r in facts.recommended:
        if r.unit.strip() in ("V", "mV") and r.pins:
            out.append(f"권장 {r.parameter} {','.join(r.pins)}: {r.min}~{r.max} {r.unit} (p.{r.page or '?'})")
    for n in facts.notes[:6]:
        out.append(f"{n.text} (p.{n.page or '?'})")
    return "\n".join(out) or None


class PartResolver:
    def __init__(self, library: SymbolLibrary, index: SymbolIndex, inventory: Inventory | None, datasheets):
        self.library, self.index, self.inventory, self.datasheets = library, index, inventory, datasheets

    def _usable(self, lib_id: str) -> tuple[bool, str]:
        try:
            self.library.get(lib_id)
            return True, ""
        except SymbolLibraryError as exc:
            return False, str(exc)

    def __call__(self, part: dict) -> tuple[ResolvedPart | None, str]:
        ref, category = part["ref"], part.get("category") or "other"
        value = str(part.get("value") or "").strip() or "?"
        mpn = str(part.get("mpn") or "").strip() or None
        maker = str(part.get("manufacturer") or "").strip() or None
        package = str(part.get("package") or "").strip() or None
        footprint = None
        how = []
        inv_id = str(part.get("inventory_part_id") or "").strip()
        if inv_id and self.inventory:
            stocked = next((p for p in self.inventory.parts if p.id == inv_id), None)
            if stocked is not None:
                mpn, maker = stocked.mpn or mpn, stocked.manufacturer or maker
                footprint = stocked.footprint_id
                package = stocked.package or package
                how.append(f"보유 자재 {stocked.mpn or stocked.value}")

        candidates: list[tuple[str, str]] = []
        guess = str(part.get("kicad_symbol") or "").strip()
        named = bool(re.fullmatch(r"[A-Za-z0-9_.\-]+:[^:\s]+", guess))
        count = int(part.get("pin_count") or 0)
        if category in GENERIC_SYMBOLS or category == "connector":
            # A symbol the AI names (a TVS, a dual diode, a USB-C receptacle, a polyfuse) is tried first; then, for
            # diodes and fuses, a library symbol named exactly like the part number; the generic symbol last.
            if named:
                candidates.append((guess, "AI가 제안한 심볼, 라이브러리에서 확인"))
            if category in ("diode", "fuse") and (mpn or value):
                # A library symbol named like the part: exactly, or with a package suffix ("SM712" → SM712_SOT23),
                # or the part number with an ordering suffix ("SMAJ33A-13-F" → SMAJ33A).
                names = {n for n in ((mpn or "").upper(), value.upper(), (mpn or "").upper().split(".")[0],
                                     guess.split(":")[-1].upper() if guess else "") if len(n) >= 4}
                terms = [t for t in (mpn, value, guess.split(":")[-1] if guess else None,
                                     *[str(x) for x in part.get("search_terms") or []]) if t]
                for score, sym in self.index.search(terms, limit=8):
                    name = sym.lib_id.split(":")[-1].upper()
                    if name in names or any(name.startswith(n + "_") or (n.startswith(name) and len(name) >= 5)
                                            for n in names):
                        candidates.append((sym.lib_id, f"품번과 같은 이름의 라이브러리 심볼 (점수 {score})"))
            if category == "diode" and re.search(r"(?i)SOT-?23|SOT-?363|SC-?70|SOT-?323|SOT-?143", package or ""):
                # A diode in a 3+ pin package (a TVS array, a dual diode) has no two-pin generic stand-in: its pads
                # would not match. Stop here so the design offers a choice instead of a wrong symbol.
                if not candidates:
                    return None, (f"{package} 패키지 다이오드인데 맞는 KiCad 심볼을 찾지 못했습니다. 2핀 일반 심볼로는 "
                                  "핀이 맞지 않아 바꾸지 않았습니다. kicad_symbol을 정확히 적어야 합니다.")
                candidates.append(("", ""))  # marker: no generic fallback below
            if category == "fuse" and re.search(r"(?i)PTC|poly|리셋|resettable", f"{value} {part.get('role') or ''}"):
                candidates.append(("Device:Polyfuse", "일반 부품 심볼 (PTC 퓨즈)"))
            if ("", "") in candidates:
                candidates.remove(("", ""))
            elif category in GENERIC_SYMBOLS:
                candidates.append((GENERIC_SYMBOLS[category], "일반 부품 심볼"))
            elif 1 <= count <= 40:
                candidates.append((f"Connector_Generic:Conn_01x{count:02d}", f"{count}핀 일반 커넥터"))
            elif not named:
                return None, "커넥터 핀 수(pin_count)나 KiCad 심볼 이름이 없습니다."
        else:
            if named:
                candidates.append((guess, "AI가 제안한 심볼, 라이브러리에서 확인"))
            terms = [t for t in (mpn, value, guess.split(":")[-1] if guess else None,
                                 *[str(s) for s in part.get("search_terms") or []]) if t]
            for score, sym in self.index.search(terms, limit=6):
                candidates.append((sym.lib_id, f"라이브러리 검색 (점수 {score})"))
        tried = []
        for lib_id, reason in dict.fromkeys(candidates):
            ok, why = self._usable(lib_id)
            if not ok:
                tried.append(f"{lib_id}: {why}")
                continue
            sym = self.library.get(lib_id)
            if footprint is None:
                # A switch's symbol says what kind it is (SW_DIP_x02 with package "SMD" is a 2-way DIP switch).
                text = f"{package or ''} {lib_id}" if category == "switch" else package
                footprint = package_footprint(category, text, len(sym.pins))
            if footprint is None and not lib_id.startswith(("Device:", "Connector_Generic:")):
                default_fp = sym.field("Footprint")  # a specific part's symbol usually names its package
                footprint = default_fp.value if default_fp and default_fp.value else None
            notes = None
            if self.datasheets is not None:
                probe = Component(id=ref.lower(), reference=ref, value=value, symbol_id=lib_id,
                                  manufacturer_part_number=mpn, pins=[{"number": p.number} for p in sym.pins])
                entry = self.datasheets.find(probe, self.library.datasheet_url(lib_id))
                notes = facts_notes(self.datasheets.facts(entry)) if entry else None
            return ResolvedPart(
                ref=ref, role=str(part.get("role") or "")[:200], category=category, value=value, symbol_id=lib_id,
                pins=[(p.number, p.name, p.electrical_type) for p in sym.pins], how=" · ".join([*how, reason]),
                footprint_id=footprint, manufacturer=maker, mpn=mpn, datasheet_notes=notes,
                stacks=sym.stacks(), reason=str(part.get("reason") or ""), basis=str(part.get("basis") or "ai"),
                request=str(part.get("request") or ""), calc=str(part.get("calc") or "")), ""
        if not candidates:
            return None, "KiCad 라이브러리에서 맞는 심볼을 찾지 못했습니다. 품번이나 KiCad 심볼 이름이 필요합니다."
        return None, "쓸 수 있는 KiCad 심볼이 없습니다: " + "; ".join(tried[:3])
