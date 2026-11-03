"""Direct Circuit IR -> KiCad 10 ``.kicad_sch`` generator (ADR-001 option B).

Connectivity strategy (PoC): every connected pin gets a short wire stub that
points away from the symbol body, and a local label carrying the net name sits
on the free end of the stub. KiCad joins equal label names on a sheet, so the
netlist depends only on the IR nets, never on how symbols are arranged.

The output is deterministic: UUIDs are derived from the project id and IR ids
(uuid5), and ordering follows the IR. The same IR always yields the same file.

Coordinates: library symbols use a Y-up frame; the schematic sheet is Y-down.
A pin at library (px, py) on a symbol placed at (X, Y) with CCW rotation r is at
``(X, Y) + rot_r(px, -py)`` on the sheet.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from circuit_core import CircuitIR, Issue, Placement, Severity, validate_circuit

from . import sexpr
from .sexpr import num, q
from .symbols import LibField, LibSymbol, SymbolLibrary, SymbolLibraryError

SCHEMATIC_FILE_VERSION = "20260306"
"""``.kicad_sch`` format written by KiCad 10.0 (verified against 10.0.8)."""
GENERATOR = "ai_circuit_studio"
GENERATOR_VERSION = "0.1"

GRID = 1.27
STUB_LENGTH = 2.54
LABEL_FONT = 1.27
_UUID_NAMESPACE = uuid.UUID("3b0f6f8e-6a43-4d4e-9a8c-2f6f1d0c5a11")
_PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

_MANDATORY_FIELDS = ("Reference", "Value", "Footprint", "Datasheet", "Description")

# Pin direction (towards body, library frame) -> outward unit vector, sheet frame.
_OUTWARD = {0: (-1, 0), 90: (0, 1), 180: (1, 0), 270: (0, -1)}
# Outward direction -> (label angle, justification) so the text reads away from the pin.
_LABEL_STYLE = {
    (1, 0): (0, ("left", "bottom")),
    (0, -1): (90, ("left", "bottom")),
    (-1, 0): (180, ("right", "bottom")),
    (0, 1): (270, ("right", "bottom")),
}


class SchematicGenerationError(Exception):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        lines = [f"[{i.error_code}] {i.message}" for i in issues]
        super().__init__("schematic generation failed:\n  " + "\n  ".join(lines))


@dataclass(frozen=True)
class PinAnchor:
    """Where a pin landed on the sheet - kept for debugging and tests."""

    reference: str
    pin: str
    x: float
    y: float
    net: str | None


@dataclass
class GeneratedSchematic:
    text: str
    project_name: str
    root_uuid: str
    anchors: list[PinAnchor]
    warnings: list[Issue] = field(default_factory=list)
    symbol_sources: dict[str, dict[str, str]] = field(default_factory=dict)

    def write(self, out_dir: Path) -> Path:
        """Write ``<project>.kicad_sch`` and a minimal ``<project>.kicad_pro``."""
        out_dir.mkdir(parents=True, exist_ok=True)
        sch = out_dir / f"{self.project_name}.kicad_sch"
        _atomic_write(sch, self.text)
        pro = out_dir / f"{self.project_name}.kicad_pro"
        _atomic_write(pro, minimal_project_file(self.project_name))
        return sch


def minimal_project_file(project_name: str) -> str:
    # KiCad fills every missing setting with its defaults when it loads a project.
    return (
        "{\n"
        '  "meta": {\n'
        f'    "filename": "{project_name}.kicad_pro",\n'
        '    "version": 3\n'
        "  }\n"
        "}\n"
    )


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


# --- geometry ---------------------------------------------------------------

def _rotate(x: float, y: float, rotation: int) -> tuple[float, float]:
    """Rotate a sheet-frame vector counter-clockwise (as seen on screen)."""
    if rotation == 0:
        return x, y
    if rotation == 90:
        return y, -x
    if rotation == 180:
        return -x, -y
    if rotation == 270:
        return -y, x
    raise ValueError(f"unsupported rotation {rotation}")


def _to_sheet(p: Placement, lx: float, ly: float) -> tuple[float, float]:
    dx, dy = _rotate(lx, -ly, p.rotation)
    return round(p.x + dx, 4), round(p.y + dy, 4)


def _on_grid(v: float) -> bool:
    return abs(v / GRID - round(v / GRID)) < 1e-6


# --- generator --------------------------------------------------------------

class _Ids:
    def __init__(self, project_id: str):
        self.project_id = project_id

    def __call__(self, *parts: str) -> str:
        return str(uuid.uuid5(_UUID_NAMESPACE, "/".join((self.project_id, *parts))))


def _auto_placement(index: int) -> Placement:
    return Placement(x=50.8 + (index % 6) * 30.48, y=63.5 + (index // 6) * 30.48, rotation=0)


def _check_against_library(circuit: CircuitIR, library: SymbolLibrary) -> tuple[dict[str, LibSymbol], list[Issue]]:
    E, W = Severity.ERROR, Severity.WARNING
    issues: list[Issue] = []
    symbols: dict[str, LibSymbol] = {}
    for comp in circuit.components:
        try:
            sym = library.get(comp.symbol_id)
        except SymbolLibraryError as exc:
            issues.append(Issue("SYMBOL_NOT_FOUND", E, str(exc), (comp.id,),
                                "Use a symbol that exists in the installed KiCad library."))
            continue
        symbols[comp.id] = sym
        lib_numbers = {p.number for p in sym.pins}
        ir_numbers = set(comp.pin_numbers())
        for pin in comp.pins:
            lib_pin = sym.pin(pin.number)
            if lib_pin is None:
                issues.append(Issue(
                    "PIN_NOT_IN_SYMBOL", E,
                    f"{comp.reference}: pin '{pin.number}' does not exist in {comp.symbol_id} "
                    f"(symbol pins: {', '.join(sorted(lib_numbers))}).",
                    (comp.id,), "Use the symbol's pin numbers; check the pin map against the library."))
            elif pin.name is not None and pin.name != lib_pin.name:
                issues.append(Issue(
                    "PIN_NAME_MISMATCH", E,
                    f"{comp.reference}: pin {pin.number} is '{lib_pin.name}' in {comp.symbol_id}, "
                    f"but the IR expects '{pin.name}'.",
                    (comp.id,), "The pin map disagrees with the symbol; verify it before generating."))
        missing = sorted(lib_numbers - ir_numbers)
        if missing:
            issues.append(Issue(
                "PIN_NOT_DECLARED", E,
                f"{comp.reference}: {comp.symbol_id} has pins {', '.join(missing)} that the IR does not declare.",
                (comp.id,), "Declare every symbol pin so no connection is left implicit."))
        ref_field = sym.field("Reference")
        if ref_field and not comp.reference.startswith(ref_field.value.rstrip("?")):
            issues.append(Issue(
                "REFERENCE_PREFIX_MISMATCH", W,
                f"{comp.reference} uses {comp.symbol_id}, whose reference prefix is '{ref_field.value}'.",
                (comp.id,)))
    return symbols, issues


def _effects(font_size: float, justify: tuple[str, ...] = ()) -> list:
    node: list = ["effects", ["font", ["size", num(font_size), num(font_size)]]]
    if justify:
        node.append(["justify", *justify])
    return node


def _field_node(name: str, value: str, x: float, y: float, angle: float, hidden: bool,
                effects: list | None) -> list:
    node: list = ["property", q(name), q(value), ["at", num(x), num(y), num(angle)]]
    if hidden:
        node.append(["hide", "yes"])
    node += [["show_name", "no"], ["do_not_autoplace", "no"]]
    node.append(sexpr.deepcopy(effects) if effects else _effects(LABEL_FONT))
    return node


def generate_schematic(circuit: CircuitIR, library: SymbolLibrary,
                       project_name: str = "schematic") -> GeneratedSchematic:
    """Build a KiCad 10 schematic for ``circuit``; raise on any error-level issue."""
    if not _PROJECT_NAME.fullmatch(project_name):
        raise ValueError(f"project name {project_name!r} must match {_PROJECT_NAME.pattern}")
    validation = validate_circuit(circuit)
    symbols, lib_issues = _check_against_library(circuit, library)
    issues = validation.issues + lib_issues
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors:
        raise SchematicGenerationError(errors)
    warnings = [i for i in issues if i.severity is Severity.WARNING]

    ids = _Ids(circuit.project_id)
    root_uuid = ids("sheet", "root")
    placements = {
        c.id: circuit.layout.get(c.id) or _auto_placement(i)
        for i, c in enumerate(circuit.components)
    }
    for comp_id, p in placements.items():
        if not (_on_grid(p.x) and _on_grid(p.y)):
            warnings.append(Issue("OFF_GRID", Severity.WARNING,
                                  f"Placement of '{comp_id}' is not on the {GRID} mm grid.", (comp_id,)))

    net_of = {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}

    # Pin anchors, stubs and labels -------------------------------------------
    anchors: list[PinAnchor] = []
    wires: list[list] = []
    labels: list[list] = []
    points: dict[tuple[float, float], set[str]] = {}
    segments: list[tuple[tuple[float, float], tuple[float, float], str]] = []

    for comp in circuit.components:
        sym, p = symbols[comp.id], placements[comp.id]
        for pin in comp.pins:
            lib_pin = sym.pin(pin.number)
            x, y = _to_sheet(p, lib_pin.x, lib_pin.y)
            net = net_of.get((comp.id, pin.number))
            anchors.append(PinAnchor(comp.reference, pin.number, x, y, net))
            points.setdefault((x, y), set()).add(net or f"<unconnected {comp.reference}.{pin.number}>")
            if net is None:
                continue
            ox, oy = _rotate(*_OUTWARD[lib_pin.angle], p.rotation)
            ex, ey = round(x + ox * STUB_LENGTH, 4), round(y + oy * STUB_LENGTH, 4)
            points.setdefault((ex, ey), set()).add(net)
            segments.append(((x, y), (ex, ey), net))
            key = (comp.id, pin.number)
            wires.append([
                "wire",
                ["pts", ["xy", num(x), num(y)], ["xy", num(ex), num(ey)]],
                ["stroke", ["width", "0"], ["type", "default"]],
                ["uuid", q(ids("wire", *key))],
            ])
            angle, justify = _LABEL_STYLE[(ox, oy)]
            labels.append([
                "label", q(net),
                ["at", num(ex), num(ey), num(angle)],
                _effects(LABEL_FONT, justify),
                ["uuid", q(ids("label", *key))],
            ])

    collisions = _find_collisions(points, segments)
    if collisions:
        raise SchematicGenerationError(collisions)

    # Placed symbols ----------------------------------------------------------
    placed: list[list] = []
    for comp in circuit.components:
        sym, p = symbols[comp.id], placements[comp.id]
        placed.append(_placed_symbol(comp, sym, p, ids, project_name, root_uuid))

    unique_symbols = {s.lib_id: s for s in symbols.values()}
    lib_symbols = ["lib_symbols"] + [unique_symbols[k].embedded_node() for k in sorted(unique_symbols)]

    title = circuit.title or circuit.project_id
    doc: list = [
        "kicad_sch",
        ["version", SCHEMATIC_FILE_VERSION],
        ["generator", q(GENERATOR)],
        ["generator_version", q(GENERATOR_VERSION)],
        ["uuid", q(root_uuid)],
        ["paper", q("A4")],
        ["title_block",
         ["title", q(title)],
         ["comment", "1", q(f"Generated from Circuit IR '{circuit.project_id}' "
                               f"(schema {circuit.schema_version})")],
         ["comment", "2", q(f"Design verification status: {circuit.validation.status.value}")]],
        lib_symbols,
        # KiCad 10 saves items grouped by type and sorted by UUID; doing the same
        # makes a load/save round trip in KiCad a no-op apart from the generator.
        *sorted(wires, key=_uuid_of),
        *sorted(labels, key=_uuid_of),
        *sorted(placed, key=_uuid_of),
        ["sheet_instances", ["path", q("/"), ["page", q("1")]]],
    ]

    sources = {}
    for s in symbols.values():
        nickname = s.lib_id.partition(":")[0]
        sources[s.lib_id] = library.provenance(nickname)

    return GeneratedSchematic(
        text=sexpr.dumps(doc),
        project_name=project_name,
        root_uuid=root_uuid,
        anchors=anchors,
        warnings=warnings,
        symbol_sources=dict(sorted(sources.items())),
    )


def _uuid_of(node: list) -> str:
    return sexpr.child(node, "uuid")[1]


def _placed_symbol(comp, sym: LibSymbol, p: Placement, ids: _Ids, project_name: str,
                   root_uuid: str) -> list:
    def flag(name: str, default: str) -> list:
        node = sexpr.child(sym.node, name)
        return [name, node[1] if node else default]

    node: list = [
        "symbol",
        ["lib_id", q(sym.lib_id)],
        ["at", num(p.x), num(p.y), num(p.rotation)],
        ["unit", "1"],
        ["body_style", "1"],
        flag("exclude_from_sim", "no"),
        flag("in_bom", "yes"),
        flag("on_board", "yes"),
        flag("in_pos_files", "yes"),
        ["dnp", "no"],
        ["uuid", q(ids("symbol", comp.id))],
    ]

    overrides = {"Reference": comp.reference, "Value": comp.value}
    if comp.footprint_id:
        overrides["Footprint"] = comp.footprint_id
    lib_fields = {f.name: f for f in sym.fields}
    ordered = [n for n in _MANDATORY_FIELDS if n in lib_fields]
    ordered += [f.name for f in sym.fields if f.name not in _MANDATORY_FIELDS and not f.name.startswith("ki_")]
    for name in ordered:
        f: LibField = lib_fields[name]
        x, y = _to_sheet(p, f.x, f.y)
        node.append(_field_node(name, overrides.get(name, f.value), x, y, f.angle, f.hidden, f.effects))

    # Extra hidden fields for traceability back to the IR.
    extra = {"AICS_ID": comp.id}
    if comp.manufacturer_part_number:
        extra["MPN"] = comp.manufacturer_part_number
    for name, value in extra.items():
        node.append(_field_node(name, value, p.x, p.y, 0, True, None))

    for pin in sorted(comp.pins, key=lambda pin: pin.number):
        node.append(["pin", q(pin.number), ["uuid", q(ids("pin", comp.id, pin.number))]])
    node.append([
        "instances",
        ["project", q(project_name),
         ["path", q(f"/{root_uuid}"), ["reference", q(comp.reference)], ["unit", "1"]]],
    ])
    return node


def _find_collisions(points: dict[tuple[float, float], set[str]],
                     segments: list[tuple[tuple[float, float], tuple[float, float], str]]) -> list[Issue]:
    """Refuse layouts where geometry would join different nets."""
    issues: list[Issue] = []
    for (x, y), nets in sorted(points.items()):
        if len(nets) > 1:
            issues.append(Issue("LAYOUT_COLLISION", Severity.ERROR,
                                f"Different nets meet at ({num(x)}, {num(y)}): {', '.join(sorted(nets))}.",
                                recovery_hint="Move the symbols apart."))
    for (a, b, net) in segments:
        for (px, py), nets in points.items():
            if net in nets or (px, py) in (a, b):
                continue
            inside_x = min(a[0], b[0]) <= px <= max(a[0], b[0])
            inside_y = min(a[1], b[1]) <= py <= max(a[1], b[1])
            if inside_x and inside_y:
                issues.append(Issue("LAYOUT_COLLISION", Severity.ERROR,
                                    f"Stub of net '{net}' touches ({num(px)}, {num(py)}) "
                                    f"of {', '.join(sorted(nets))}.",
                                    recovery_hint="Move the symbols apart."))
    return issues
