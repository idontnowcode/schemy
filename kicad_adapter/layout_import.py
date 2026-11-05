"""Read back where parts were moved in KiCad (external edits, step 1: placement only).

The generator writes a hidden ``AICS_ID`` field on every symbol it places for a Circuit IR component, so a
schematic saved from KiCad can be matched part by part without guessing from references. Only positions,
rotations and mirroring are read; wires, labels and power symbols are drawn again by the generator, and
connectivity is never taken from the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from circuit_core.models import Placement

from . import sexpr
from .sexpr import child, children, unquote


@dataclass
class KiCadLayout:
    placements: dict[str, Placement] = field(default_factory=dict)
    """IR component id -> where its symbol (unit 1) is on its page."""
    extra_units: list[str] = field(default_factory=list)
    """References of further units of multi-unit parts (the IR keeps one position per part: they stay automatic)."""
    unknown: list[str] = field(default_factory=list)
    """References of symbols added in KiCad (no ``AICS_ID``): not imported."""


def _prop(sym: list, name: str) -> str | None:
    for p in children(sym, "property"):
        if unquote(p[1]) == name:
            return unquote(p[2])
    return None


def read_layout(texts: list[str]) -> KiCadLayout:
    """Placements from the schematic pages ``texts`` (as saved by KiCad or the generator)."""
    out = KiCadLayout()
    for text in texts:
        doc = sexpr.parse(text)
        for sym in children(doc, "symbol"):
            lib_id = unquote(child(sym, "lib_id")[1]) if child(sym, "lib_id") else ""
            if lib_id.startswith("power:"):
                continue  # power symbols and PWR_FLAGs are drawn by the generator
            ref = _prop(sym, "Reference") or "?"
            comp_id = _prop(sym, "AICS_ID")
            if comp_id is None:
                if not ref.startswith("#"):
                    out.unknown.append(ref)
                continue
            unit = child(sym, "unit")
            if unit is not None and unit[1] != "1":
                out.extra_units.append(f"{ref}{chr(ord('A') + int(unit[1]) - 1)}")
                continue
            at = child(sym, "at")
            x, y, rotation = float(at[1]), float(at[2]), int(float(at[3])) % 360 if len(at) > 3 else 0
            mirror = child(sym, "mirror")
            flip = False
            if mirror is not None and mirror[1] == "y":
                flip = True
            elif mirror is not None and mirror[1] == "x":  # mirror x = mirror y turned half way round
                flip, rotation = True, (rotation + 180) % 360
            out.placements[comp_id] = Placement(x=round(x, 4), y=round(y, 4), rotation=rotation, mirror=flip)
    return out
