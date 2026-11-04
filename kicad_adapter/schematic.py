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

import json
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

_BODY_REACH = 7.62  # generous half-size of a small symbol body around its origin
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


PAPERS = (("A4", 297.0, 210.0), ("A3", 420.0, 297.0), ("A2", 594.0, 420.0), ("A1", 841.0, 594.0),
          ("A0", 1189.0, 841.0))
_MARGIN = 15.24
_TITLE_BLOCK = 40.64  # keep the bottom strip free for KiCad's title block
_GAP = 7.62
_SHEET_SYMBOL_H = 10.16

Extent = tuple[float, float, float, float]  # min x, min y, max x, max y (sheet frame, relative to origin)


def _snap_up(v: float, step: float = 2 * GRID) -> float:
    return round(-(-v // step) * step, 4)


def _symbol_extent(sym: LibSymbol, comp, net_of: dict, rotation: int = 0) -> Extent:
    """Space a placed symbol needs: body graphics, pins, stubs with labels, visible fields."""
    pts: list[tuple[float, float]] = []
    for sub in sexpr.children(sym.node, "symbol"):
        for g in sub[2:]:
            kind = sexpr.head(g)
            if kind == "rectangle":
                for corner in ("start", "end"):
                    n = sexpr.child(g, corner)
                    pts.append((float(n[1]), -float(n[2])))
            elif kind in ("polyline", "bezier"):
                for xy in sexpr.children(sexpr.child(g, "pts") or ["pts"], "xy"):
                    pts.append((float(xy[1]), -float(xy[2])))
            elif kind == "circle":
                c, r = sexpr.child(g, "center"), float(sexpr.child(g, "radius")[1])
                pts += [(float(c[1]) - r, -float(c[2]) - r), (float(c[1]) + r, -float(c[2]) + r)]
            elif kind == "arc":
                for key in ("start", "mid", "end"):
                    n = sexpr.child(g, key)
                    pts.append((float(n[1]), -float(n[2])))
    for pin in comp.pins:
        lib_pin = sym.pin(pin.number)
        x, y = lib_pin.x, -lib_pin.y
        pts.append((x, y))
        net = net_of.get((comp.id, pin.number))
        if net:
            ox, oy = _OUTWARD[lib_pin.angle]
            reach = STUB_LENGTH + len(net) * LABEL_FONT * 0.9 + 2.0
            pts.append((x + ox * reach, y + oy * reach))
    shown = {"Reference": comp.reference, "Value": comp.value}
    for f in sym.fields:
        if not f.hidden and not f.name.startswith("ki_"):
            half = max(GRID, len(shown.get(f.name, f.value)) * 0.7)
            pts += [(f.x - half, -f.y - half), (f.x + half, -f.y + half)]
    rotated = [_rotate(x, y, rotation) for x, y in pts] or [(0.0, 0.0)]
    xs, ys = [x for x, _ in rotated], [y for _, y in rotated]
    return (min(xs) - GRID, min(ys) - GRID, max(xs) + GRID, max(ys) + GRID)


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
    """Build a KiCad 10 schematic for ``circuit``; raise on any error-level issue.

    One page gives one ``.kicad_sch``. With several pages (``circuit.sheets``), page 1 is
    the root schematic holding a sheet symbol for every other page, each other page is
    its own file, and nets that appear on more than one page use global labels.
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
    net_pages: dict[str, set[str]] = {}
    for (comp_id, _), net in net_of.items():
        net_pages.setdefault(net, set()).add(page_of[comp_id])
    global_nets = {net for net, pages in net_pages.items() if len(pages) > 1}

    # Placement per page -------------------------------------------------------
    placements: dict[str, Placement] = {}
    papers: dict[str, str] = {}
    sheet_origins: dict[str, tuple[float, float]] = {}
    sheet_sizes = {sh.id: (max(30.48, _snap_up(len(sh.name) * LABEL_FONT * 0.8 + 10.16)), _SHEET_SYMBOL_H)
                   for sh in sheets[1:]}
    for index, sheet in enumerate(sheets):
        comps = [c for c in circuit.components if page_of[c.id] == sheet.id]
        fixed = {c.id: (circuit.layout[c.id], _symbol_extent(symbols[c.id], c, net_of, circuit.layout[c.id].rotation))
                 for c in comps if c.id in circuit.layout}
        auto: list[tuple[str, Extent]] = [(c.id, _symbol_extent(symbols[c.id], c, net_of))
                                          for c in comps if c.id not in circuit.layout]
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
            else:
                ox, oy = origins[c.id]
                placements[c.id] = Placement(x=ox, y=oy, rotation=0)
        for key, origin in origins.items():
            if key.startswith("sheet:"):
                sheet_origins[key[6:]] = origin
    for comp_id, pl in placements.items():
        if not (_on_grid(pl.x) and _on_grid(pl.y)):
            warnings.append(Issue("OFF_GRID", Severity.WARNING,
                                  f"Placement of '{comp_id}' is not on the {GRID} mm grid.", (comp_id,)))

    # Pages ------------------------------------------------------------------------
    sources = {}
    for sym in symbols.values():
        sources[sym.lib_id] = library.provenance(sym.lib_id.partition(":")[0])
    title = circuit.title or circuit.project_id
    anchors: list[PinAnchor] = []
    pages: list[GeneratedPage] = []
    for index, sheet in enumerate(sheets):
        comps = [c for c in circuit.components if page_of[c.id] == sheet.id]
        instance_path = f"/{root_uuid}" if index == 0 else f"/{root_uuid}/{ids('sheet-symbol', sheet.id)}"
        wires, labels, glabels, placed = [], [], [], []
        points: dict[tuple[float, float], set[str]] = {}
        extent: list[tuple[float, float]] = []
        segments: list[tuple[tuple[float, float], tuple[float, float], str]] = []

        for comp in comps:
            sym, pl = symbols[comp.id], placements[comp.id]
            for pin in comp.pins:
                lib_pin = sym.pin(pin.number)
                x, y = _to_sheet(pl, lib_pin.x, lib_pin.y)
                net = net_of.get((comp.id, pin.number))
                anchors.append(PinAnchor(comp.reference, pin.number, x, y, net, sheet.id))
                points.setdefault((x, y), set()).add(net or f"<unconnected {comp.reference}.{pin.number}>")
                if net is None:
                    continue
                ox, oy = _rotate(*_OUTWARD[lib_pin.angle], pl.rotation)
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
                text_len = len(net) * LABEL_FONT * 0.9 + 1.0
                extent.append((ex + ox * text_len, ey + oy * text_len))
                if net in global_nets:
                    glabels.append(_global_label(net, ex, ey, angle, justify[0], ids("glabel", *key)))
                else:
                    labels.append([
                        "label", q(net),
                        ["at", num(ex), num(ey), num(angle)],
                        _effects(LABEL_FONT, justify),
                        ["uuid", q(ids("label", *key))],
                    ])
            placed.append(_placed_symbol(comp, sym, pl, ids, project_name, instance_path))
            extent += [(pl.x - _BODY_REACH, pl.y - _BODY_REACH), (pl.x + _BODY_REACH, pl.y + _BODY_REACH)]
            shown = {"Reference": comp.reference, "Value": comp.value}
            for f in sym.fields:
                if not f.hidden and not f.name.startswith("ki_"):
                    fx, fy = _to_sheet(pl, f.x, f.y)
                    half = max(GRID, len(shown.get(f.name, f.value)) * 0.7)
                    extent += [(fx - half, fy - half), (fx + half, fy + half)]

        extent.extend(points)
        collisions = _find_collisions(points, segments)
        if collisions:
            raise SchematicGenerationError(collisions)

        sheet_nodes = []
        if index == 0:
            for number, sub in enumerate(sheets[1:], start=2):
                sx, sy = sheet_origins[sub.id]
                w, h = sheet_sizes[sub.id]
                sheet_nodes.append(_sheet_symbol(sub, number, sx, sy, w, h, _page_filename(project_name, sub, number),
                                                 ids("sheet-symbol", sub.id), project_name, root_uuid))
                extent += [(sx, sy - 2 * GRID), (sx + w, sy + h + 2 * GRID)]

        used = {symbols[c.id].lib_id: symbols[c.id] for c in comps}
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
                   instance_path: str) -> list:
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
         ["path", q(instance_path), ["reference", q(comp.reference)], ["unit", "1"]]],
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
