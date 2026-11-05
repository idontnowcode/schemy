"""Direct Circuit IR -> KiCad 10 ``.kicad_sch`` generator (ADR-001 option B).

Drawing (see ``drawing.py``): rails get KiCad power symbols, nearby pins of a
signal net are joined with wires where the path is clear, and every other pin
gets a short stub with a label carrying the net name. KiCad joins equal label
names, so the netlist depends only on the IR nets; wires are an addition that
is checked against every other net before it is drawn.

The output is deterministic: UUIDs are derived from the project id and IR ids
(uuid5), and ordering follows the IR. The same IR always yields the same file.

Coordinates: library symbols use a Y-up frame; the schematic sheet is Y-down.
A pin at library (px, py) on a symbol placed at (X, Y) with CCW rotation r is at
``(X, Y) + rot_r(px, -py)`` on the sheet.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from circuit_core import CircuitIR, Issue, Placement, Severity, validate_circuit
from circuit_core.datasheets import is_ground_name
from circuit_core.models import Pin

from . import sexpr
from .drawing import (
    NOTE_FONT,
    NOTE_LINE,
    OUTWARD,
    PinView,
    Port,
    Router,
    body_rect,
    field_anchor,
    field_rect,
    flag_port,
    grow,
    junctions_of,
    label_rect,
    note_rect,
    place_note,
    plan_ports,
    power_symbol_for,
    r4,
    rail_kind,
    rail_port,
    rect_of,
    rotate_rect,
    shift,
    transform,
    transform_dir,
    union,
    upright,
    wire_page,
    wrap_note,
)
from .layout import PinGeom, plan_groups
from .sexpr import num, q
from .symbols import LibField, LibSymbol, SymbolLibrary, SymbolLibraryError

SCHEMATIC_FILE_VERSION = "20260306"
"""``.kicad_sch`` format written by KiCad 10.0 (verified against 10.0.8)."""
GENERATOR = "ai_circuit_studio"
GENERATOR_VERSION = "0.1"

GRID = 1.27
LABEL_FONT = 1.27
_UUID_NAMESPACE = uuid.UUID("3b0f6f8e-6a43-4d4e-9a8c-2f6f1d0c5a11")
_PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

_MANDATORY_FIELDS = ("Reference", "Value", "Footprint", "Datasheet", "Description")

_RC = re.compile(r"^Device:(R|C|CP)(_.*)?$")


def _kind(sym: LibSymbol) -> str:
    if _RC.match(sym.lib_id):
        return "rc"
    return "connector" if sym.lib_id.partition(":")[0].startswith("Connector") else ""
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
    sheet: str = "p1"


@dataclass
class GeneratedPage:
    """One page = one .kicad_sch file. Page 1 is the root schematic."""

    sheet_id: str
    name: str
    number: int
    filename: str
    text: str
    paper: str
    bbox: tuple[float, float, float, float] | None
    """Approximate drawing extent on the sheet (x, y, width, height in mm), for cropping previews."""


@dataclass
class GeneratedSchematic:
    pages: list[GeneratedPage]
    project_name: str
    root_uuid: str
    anchors: list[PinAnchor]
    warnings: list[Issue] = field(default_factory=list)
    symbol_sources: dict[str, dict[str, str]] = field(default_factory=dict)
    power_flags: list[str] = field(default_factory=list)
    """Nets that got an automatic PWR_FLAG (power enters through a connector; nothing on the board drives it)."""

    @property
    def text(self) -> str:
        """The root schematic (page 1)."""
        return self.pages[0].text

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        return self.pages[0].bbox

    @property
    def files(self) -> dict[str, str]:
        return {page.filename: page.text for page in self.pages}

    def write(self, out_dir: Path) -> Path:
        """Write every page, plus ``<project>.kicad_pro``; returns the root ``.kicad_sch``.

        Users should open the ``.kicad_pro``. A ``.kicad_sch`` opened on its own
        runs without a project, and KiCad may then report libraries from other
        projects' settings as missing.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        for page in self.pages:
            _atomic_write(out_dir / page.filename, page.text)
        pro = out_dir / f"{self.project_name}.kicad_pro"
        _atomic_write(pro, project_file(self.project_name))
        return out_dir / self.pages[0].filename


def project_file(project_name: str) -> str:
    """The skeleton KiCad 10 itself uses for File > New Project.

    Copied from ``share/kicad/template/kicad.kicad_pro`` (KiCad 10.0.8) with only
    the file name changed; KiCad fills every other setting with its defaults.
    """
    data = {
        "board": {
            "design_settings": {
                "defaults": {},
                "diff_pair_dimensions": [],
                "drc_exclusions": [],
                "rules": {},
                "track_widths": [],
                "via_dimensions": [],
            }
        },
        "boards": [],
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{project_name}.kicad_pro", "version": 1},
        "net_settings": {"classes": [], "meta": {"version": 0}},
        "pcbnew": {"page_layout_descr_file": ""},
        "sheets": [],
        "text_variables": {},
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


# --- geometry ---------------------------------------------------------------

def _to_sheet(p: Placement, lx: float, ly: float, mirror: bool = False) -> tuple[float, float]:
    dx, dy = transform(lx, -ly, p.rotation, mirror)
    return round(p.x + dx, 4), round(p.y + dy, 4)


def _on_grid(v: float) -> bool:
    return abs(v / GRID - round(v / GRID)) < 1e-6


# --- generator --------------------------------------------------------------

class _Ids:
    def __init__(self, project_id: str):
        self.project_id = project_id

    def __call__(self, *parts: str) -> str:
        return str(uuid.uuid5(_UUID_NAMESPACE, "/".join((self.project_id, *parts))))


PAPERS = (("A4", 297.0, 210.0), ("A3", 420.0, 297.0), ("A2", 594.0, 420.0), ("A1", 841.0, 594.0),
          ("A0", 1189.0, 841.0))
_MARGIN = 15.24
_TITLE_BLOCK = 40.64  # keep the bottom strip free for KiCad's title block
_GAP = 7.62
_SHEET_SYMBOL_H = 10.16

Extent = tuple[float, float, float, float]  # min x, min y, max x, max y (sheet frame, relative to origin)


def _snap_up(v: float, step: float = 2 * GRID) -> float:
    return round(-(-v // step) * step, 4)


def _pin_views(sym: LibSymbol, comp, net_of: dict, rotation: int, mirror: bool = False,
               unique: bool = False) -> list[PinView]:
    """A part's pins at a rotation (after an optional ``mirror y``), relative to its origin (sheet frame).

    With ``unique``, pins stacked on one connection point count once (the visible one), since KiCad joins
    them and one wire, label or power symbol serves them all."""
    if unique:
        views = _pin_views(sym, comp, net_of, rotation, mirror)
        hidden = {p.number for p in sym.pins if p.hidden}
        kept: dict = {}
        for v in sorted(views, key=lambda v: v.number in hidden):
            kept.setdefault(v.end, v)
        keep = {id(v) for v in kept.values()}
        return [v for v in views if id(v) in keep]
    views = []
    for pin in comp.pins:
        lib_pin = sym.pin(pin.number)
        if lib_pin is None:
            continue
        x, y = transform(lib_pin.x, -lib_pin.y, rotation, mirror)
        views.append(PinView(pin.number, r4((x, y)), transform_dir(OUTWARD[lib_pin.angle], rotation, mirror),
                             net_of.get((comp.id, pin.number)), getattr(pin, "no_connect", False)))
    return views


def _field_rects(sym: LibSymbol, comp, rotation: int, mirror: bool = False) -> list[Extent]:
    shown = {"Reference": comp.reference, "Value": comp.value}
    unit = getattr(comp, "unit", 1)
    return [field_rect(f, text, rotation, mirror, field_anchor(sym, f, text, unit)) for f in sym.fields
            if not f.hidden and not f.name.startswith("ki_") for text in (shown.get(f.name, f.value),)]


def _part_extent(sym: LibSymbol, comp, net_of: dict, power: dict[str, LibSymbol], rotation: int = 0,
                 quiet: dict[str, float] | None = None, mirror: bool = False, parts: bool = False,
                 flags: dict[str, LibSymbol] | None = None):
    """Space a placed part needs: body, pins, power symbols, labels (or the wires of ``quiet`` pins), fields,
    and the PWR_FLAGs that will sit on its power symbols (``flags``: net -> flag symbol, for a feeding part).

    One box around all of it, or with ``parts`` the pieces themselves: an IC's label strips reach out from its
    pins, and the corners between them stay free for other parts."""
    quiet = quiet or {}
    body = rotate_rect(body_rect(sym, getattr(comp, "unit", 1)), rotation, mirror)
    views = _pin_views(sym, comp, net_of, rotation, mirror, unique=True)
    texts = _field_rects(sym, comp, rotation, mirror)
    # A ``quiet`` rail pin is wired (inside a regulator's block), so it has no power symbol of its own.
    port_views = [v for v in views if not (v.net in power and v.number in quiet)]
    ports = plan_ports(body, port_views, power, texts)
    rects = [body, *(r for port in ports for r in port.rects), *texts]
    for port in ports:
        if flags and port.net in flags:
            rects += flag_port(Router(), flags[port.net], port, rects).rects
    for v in views:
        if not v.net or (v.net in power and v.number not in quiet):
            continue
        if v.number not in quiet:
            rects.append(label_rect(v.end, v.out, v.net))
        elif quiet[v.number] > 0:
            far = (v.end[0] + v.out[0] * quiet[v.number], v.end[1] + v.out[1] * quiet[v.number])
            rects.append(grow(rect_of([v.end, far]), 1.9))
        else:
            rects.append((*v.end, *v.end))
    if parts:
        return [grow(union(body, *texts), GRID), *(grow(r, GRID) for r in rects if r not in texts and r != body)]
    return grow(union(*rects), GRID)


def _layout_page(fixed: dict[str, tuple[Placement, Extent]], auto: list[tuple[str, Extent]]
                 ) -> tuple[dict[str, tuple[float, float]], str, bool]:
    """Shelf-pack ``auto`` items below any fixed ones on the smallest paper that fits.

    Returns origin per auto item, the paper name, and whether even A0 overflowed.
    """
    fixed_box = None
    if fixed:
        boxes = [(p.x + e[0], p.y + e[1], p.x + e[2], p.y + e[3]) for p, e in fixed.values()]
        fixed_box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes))
    result: dict[str, tuple[float, float]] = {}
    for name, width, height in PAPERS:
        left, top, right, bottom = _MARGIN, _MARGIN, width - _MARGIN, height - _MARGIN - _TITLE_BLOCK
        if fixed_box and (fixed_box[2] > width - _MARGIN or fixed_box[3] > height - _MARGIN):
            continue
        row_top = top if fixed_box is None else max(top, fixed_box[3] + _GAP)
        cursor, row_bottom, result, fits = left, row_top, {}, True
        for key, (x0, y0, x1, y1) in auto:
            if x1 - x0 > right - left:
                fits = False
            ox = _snap_up(cursor - x0)
            if ox + x1 > right and cursor > left:
                row_top, cursor = _snap_up(row_bottom + _GAP), left
                ox = _snap_up(cursor - x0)
            oy = _snap_up(row_top - y0)
            result[key] = (ox, oy)
            cursor = ox + x1 + _GAP
            row_bottom = max(row_bottom, oy + y1)
        if fits and (not auto or row_bottom <= bottom):
            return result, name, False
    return result, PAPERS[-1][0], True


def check_against_library(circuit: CircuitIR, library: SymbolLibrary) -> tuple[dict[str, LibSymbol], list[Issue]]:
    """Compare every component's declared pins with its KiCad symbol (L1, library level)."""
    E, W = Severity.ERROR, Severity.WARNING
    issues: list[Issue] = []
    symbols: dict[str, LibSymbol] = {}
    net_of_pin = {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}
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
        for stack in sym.stacks():
            nets = {net_of_pin.get((comp.id, number)) for number in stack if number in ir_numbers}
            if len(nets) > 1:
                issues.append(Issue(
                    "STACKED_PINS_DIFFER", E,
                    f"{comp.reference}: pins {', '.join(stack)} share one connection point in {comp.symbol_id}, so "
                    f"KiCad joins them, but the IR puts them on different nets "
                    f"({', '.join(sorted(n or '(none)' for n in nets))}).",
                    (comp.id,), "Put stacked pins (extra GND/VCC pins of an IC) on the same net."))
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


PWR_FLAG_ID = "power:PWR_FLAG"


@dataclass
class _VirtualSymbol:
    """A symbol the generator adds that is not a part of the circuit (PWR_FLAG, power symbols)."""

    id: str
    reference: str
    value: str
    net: str
    feeder: str = ""
    """For a PWR_FLAG: the part that brings this power in; the flag is drawn next to it."""
    pins: tuple[Pin, ...] = (Pin(number="1"),)
    footprint_id: str | None = None
    manufacturer_part_number: str | None = None


@dataclass
class _Unit:
    """One unit of a multi-unit symbol (an op-amp channel, its power unit), drawn as its own symbol instance
    with the part's reference. Unit 1 keeps the component id; other units get ``<id>#u<n>``."""

    id: str
    comp_id: str
    unit: int
    reference: str
    value: str
    pins: tuple[Pin, ...]
    footprint_id: str | None = None
    manufacturer_part_number: str | None = None


def _units(components, symbols: dict[str, LibSymbol], page_of: dict[str, str], net_of: dict) -> list:
    out: list = []
    for comp in components:
        sym = symbols[comp.id]
        count = sym.unit_count
        if count <= 1:
            out.append(comp)
            continue
        for unit in range(1, count + 1):
            uid = comp.id if unit == 1 else f"{comp.id}#u{unit}"
            pins = tuple(p for p in comp.pins if sym.unit_of(p.number) == unit)
            if uid != comp.id:
                symbols[uid] = sym
                page_of[uid] = page_of[comp.id]
                for p in pins:
                    if (comp.id, p.number) in net_of:
                        net_of[(uid, p.number)] = net_of.pop((comp.id, p.number))
            out.append(_Unit(uid, comp.id, unit, comp.reference, comp.value, pins, comp.footprint_id,
                             comp.manufacturer_part_number))
    return out


# Parts power can come in through: a connector or battery, or for a supply also a series part from
# another rail (inductor, ferrite, fuse, diode, resistor, switch, jumper, transistor). Capacitors do not.
_FEEDS_GROUND = re.compile(r"^(Connector\w*:.*|Device:Battery\w*)$")
_FEEDS_SUPPLY = re.compile(r"^(Connector\w*:.*|Device:Battery\w*|Device:(L|FerriteBead|Fuse|Polyfuse|R|D)(_\w*)?"
                           r"|Device:Q_\w+|Diode:.*|Switch:.*|Jumper:.*|Transistor_\w+:.*|Inductor\w*:.*|Fuse:.*)$")


def _power_nets(circuit: CircuitIR, symbols: dict[str, LibSymbol], library: SymbolLibrary) -> dict[str, LibSymbol]:
    """Rails drawn with KiCad power symbols, and the symbol each one uses (none without the power library)."""
    out: dict[str, LibSymbol] = {}
    for net in circuit.nets:
        types, connector = set(), False
        for node in net.nodes:
            sym = symbols[node.component_id]
            lib_pin = sym.pin(node.pin)
            types.add(lib_pin.electrical_type if lib_pin else "")
            connector = connector or sym.lib_id.partition(":")[0].startswith("Connector")
        kind = rail_kind(net.name, bool(types & {"power_in", "power_out"}), connector)
        sym = power_symbol_for(library, net.name, kind) if kind else None
        if sym is not None:
            out[net.name] = sym
    return out


def _power_flags(circuit: CircuitIR, symbols: dict[str, LibSymbol], library: SymbolLibrary,
                 page_of: dict[str, str], net_of: dict, power: dict[str, LibSymbol],
                 warnings: list[Issue]) -> list[_VirtualSymbol]:
    """KiCad ERC requires every power input pin to be driven by a power output, and a power symbol counts
    as a power input. Where power enters the board through a connector (or reaches a rail through a series
    part such as an inductor or a diode), nothing on the board drives it, so KiCad's answer is a PWR_FLAG.
    Add one only to nets with a power input, no power output and such a feeding part; any other undriven
    power net is a real problem and stays an ERC error."""
    needed: list[tuple[str, str]] = []  # (net, feeding component id)
    for net in circuit.nets:
        types, feeder = [], None
        feeds = _FEEDS_GROUND if is_ground_name(net.name) else _FEEDS_SUPPLY
        for node in net.nodes:
            sym = symbols[node.component_id]
            lib_pin = sym.pin(node.pin)
            types.append(lib_pin.electrical_type if lib_pin else "")
            if feeder is None and feeds.match(sym.lib_id):
                feeder = node.component_id
        if ("power_in" in types or net.name in power) and "power_out" not in types and feeder:
            needed.append((net.name, feeder))
    if not needed:
        return []
    try:
        flag = library.get(PWR_FLAG_ID)
    except SymbolLibraryError:
        warnings.append(Issue("PWR_FLAG_UNAVAILABLE", Severity.WARNING,
                              "KiCad 'power' library not found; no PWR_FLAG added to "
                              f"{', '.join(n for n, _ in needed)}. "
                              "KiCad ERC will report these power pins as not driven."))
        return []
    out = []
    for n, (net, feeder) in enumerate(needed, start=1):
        v = _VirtualSymbol(id=f"#pwr-flag:{net}", reference=f"#FLG{n:02d}", value="PWR_FLAG", net=net,
                           feeder=feeder)
        symbols[v.id] = flag
        page_of[v.id] = page_of[feeder]
        net_of[(v.id, "1")] = net
        out.append(v)
    return out


def generate_schematic(circuit: CircuitIR, library: SymbolLibrary,
                       project_name: str = "schematic") -> GeneratedSchematic:
    """Build a KiCad 10 schematic for ``circuit``; raise on any error-level issue.

    One page gives one ``.kicad_sch``. With several pages (``circuit.sheets``), page 1 is
    the root schematic holding a sheet symbol for every other page, each other page is
    its own file, and signal nets that appear on more than one page use global labels (rails are
    power symbols, which KiCad joins across pages anyway).
    """
    if not _PROJECT_NAME.fullmatch(project_name):
        raise ValueError(f"project name {project_name!r} must match {_PROJECT_NAME.pattern}")
    validation = validate_circuit(circuit)
    symbols, lib_issues = check_against_library(circuit, library)
    issues = validation.issues + lib_issues
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors:
        raise SchematicGenerationError(errors)
    warnings = [i for i in issues if i.severity is Severity.WARNING]

    ids = _Ids(circuit.project_id)
    root_uuid = ids("sheet", "root")
    sheets = circuit.effective_sheets()
    multi = len(sheets) > 1
    page_of = circuit.page_of()
    net_of = {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}
    power = _power_nets(circuit, symbols, library)
    virtual = _power_flags(circuit, symbols, library, page_of, net_of, power, warnings)
    drawn = _units(circuit.components, symbols, page_of, net_of)
    all_comps = [*drawn, *virtual]
    net_nodes: dict[str, list[tuple[str, str]]] = {}
    for (comp_id, pin_number), net in net_of.items():
        net_nodes.setdefault(net, []).append((comp_id, pin_number))
    # A PWR_FLAG on a rail drawn with power symbols sits on one of those symbols (placed once the page is wired);
    # only a flag on a net drawn with labels is laid out like a part.
    attached = {v.id: v for v in virtual if v.net in power}
    flag_hints = {v.id: v.feeder for v in virtual if v.id not in attached}
    feeder_flags: dict[str, dict[str, LibSymbol]] = {}
    for v in attached.values():
        feeder_flags.setdefault(v.feeder, {})[v.net] = symbols[v.id]
    up_of = {net: upright(sym) for net, sym in power.items()}
    net_pages: dict[str, set[str]] = {}
    for (comp_id, _), net in net_of.items():
        net_pages.setdefault(net, set()).add(page_of[comp_id])
    global_nets = {net for net, pages in net_pages.items() if len(pages) > 1}

    def note_page(note) -> str:
        if note.components:
            return page_of.get(note.components[0], sheets[0].id)
        if note.net and net_pages.get(note.net):
            return next(sh.id for sh in sheets if sh.id in net_pages[note.net])
        return sheets[0].id

    notes_on = {sh.id: [n for n in circuit.notes if note_page(n) == sh.id] for sh in sheets}

    # Placement per page -------------------------------------------------------
    placements: dict[str, Placement] = {}
    mirrored: set[str] = set()
    wired_rail_pins: set[tuple[str, str]] = set()
    """Rail pins drawn with wires (inside a regulator's block) instead of a power symbol each."""
    """Auto-placed connectors drawn with KiCad's ``mirror y`` so pin 1 stays on top while facing right."""
    papers: dict[str, str] = {}
    sheet_origins: dict[str, tuple[float, float]] = {}
    sheet_sizes = {sh.id: (max(30.48, _snap_up(len(sh.name) * LABEL_FONT * 0.8 + 10.16)), _SHEET_SYMBOL_H)
                   for sh in sheets[1:]}
    for index, sheet in enumerate(sheets):
        comps = [c for c in all_comps if page_of[c.id] == sheet.id]
        fixed = {c.id: (circuit.layout[c.id],
                        _part_extent(symbols[c.id], c, net_of, power, circuit.layout[c.id].rotation,
                                     mirror=circuit.layout[c.id].mirror, flags=feeder_flags.get(c.id)))
                 for c in comps if c.id in circuit.layout}
        free = [c for c in comps if c.id not in circuit.layout and c.id not in attached]
        by_id = {c.id: c for c in free}
        groups = plan_groups(
            [c.id for c in free],
            {c.id: [PinGeom(p.number, lp.x, -lp.y, OUTWARD[lp.angle], lp.electrical_type)
                    for p in c.pins if (lp := symbols[c.id].pin(p.number)) is not None] for c in free},
            lambda cid, rot, quiet, mirror=False, parts=False, by_id=by_id: _part_extent(
                symbols[cid], by_id[cid], net_of, power, rot, quiet, mirror, parts, feeder_flags.get(cid)),
            net_of, net_nodes, flag_hints, {c.id: c.value for c in free}, up_of,
            {c.id: _kind(symbols[c.id]) for c in free})
        # A group with notes on its parts keeps a line free above it per note, so a caption always fits.
        lines: Counter[str] = Counter()
        for n in notes_on[sheet.id]:
            g_key = next((g.key for g in groups if n.components and n.components[0] in g.offsets), None)
            if g_key is not None:
                lines[g_key] += wrap_note(n.text).count("\n") + 1
        auto: list[tuple[str, Extent]] = [
            (f"group:{g.key}", (g.extent[0], g.extent[1] - NOTE_LINE * lines[g.key], g.extent[2], g.extent[3]))
            for g in groups]
        if index == 0 and multi:
            auto += [(f"sheet:{sh.id}", (0.0, -2 * GRID, sheet_sizes[sh.id][0], _SHEET_SYMBOL_H + 2 * GRID))
                     for sh in sheets[1:]]
        origins, paper, overflow = _layout_page(fixed, auto)
        papers[sheet.id] = paper
        if overflow:
            warnings.append(Issue("PAGE_OVERFLOW", Severity.WARNING,
                                  f"Page '{sheet.name}' does not fit on A0; split it into more pages."))
        for c in comps:
            if c.id in circuit.layout:
                placements[c.id] = circuit.layout[c.id]
                if circuit.layout[c.id].mirror:
                    mirrored.add(c.id)
        for g in groups:
            gx, gy = origins[f"group:{g.key}"]
            for member, (dx, dy, rot) in g.offsets.items():
                placements[member] = Placement(x=round(gx + dx, 4), y=round(gy + dy, 4), rotation=rot)
                wired_rail_pins |= {(member, p.number) for p in by_id[member].pins
                                    if net_of.get((member, p.number)) in g.wired_rails}
            wired_rail_pins |= g.wired_pins
            mirrored |= g.mirrored
        for key, origin in origins.items():
            if key.startswith("sheet:"):
                sheet_origins[key[6:]] = origin
    for comp_id, pl in placements.items():
        if not (_on_grid(pl.x) and _on_grid(pl.y)):
            warnings.append(Issue("OFF_GRID", Severity.WARNING,
                                  f"Placement of '{comp_id}' is not on the {GRID} mm grid.", (comp_id,)))

    # Pages ------------------------------------------------------------------------
    sources = {}
    for sym in [*symbols.values(), *power.values()]:
        sources[sym.lib_id] = library.provenance(sym.lib_id.partition(":")[0])
    title = circuit.title or circuit.project_id
    anchors: list[PinAnchor] = []
    pages: list[GeneratedPage] = []
    power_count = 0
    for index, sheet in enumerate(sheets):
        comps = [c for c in all_comps if page_of[c.id] == sheet.id]
        instance_path = f"/{root_uuid}" if index == 0 else f"/{root_uuid}/{ids('sheet-symbol', sheet.id)}"
        labels, glabels, placed, no_connects = [], [], [], []
        router = Router()
        terminals: dict[str, list] = {}
        ports: list[tuple[str, Port]] = []
        boxes: list[Extent] = []
        part_rects: dict[str, list[Extent]] = {}
        pin_ends: dict[tuple[str, str], tuple[tuple[float, float], tuple[int, int]]] = {}

        for comp in comps:
            if comp.id in attached:
                continue
            sym, pl = symbols[comp.id], placements[comp.id]
            mirror = comp.id in mirrored
            views = _pin_views(sym, comp, net_of, pl.rotation, mirror)
            body = rotate_rect(body_rect(sym, getattr(comp, "unit", 1)), pl.rotation, mirror)
            texts = _field_rects(sym, comp, pl.rotation, mirror)
            router.blocks.append(shift(body, pl.x, pl.y))
            boxes += [shift(r, pl.x, pl.y) for r in (body, *texts)]
            part_rects[comp.id] = [shift(r, pl.x, pl.y) for r in (body, *texts)]
            drawn_pins = {v.number for v in _pin_views(sym, comp, net_of, pl.rotation, mirror, unique=True)}
            for v in views:
                x, y = r4((pl.x + v.end[0], pl.y + v.end[1]))
                if not isinstance(comp, _VirtualSymbol):
                    anchors.append(PinAnchor(comp.reference, v.number, x, y, v.net, sheet.id))
                if v.number not in drawn_pins:
                    continue  # stacked under the pin that is drawn: the same point, nothing more to draw
                router.pin_points.append((x, y))
                if v.net is None:
                    router.add_point((x, y), f"<unconnected {comp.reference}.{v.number}>")
                    if v.no_connect:
                        no_connects.append(["no_connect", ["at", num(x), num(y)],
                                            ["uuid", q(ids("no-connect", comp.id, v.number))]])
                    continue
                router.add_point((x, y), v.net)
                if v.net not in power or (comp.id, v.number) in wired_rail_pins:
                    terminals.setdefault(v.net, []).append(((comp.id, v.number), (x, y), v.out))
                    router.reserved[(comp.id, v.number)] = (v.net, label_rect((x, y), v.out, v.net))
                    pin_ends[(comp.id, v.number)] = ((x, y), v.out)
            symbol_pins = [v for v in views if v.number in drawn_pins and (comp.id, v.number) not in wired_rail_pins]
            for port in plan_ports(body, symbol_pins, power, texts):
                port = port.moved(pl.x, pl.y)
                ports.append((comp.id, port))
                router.blocks.append(port.rects[0])
                boxes += port.rects
                part_rects.setdefault(comp.id, []).extend(port.rects)
                attach = port.stub_end or port.end
                if port.stub_end:
                    router.add_wire(port.end, port.stub_end, port.net)
                router.add_point(attach, port.net)
                router.pin_points.append(attach)
            placed.append(_placed_symbol(comp, sym, pl, ids, project_name, instance_path,
                                         trace=not isinstance(comp, _VirtualSymbol), mirror=mirror))

        wiring = wire_page(router, terminals, set(power))
        if wiring.rails:
            # One power symbol per wired piece of a rail (a regulator's input line, its output line).
            big = {c.id for c in comps if len(c.pins) >= 3}
            for net, piece in wiring.rails:
                port = rail_port(router, net, power[net], piece, pin_ends, [k for k in piece.keys if k[0] in big],
                                 boxes)
                ports.append((piece.keys[0][0], port))
                router.blocks.append(port.rects[0])
                boxes += port.rects
                attach = port.stub_end or port.end
                if port.stub_end:
                    router.add_wire(port.end, port.stub_end, net)
                router.add_point(attach, net)
                router.pin_points.append(attach)
            wiring.junctions = junctions_of(router)
        for flag in (v for v in attached.values() if page_of[v.id] == sheet.id):
            on_net = [(cid, port) for cid, port in ports if port.net == flag.net]
            if not on_net:
                raise SchematicGenerationError([Issue("PWR_FLAG_NO_SYMBOL", Severity.ERROR,
                                                      f"No power symbol for '{flag.net}' on this page to put its "
                                                      "PWR_FLAG on.")])
            fp = placements.get(flag.feeder)
            _, target = min(on_net, key=lambda cp: (cp[0] != flag.feeder, math.dist(
                cp[1].stub_end or cp[1].end, (fp.x, fp.y)) if fp else 0.0))
            port = flag_port(router, symbols[flag.id], target, boxes)
            placements[flag.id] = Placement(x=port.at[0], y=port.at[1], rotation=port.rotation)
            router.blocks.append(port.rects[0])
            boxes += port.rects
            placed.append(_placed_symbol(flag, symbols[flag.id], placements[flag.id], ids, project_name,
                                         instance_path, trace=False, value_at=port.value_at))
        wires = [[
            "wire",
            ["pts", ["xy", num(a[0]), num(a[1])], ["xy", num(b[0]), num(b[1])]],
            ["stroke", ["width", "0"], ["type", "default"]],
            ["uuid", q(ids("wire", sheet.id, net, f"{num(a[0])},{num(a[1])}", f"{num(b[0])},{num(b[1])}"))],
        ] for a, b, net in wiring.wires]
        junctions = [["junction", ["at", num(x), num(y)], ["diameter", "0"], ["color", "0", "0", "0", "0"],
                      ["uuid", q(ids("junction", sheet.id, f"{num(x)},{num(y)}"))]] for x, y in wiring.junctions]
        for lbl in wiring.labels:
            angle, justify = _LABEL_STYLE[lbl.out]
            (lx, ly), uid = lbl.at, ids("glabel" if lbl.net in global_nets else "label", *lbl.key)
            boxes.append(label_rect(lbl.stub_from or lbl.at, lbl.out, lbl.net, 2.54 if lbl.stub_from else 0.0))
            if lbl.net in global_nets:
                glabels.append(_global_label(lbl.net, lx, ly, angle, justify[0], uid))
            else:
                labels.append(["label", q(lbl.net), ["at", num(lx), num(ly), num(angle)],
                               _effects(LABEL_FONT, justify), ["uuid", q(uid)]])
        power_used: dict[str, LibSymbol] = {}
        for comp_id, port in ports:
            power_count += 1
            v = _VirtualSymbol(id=f"#pwr:{comp_id}:{port.pin}", reference=f"#PWR{power_count:02d}", value=port.net,
                               net=port.net, pins=(Pin(number=port.symbol.pins[0].number),))
            power_used[port.symbol.lib_id] = port.symbol
            placed.append(_placed_symbol(v, port.symbol, Placement(x=port.at[0], y=port.at[1], rotation=port.rotation),
                                         ids, project_name, instance_path, trace=False, value_at=port.value_at))

        texts_out = []
        segments = [(a, b) for a, b, _ in wiring.wires]
        label_boxes: dict[str, list[Extent]] = {}
        for lbl in wiring.labels:
            label_boxes.setdefault(lbl.net, []).append(
                label_rect(lbl.stub_from or lbl.at, lbl.out, lbl.net, 2.54 if lbl.stub_from else 0.0))
        for note in notes_on[sheet.id]:
            if note.components:
                targets = [r for cid in note.components for r in part_rects.get(cid, [])]
            elif note.net:
                targets = (label_boxes.get(note.net) or [r for _, port in ports if port.net == note.net
                                                         for r in port.rects]
                           or [(a.x, a.y, a.x, a.y) for a in anchors if a.sheet == sheet.id and a.net == note.net])
            else:
                targets = []
            text = wrap_note(note.text)
            spot = place_note(text, targets, boxes, segments, list(router.points)) if targets else None
            if spot is None:  # nothing free nearby (or a page note): lines above everything on the page
                top = min((b[1] for b in boxes), default=_MARGIN + NOTE_LINE)
                left = min((b[0] for b in boxes), default=_MARGIN)
                taken = sum(t[2] for t in texts_out if t[0] == "page")
                at = (_snap_up(left), round(-_snap_up(-(top - GRID)) - NOTE_LINE * taken, 4))
                rect = note_rect(at, text)
                kind = "page"
            else:
                (at, rect), kind = spot, "near"
            boxes.append(rect)
            texts_out.append((kind, ["text", q(text), ["exclude_from_sim", "no"],
                                     ["at", num(at[0]), num(at[1]), "0"],
                                     ["effects", ["font", ["size", num(NOTE_FONT), num(NOTE_FONT)], ["italic", "yes"]],
                                      ["justify", "left", "bottom"]],
                                     ["uuid", q(ids("note", note.id))]], text.count("\n") + 1))
        notes_nodes = [node for _, node, _ in texts_out]

        # Independent check of the finished page: no geometry may join two nets.
        points: dict[tuple[float, float], set[str]] = {}
        for pt, net in router.points.items():
            points.setdefault(pt, set()).add(net)
        for comp in comps:
            pl = placements[comp.id]
            for v in _pin_views(symbols[comp.id], comp, net_of, pl.rotation, comp.id in mirrored):
                pt = r4((pl.x + v.end[0], pl.y + v.end[1]))
                points.setdefault(pt, set()).add(v.net or f"<unconnected {comp.reference}.{v.number}>")
        for lbl in wiring.labels:
            points.setdefault(lbl.at, set()).add(lbl.net)
        collisions = _find_collisions(points, wiring.wires)
        if collisions:
            raise SchematicGenerationError(collisions)
        extent: list[tuple[float, float]] = [pt for b in boxes for pt in ((b[0], b[1]), (b[2], b[3]))]
        extent += list(points)

        sheet_nodes = []
        if index == 0:
            for number, sub in enumerate(sheets[1:], start=2):
                sx, sy = sheet_origins[sub.id]
                w, h = sheet_sizes[sub.id]
                sheet_nodes.append(_sheet_symbol(sub, number, sx, sy, w, h, _page_filename(project_name, sub, number),
                                                 ids("sheet-symbol", sub.id), project_name, root_uuid))
                extent += [(sx, sy - 2 * GRID), (sx + w, sy + h + 2 * GRID)]

        used = {symbols[c.id].lib_id: symbols[c.id] for c in comps} | power_used
        lib_symbols = ["lib_symbols"] + [used[k].embedded_node() for k in sorted(used)]
        comments = [
            ["comment", "1", q(f"Generated from Circuit IR '{circuit.project_id}' (schema {circuit.schema_version})")],
            ["comment", "2", q(f"Design verification status: {circuit.validation.status.value}")],
        ]
        if multi:
            comments.append(["comment", "3", q(f"Page {index + 1}/{len(sheets)}: {sheet.name}")])
        doc: list = [
            "kicad_sch",
            ["version", SCHEMATIC_FILE_VERSION],
            ["generator", q(GENERATOR)],
            ["generator_version", q(GENERATOR_VERSION)],
            ["uuid", q(root_uuid if index == 0 else ids("sheet-file", sheet.id))],
            ["paper", q(papers[sheet.id])],
            ["title_block", ["title", q(title)], *comments],
            lib_symbols,
            # KiCad 10 saves items grouped by type and sorted by UUID; doing the same
            # makes a load/save round trip in KiCad a no-op apart from the generator.
            *sorted(notes_nodes, key=_uuid_of),
            *sorted(junctions, key=_uuid_of),
            *sorted(no_connects, key=_uuid_of),
            *sorted(wires, key=_uuid_of),
            *sorted(labels, key=_uuid_of),
            *sorted(glabels, key=_uuid_of),
            *sorted(placed, key=_uuid_of),
            *sorted(sheet_nodes, key=_uuid_of),
        ]
        if index == 0:
            doc.append(["sheet_instances", ["path", q("/"), ["page", q("1")]]])
        pages.append(GeneratedPage(
            sheet_id=sheet.id, name=sheet.name, number=index + 1,
            filename=f"{project_name}.kicad_sch" if index == 0 else _page_filename(project_name, sheet, index + 1),
            text=sexpr.dumps(doc), paper=papers[sheet.id], bbox=_bbox(extent, margin=2 * GRID),
        ))

    return GeneratedSchematic(
        pages=pages,
        project_name=project_name,
        root_uuid=root_uuid,
        anchors=anchors,
        warnings=warnings,
        symbol_sources=dict(sorted(sources.items())),
        power_flags=[v.net for v in virtual],
    )


def _page_filename(project_name: str, sheet, number: int) -> str:
    return f"{project_name}-p{number}-{sheet.id}.kicad_sch"


def _global_label(net: str, x: float, y: float, angle: int, justify: str, uid: str) -> list:
    return [
        "global_label", q(net),
        ["shape", "passive"],
        ["at", num(x), num(y), num(angle)],
        ["fields_autoplaced", "yes"],
        _effects(LABEL_FONT, (justify,)),
        ["uuid", q(uid)],
        ["property", q("Intersheetrefs"), q("${INTERSHEET_REFS}"),
         ["at", num(x), num(y), num(angle)],
         ["hide", "yes"], ["show_name", "no"], ["do_not_autoplace", "no"],
         _effects(LABEL_FONT, (justify,))],
    ]


def _sheet_symbol(sheet, number: int, x: float, y: float, w: float, h: float, filename: str, uid: str,
                  project_name: str, root_uuid: str) -> list:
    def field_node(name: str, value: str, fy: float, justify: tuple[str, str]) -> list:
        return ["property", q(name), q(value), ["at", num(x), num(fy), "0"],
                ["show_name", "no"], ["do_not_autoplace", "no"], _effects(LABEL_FONT, justify)]

    return [
        "sheet",
        ["at", num(x), num(y)],
        ["size", num(w), num(h)],
        ["exclude_from_sim", "no"],
        ["in_bom", "yes"],
        ["on_board", "yes"],
        ["dnp", "no"],
        ["stroke", ["width", "0"], ["type", "solid"]],
        ["fill", ["color", "0", "0", "0", "0"]],
        ["uuid", q(uid)],
        field_node("Sheetname", sheet.name, y - 0.762, ("left", "bottom")),
        field_node("Sheetfile", filename, y + h + 0.635, ("left", "top")),
        ["instances", ["project", q(project_name), ["path", q(f"/{root_uuid}"), ["page", q(str(number))]]]],
    ]


def _bbox(pts: list[tuple[float, float]], margin: float) -> tuple[float, float, float, float] | None:
    if not pts:
        return None
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    x0, y0 = min(xs) - margin, min(ys) - margin
    return (round(x0, 2), round(y0, 2), round(max(xs) + margin - x0, 2), round(max(ys) + margin - y0, 2))


def _uuid_of(node: list) -> str:
    return sexpr.child(node, "uuid")[1]


def _placed_symbol(comp, sym: LibSymbol, p: Placement, ids: _Ids, project_name: str,
                   instance_path: str, trace: bool = True, mirror: bool = False,
                   value_at: tuple[tuple[float, float], float, str] | None = None) -> list:
    def flag(name: str, default: str) -> list:
        node = sexpr.child(sym.node, name)
        return [name, node[1] if node else default]

    unit = getattr(comp, "unit", 1)
    comp_id = getattr(comp, "comp_id", comp.id)
    node: list = [
        "symbol",
        ["lib_id", q(sym.lib_id)],
        ["at", num(p.x), num(p.y), num(p.rotation)],
        *([["mirror", "y"]] if mirror else []),
        ["unit", str(unit)],
        ["body_style", "1"],
        flag("exclude_from_sim", "no"),
        flag("in_bom", "yes"),
        flag("on_board", "yes"),
        flag("in_pos_files", "yes"),
        ["dnp", "no"],
        ["uuid", q(ids("symbol", comp_id) if unit == 1 else ids("symbol", comp_id, f"unit{unit}"))],
    ]

    overrides = {"Reference": comp.reference, "Value": comp.value}
    if comp.footprint_id:
        overrides["Footprint"] = comp.footprint_id
    lib_fields = {f.name: f for f in sym.fields}
    ordered = [n for n in _MANDATORY_FIELDS if n in lib_fields]
    ordered += [f.name for f in sym.fields if f.name not in _MANDATORY_FIELDS and not f.name.startswith("ki_")]
    for name in ordered:
        f: LibField = lib_fields[name]
        ax, ay = field_anchor(sym, f, overrides.get(name, f.value), unit) if not f.hidden else (f.x, -f.y)
        x, y = _to_sheet(p, ax, -ay, mirror)
        angle, effects = f.angle, f.effects
        if name == "Value" and value_at is not None:
            (x, y), angle, justify = value_at
            effects = [e for e in sexpr.deepcopy(f.effects or _effects(LABEL_FONT)) if sexpr.head(e) != "justify"]
            effects.append(["justify", justify])
        node.append(_field_node(name, overrides.get(name, f.value), x, y, angle, f.hidden, effects))

    # Extra hidden fields for traceability back to the IR (not on generator-added flags).
    extra = {"AICS_ID": comp_id} if trace else {}
    if trace and comp.manufacturer_part_number:
        extra["MPN"] = comp.manufacturer_part_number
    for name, value in extra.items():
        node.append(_field_node(name, value, p.x, p.y, 0, True, None))

    # Every unit of a multi-unit symbol lists all of the part's pins (as KiCad saves it); a pin's uuid in
    # the units that do not draw it is derived from the unit too, so uuids stay unique in the file.
    own = {p.number for p in comp.pins}
    numbers = [p.number for p in sym.pins] if unit > 1 or sym.unit_count > 1 else sorted(own)
    for number in sorted(dict.fromkeys(numbers)):
        uid = ids("pin", comp_id, number) if number in own else ids("pin", comp_id, f"unit{unit}", number)
        node.append(["pin", q(number), ["uuid", q(uid)]])
    node.append([
        "instances",
        ["project", q(project_name),
         ["path", q(instance_path), ["reference", q(comp.reference)], ["unit", str(unit)]]],
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
                                    f"Wire of net '{net}' touches ({num(px)}, {num(py)}) "
                                    f"of {', '.join(sorted(nets))}.",
                                    recovery_hint="Move the symbols apart."))
    return issues
