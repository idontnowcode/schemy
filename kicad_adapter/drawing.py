"""Drawing a placed page: power symbols on rails, wires between nearby pins, labels for the rest.

Rails (ground, and supplies such as ``+5V`` or ``VCC``) get a KiCad power symbol on every pin, the way people
draw them. Signal pins of one net are joined with straight wires, or wires with one or two bends, where the
path is short and clear. Whatever cannot be wired keeps a label, so connectivity never depends on routing
succeeding. A wire only ever joins pins of its own net: every route is checked against other nets' pins,
wires, labels and symbol bodies before it is drawn, and the generator checks the finished page again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from circuit_core.datasheets import is_ground_name, net_voltage

from . import sexpr
from .symbols import LibField, LibSymbol, SymbolLibrary, SymbolLibraryError

GRID = 1.27
STEP = 2.54
STUB = 2.54
FONT = 1.27
MAX_ROUTE = 35.56
"""Longest wire drawn between two pins (mm); farther pins keep labels."""
EPS = 1e-6

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
Dir = tuple[int, int]

# Pin direction (towards the body, library frame) -> outward unit vector, sheet frame.
OUTWARD: dict[int, Dir] = {0: (-1, 0), 90: (0, 1), 180: (1, 0), 270: (0, -1)}

# Supply names without a voltage in them: the bare name, optionally with a number (VCC, VDD_1, VBUS2), but not
# signals that merely start that way (VIN_A, VBUS_DET, VCC_SENSE).
_RAIL_NAME = re.compile(r"^(VCC|VDD|VEE|VSS|VIN|VBAT|VBATT|VBUS|VSYS|VMOT|VPP|AVDD|DVDD|AVCC|DVCC|VDDA|VDDIO|VPWR)"
                        r"(_?\d+)?$", re.I)


# --- geometry -------------------------------------------------------------------------------

def rotate(x: float, y: float, rotation: int) -> tuple[float, float]:
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


def rotate_dir(d: Dir, rotation: int) -> Dir:
    x, y = rotate(d[0], d[1], rotation)
    return int(x), int(y)


def orient(out: Dir, want: Dir) -> int | None:
    """The rotation that turns direction ``out`` into ``want``."""
    for r in (0, 90, 180, 270):
        if rotate_dir(out, r) == want:
            return r
    return None


def snap(v: float, step: float = GRID) -> float:
    return round(round(v / step) * step, 4)


def r4(p: Point) -> Point:
    return round(p[0], 4), round(p[1], 4)


def rect_of(points: list[Point]) -> Rect:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def union(*rects: Rect) -> Rect:
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def shift(rect: Rect, dx: float, dy: float) -> Rect:
    return rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy


def grow(rect: Rect, m: float) -> Rect:
    return rect[0] - m, rect[1] - m, rect[2] + m, rect[3] + m


def rotate_rect(rect: Rect, rotation: int, mirror: bool = False) -> Rect:
    return rect_of([transform(x, y, rotation, mirror) for x in (rect[0], rect[2]) for y in (rect[1], rect[3])])


def transform(x: float, y: float, rotation: int, mirror: bool = False) -> tuple[float, float]:
    """A symbol-frame point on the sheet: KiCad turns first, then mirrors (``mirror y``: x -> -x on the sheet).
    The order matters only for parts both turned 90/270 degrees and mirrored (checked against KiCad 10.0.8)."""
    rx, ry = rotate(x, y, rotation)
    return (-rx if mirror else rx), ry


def transform_dir(d: Dir, rotation: int, mirror: bool = False) -> Dir:
    x, y = transform(d[0], d[1], rotation, mirror)
    return int(x), int(y)


def overlaps(a: Rect, b: Rect) -> bool:
    """Open interiors intersect (touching edges do not count)."""
    return a[0] < b[2] - EPS and b[0] < a[2] - EPS and a[1] < b[3] - EPS and b[1] < a[3] - EPS


def overlap_area(a: Rect, b: Rect) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def segment_hits_rect(p: Point, q: Point, rect: Rect) -> bool:
    """An axis-aligned segment passes through the open interior of ``rect``."""
    x0, y0, x1, y1 = rect
    lo_x, hi_x = sorted((p[0], q[0]))
    lo_y, hi_y = sorted((p[1], q[1]))
    if hi_y - lo_y < EPS:  # horizontal
        return y0 + EPS < lo_y < y1 - EPS and lo_x < x1 - EPS and hi_x > x0 + EPS
    return x0 + EPS < lo_x < x1 - EPS and lo_y < y1 - EPS and hi_y > y0 + EPS


def on_segment(pt: Point, p: Point, q: Point) -> bool:
    return (min(p[0], q[0]) - EPS <= pt[0] <= max(p[0], q[0]) + EPS
            and min(p[1], q[1]) - EPS <= pt[1] <= max(p[1], q[1]) + EPS
            and (abs(p[0] - q[0]) < EPS or abs(p[1] - q[1]) < EPS))


def touch(p: Point, q: Point, u: Point, v: Point) -> Point | str | None:
    """Where two axis-aligned segments meet: None, the single common point, or ``"overlap"``."""
    x0, x1 = max(min(p[0], q[0]), min(u[0], v[0])), min(max(p[0], q[0]), max(u[0], v[0]))
    y0, y1 = max(min(p[1], q[1]), min(u[1], v[1])), min(max(p[1], q[1]), max(u[1], v[1]))
    if x0 > x1 + EPS or y0 > y1 + EPS:
        return None
    if abs(x1 - x0) < EPS and abs(y1 - y0) < EPS:
        return r4((x0, y0))
    return "overlap"


def direction(p: Point, q: Point) -> Dir:
    dx, dy = q[0] - p[0], q[1] - p[1]
    return (1 if dx > EPS else -1 if dx < -EPS else 0, 1 if dy > EPS else -1 if dy < -EPS else 0)


def path_length(path: list[Point]) -> float:
    return sum(abs(q[0] - p[0]) + abs(q[1] - p[1]) for p, q in zip(path, path[1:], strict=False))


# --- symbols ---------------------------------------------------------------------------------

def _graphics(sym: LibSymbol, unit: int = 1) -> list[Rect]:
    """Box of each drawn item (no pins) of one unit: sheet frame (y down), unrotated, relative to the origin."""
    rects: list[Rect] = []
    for sub in sexpr.children(sym.node, "symbol"):
        m = re.match(r"^.+_(\d+)_(\d+)$", sexpr.unquote(sub[1]))
        if m and int(m.group(1)) not in (0, unit):
            continue
        for g in sub[2:]:
            kind = sexpr.head(g)
            pts: list[Point] = []
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
            if pts:
                rects.append(rect_of(pts))
    return rects


def body_rect(sym: LibSymbol, unit: int = 1) -> Rect:
    """Graphics and pins of one unit of a library symbol: sheet frame (y down), unrotated, relative to its origin."""
    pts: list[Point] = []
    for r in _graphics(sym, unit):
        pts += [(r[0], r[1]), (r[2], r[3])]
    for p in sym.pins:
        if p.unit not in (0, unit):
            continue
        ox, oy = OUTWARD[p.angle]
        pts += [(p.x, -p.y), (p.x - ox * p.length, -p.y - oy * p.length)]
    return rect_of(pts) if pts else (0.0, 0.0, 0.0, 0.0)


CHAR = FONT * 0.95
"""Width of one character of KiCad's default 1.27 mm font (measured on rendered sheets)."""


def text_box(center: Point, text: str, horizontal: bool) -> Rect:
    w, h = max(len(text), 1) * CHAR / 2 + 0.3, FONT * 0.6
    if not horizontal:
        w, h = h, w
    return center[0] - w, center[1] - h, center[0] + w, center[1] + h


def _text_rect(f: LibField, text: str, at: Point) -> Rect:
    """A field's text box in the symbol's frame (sheet orientation, y down), anchored at ``at``."""
    length, height = max(len(text), 1) * CHAR, FONT * 0.7
    justify = sexpr.child(f.effects, "justify") if f.effects else None
    tokens = set(justify[1:]) if justify else set()
    lo = 0.0 if "left" in tokens else -length if "right" in tokens else -length / 2
    ax, ay = at
    if int(f.angle) % 180 == 0:
        return ax + lo, ay - height, ax + lo + length, ay + height
    return ax - height, ay - lo - length, ax + height, ay - lo  # reads bottom to top


def field_anchor(sym: LibSymbol, f: LibField, text: str, unit: int = 1) -> Point:
    """Where a field's text is anchored, in the symbol's frame (y down): the library's spot, slid along the
    text when a text longer than the library's own would run into the drawing - e.g. an LED's value "GREEN"
    into its light arrows. Items the field sits inside on purpose (a resistor's value in its box) are left be."""
    at = (f.x, -f.y)
    if len(text) <= len(f.value):
        return at
    i = 0 if int(f.angle) % 180 == 0 else 1
    mine, lib = _text_rect(f, text, at), _text_rect(f, f.value, at)
    shift = 0.0
    for g in _graphics(sym, unit):
        if g[0] <= at[0] <= g[2] and g[1] <= at[1] <= g[3]:
            continue
        if not overlaps(mine, g):
            continue
        before = (g[i] + g[i + 2]) / 2 < at[i]  # the item is on the low side of the text: slide toward high

        def depth(r: Rect, g: Rect = g, before: bool = before) -> float:
            if not overlaps(r, g):
                return 0.0
            return max(0.0, g[i + 2] - r[i]) if before else max(0.0, r[i + 2] - g[i])

        s = depth(mine) - depth(lib)
        if s > 0 and s + 0.3 > abs(shift):
            shift = (s + 0.3) if before else -(s + 0.3)
    if not shift:
        return at
    return (round(at[0] + shift, 4), at[1]) if i == 0 else (at[0], round(at[1] + shift, 4))


def field_rect(f: LibField, text: str, rotation: int, mirror: bool = False, at: Point | None = None) -> Rect:
    """Where a visible symbol field's text lands, relative to the placed symbol.

    KiCad keeps a field on the same side of the body when the symbol turns (it swaps the justification
    rather than draw text upside down), so the box is worked out in the symbol's frame and turned with it."""
    return rotate_rect(_text_rect(f, text, at if at is not None else (f.x, -f.y)), rotation, mirror)


def label_length(net: str) -> float:
    return len(net) * FONT * 0.9 + 1.0


def label_rect(end: Point, out: Dir, net: str, stub: float = STUB) -> Rect:
    """Space taken by a stub and the label text on its end."""
    reach = stub + label_length(net)
    far = (end[0] + out[0] * reach, end[1] + out[1] * reach)
    x0, y0, x1, y1 = rect_of([end, far])
    if out[1] == 0:
        return x0, y0 - 1.9, x1, y1 + 0.6
    return x0 - 1.9, y0, x1 + 1.9, y1


def rail_kind(net: str, has_power_pin: bool, has_connector: bool) -> str | None:
    """``"ground"``, ``"supply"`` or None (a signal, drawn with wires and labels)."""
    if is_ground_name(net):
        return "ground"
    if net_voltage(net) is not None:
        return "supply"
    if _RAIL_NAME.match(net) and (has_power_pin or has_connector):
        return "supply"
    return None


def is_power_symbol(sym: LibSymbol) -> bool:
    return sexpr.child(sym.node, "power") is not None and len(sym.pins) == 1


def power_symbol_for(library: SymbolLibrary, net: str, kind: str) -> LibSymbol | None:
    """KiCad's own symbol when one carries the net's name (``power:+5V``), else GND / VCC renamed."""
    fallback = ["power:GND"] if kind == "ground" else (["power:VEE", "power:VCC"] if net.startswith("-")
                                                       else ["power:VCC"])
    for lib_id in (f"power:{net}", *fallback):
        try:
            sym = library.get(lib_id)
        except SymbolLibraryError:
            continue
        if is_power_symbol(sym):
            return sym
    return None


def upright(sym: LibSymbol) -> Dir:
    """Where a power symbol's graphic points at rotation 0 (up for supplies, down for ground)."""
    ox, oy = OUTWARD[sym.pins[0].angle]
    return -ox, -oy


# --- power ports -------------------------------------------------------------------------------

@dataclass(frozen=True)
class PinView:
    number: str
    end: Point
    """Connection point (relative to the part origin, or absolute once placed)."""
    out: Dir
    net: str | None
    no_connect: bool = False


@dataclass(frozen=True)
class Port:
    """A power symbol hanging on a pin, optionally after a short wire."""

    pin: str
    net: str
    symbol: LibSymbol
    at: Point
    rotation: int
    end: Point
    """The pin's connection point."""
    stub_end: Point | None
    rects: tuple[Rect, ...]
    """The symbol with its text, and the stub (kept apart so text beside the stub does not count)."""
    value_at: tuple[Point, float, str] | None = None
    """For a symbol lying on its side: where its Value text goes instead (position, angle, justification),
    so the text reads horizontally just past the arrow rather than standing across neighbouring rows."""

    @property
    def rect(self) -> Rect:
        return union(*self.rects)

    def moved(self, dx: float, dy: float) -> Port:
        def mv(p: Point) -> Point:
            return r4((p[0] + dx, p[1] + dy))

        value_at = (mv(self.value_at[0]), *self.value_at[1:]) if self.value_at else None
        return Port(self.pin, self.net, self.symbol, mv(self.at), self.rotation, mv(self.end),
                    mv(self.stub_end) if self.stub_end else None, tuple(shift(r, dx, dy) for r in self.rects),
                    value_at)


def _port(pin: PinView, sym: LibSymbol, stub: float, rotation: int) -> Port:
    end = pin.end
    attach = r4((end[0] + pin.out[0] * stub, end[1] + pin.out[1] * stub))
    p = sym.pins[0]
    px, py = rotate(p.x, -p.y, rotation)
    at = r4((attach[0] - px, attach[1] - py))
    graphic = shift(rotate_rect(body_rect(sym), rotation), *at)
    rect, value_at = graphic, None
    value = sym.field("Value")
    if value is not None and not value.hidden:
        text = pin.net or ""
        tip = rotate_dir(upright(sym), rotation)
        if tip[1] == 0:  # on its side: horizontal text just past the arrow
            x = (graphic[2] if tip[0] > 0 else graphic[0]) + tip[0] * 0.635
            y = attach[1]
            value_at = (r4((x, y)), float((int(value.angle) + 90) % 180 if rotation in (90, 270) else value.angle),
                        "left" if tip[0] > 0 else "right")
            length = len(text) * CHAR
            rect = union(rect, (min(x, x + tip[0] * length), y - FONT * 0.6, max(x, x + tip[0] * length),
                                y + FONT * 0.6))
        else:
            vx, vy = rotate(value.x, -value.y, rotation)
            rect = union(rect, text_box((at[0] + vx, at[1] + vy), text, (value.angle + rotation) % 180 == 0))
    x0, y0, x1, y1 = rect_of([end, attach])
    wire = (x0, y0 - 0.3, x1, y1 + 0.3) if y0 == y1 else (x0 - 0.3, y0, x1 + 0.3, y1)
    rects = (rect, wire) if stub else (rect,)
    return Port(pin.number, pin.net or "", sym, at, rotation, end, attach if stub else None, rects, value_at)


def _crosses_row(pin: PinView, up: Dir, pins: list[PinView], power: dict[str, LibSymbol]) -> bool:
    """An upright power symbol on sideways ``pin`` would span another signal pin's row on the same side."""
    reach = 5.08  # graphic and text of a power symbol
    for q in pins:
        if q is pin or q.out != pin.out or not q.net or q.net in power:
            continue
        gap = (q.end[1] - pin.end[1]) * up[1] + (q.end[0] - pin.end[0]) * up[0]
        if 0 < gap < reach:
            return True
    return False


def plan_ports(body: Rect, pins: list[PinView], power: dict[str, LibSymbol], texts: list[Rect] = ()) -> list[Port]:
    """Power symbols for a part's rail pins, upright where they fit, clear of the body, its texts and labels."""
    taken = [body, *texts] + [label_rect(p.end, p.out, p.net) for p in pins if p.net and p.net not in power]
    ports: list[Port] = []
    for pin in pins:
        sym = power.get(pin.net or "")
        if sym is None:
            continue
        up = upright(sym)
        facing = orient(up, pin.out) or 0
        if pin.out == up:
            options = [(0.0, 0), (STUB, 0), (2 * STUB, 0)]
        elif pin.out == (-up[0], -up[1]):
            options = [(0.0, facing)]
        elif _crosses_row(pin, up, pins, power):
            # Upright it would cut across the row of a neighbouring signal pin (whose wire or label runs
            # out along that row), so it lies along its own pin instead, like a label.
            options = [(0.0, facing)]
        else:  # sideways pin: a short wire, then the symbol upright; further out if that collides
            options = [(STUB * n, 0) for n in range(1, 7)] + [(0.0, facing)]
        tries = [_port(pin, sym, stub, rotation) for stub, rotation in options]
        port = min(tries, key=lambda t: (sum(overlap_area(r, x) for r in t.rects for x in taken), tries.index(t)))
        taken += port.rects
        ports.append(port)
    return ports


# --- routing ---------------------------------------------------------------------------------

Key = tuple[str, str]
"""(component id, pin number)"""


@dataclass
class _Cluster:
    keys: list[Key]
    segments: list[tuple[Point, Point]] = field(default_factory=list)
    vertices: set[Point] = field(default_factory=set)


@dataclass
class Label:
    net: str
    at: Point
    out: Dir
    key: Key
    stub_from: Point | None
    """Pin end the label's stub starts at (None: the label sits on the pin end itself)."""


@dataclass
class Wiring:
    wires: list[tuple[Point, Point, str]]
    labels: list[Label]
    junctions: list[Point]
    rails: list[tuple[str, _Cluster]] = field(default_factory=list)
    """Wired pieces of rails (nets drawn with power symbols): each gets a power symbol instead of a label."""


def _clean(path: list[Point]) -> list[Point] | None:
    pts: list[Point] = []
    for p in path:
        p = r4(p)
        if not pts or p != pts[-1]:
            pts.append(p)
    changed = True
    while changed:
        changed = False
        for i in range(1, len(pts) - 1):
            d1, d2 = direction(pts[i - 1], pts[i]), direction(pts[i], pts[i + 1])
            if d1 == d2:
                del pts[i]
                changed = True
                break
            if d1 == (-d2[0], -d2[1]):
                return None  # doubles back on itself
    if len(pts) < 2:
        return None
    if any(p[0] != q[0] and p[1] != q[1] for p, q in zip(pts, pts[1:], strict=False)):
        return None
    return pts


def _shapes(a: Point, da: Dir, b: Point, db: Dir | None) -> list[list[Point]]:
    """Orthogonal paths from a to b with at most two bends."""
    raw: list[list[Point]] = [[a, b]] if a[0] == b[0] or a[1] == b[1] else [
        [a, (b[0], a[1]), b], [a, (a[0], b[1]), b]]
    xs = {snap((a[0] + b[0]) / 2)}
    ys = {snap((a[1] + b[1]) / 2)}
    if da[0]:
        xs.add(round(a[0] + da[0] * STEP, 4))
    if da[1]:
        ys.add(round(a[1] + da[1] * STEP, 4))
    if db and db[0]:
        xs.add(round(b[0] + db[0] * STEP, 4))
    if db and db[1]:
        ys.add(round(b[1] + db[1] * STEP, 4))
    raw += [[a, (xm, a[1]), (xm, b[1]), b] for xm in sorted(xs)]
    raw += [[a, (a[0], ym), (b[0], ym), b] for ym in sorted(ys)]
    seen, out = set(), []
    for path in raw:
        clean = _clean(path)
        if clean and tuple(clean) not in seen:
            seen.add(tuple(clean))
            out.append(clean)
    return out


def _cost(path: list[Point], da: Dir, db: Dir | None) -> float:
    cost = path_length(path) + (len(path) - 2) * STEP * 1.5
    if len(path) == 2:  # one straight wire reads well even when it meets a pin from the side (a line of parts)
        return cost
    if direction(path[0], path[1]) != da:
        cost += 3 * STEP
    if db is not None and direction(path[-2], path[-1]) != (-db[0], -db[1]):
        cost += 2 * STEP
    return cost


class Router:
    """Everything already drawn on a page that a new wire has to keep clear of."""

    def __init__(self) -> None:
        self.blocks: list[Rect] = []
        self.points: dict[Point, str] = {}
        self.wires: list[tuple[Point, Point, str]] = []
        self.reserved: dict[Key, tuple[str, Rect]] = {}
        self.pin_points: list[Point] = []

    def add_point(self, pt: Point, net: str) -> None:
        self.points.setdefault(r4(pt), net)

    def add_wire(self, a: Point, b: Point, net: str) -> None:
        self.wires.append((r4(a), r4(b), net))
        self.add_point(a, net)
        self.add_point(b, net)

    def clear(self, path: list[Point], net: str, end_joins: bool) -> bool:
        """``path`` touches nothing but its own start and (when ``end_joins``) its own net at its end."""
        start, end = path[0], path[-1]
        segs = list(zip(path, path[1:], strict=False))
        box = grow(rect_of(path), EPS * 10)
        for rect in self.blocks:
            if overlaps(rect, box) and any(segment_hits_rect(p, q, rect) for p, q in segs):
                return False
        for owner, rect in self.reserved.values():
            if owner != net and any(segment_hits_rect(p, q, rect) for p, q in segs):
                return False
        last = len(segs) - 1
        for pt, n in self.points.items():
            if not (box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]):
                continue
            for i, (p, q) in enumerate(segs):
                if not on_segment(pt, p, q):
                    continue
                if pt == start and i == 0 and n == net:
                    continue
                if end_joins and pt == end and i == last and n == net:
                    continue
                return False
        for u, v, n in self.wires:
            for i, (p, q) in enumerate(segs):
                hit = touch(p, q, u, v)
                if hit is None:
                    continue
                if n == net and hit == start and i == 0:
                    continue
                if n == net and end_joins and hit == end and i == last:
                    continue
                return False
        return True

    def route_net(self, net: str, terminals: list[tuple[Key, Point, Dir]]) -> list[_Cluster]:
        """Join ``terminals`` (one net, one page) into as few wired clusters as clear paths allow."""
        info = {key: (pt, out) for key, pt, out in terminals}
        remaining = [key for key, _, _ in terminals]
        clusters: list[_Cluster] = []
        while remaining:
            if not clusters:
                first = remaining.pop(0)
                clusters.append(_Cluster([first], vertices={info[first][0]}))
                continue
            options: list[tuple[float, int, Key, int, list[Point], tuple[Point, Point] | None]] = []
            serial = 0
            for key in remaining:
                a, da = info[key]
                for ci, cluster in enumerate(clusters):
                    targets: list[tuple[Point, Dir | None]] = [(info[k][0], info[k][1]) for k in cluster.keys]
                    targets += [(v, None) for v in cluster.vertices if v not in {info[k][0] for k in cluster.keys}]
                    for b, db in targets:
                        for path in _shapes(a, da, b, db):
                            if path_length(path) <= MAX_ROUTE + EPS:
                                serial += 1
                                options.append((_cost(path, da, db), serial, key, ci, path, None))
                    for u, v in cluster.segments:
                        if u[1] == v[1] and min(u[0], v[0]) < a[0] < max(u[0], v[0]):
                            b = (a[0], u[1])
                        elif u[0] == v[0] and min(u[1], v[1]) < a[1] < max(u[1], v[1]):
                            b = (u[0], a[1])
                        else:
                            continue
                        if b != a and path_length([a, b]) <= MAX_ROUTE + EPS:
                            serial += 1
                            options.append((_cost([a, b], da, None), serial, key, ci, [a, b], (u, v)))
            options.sort(key=lambda o: (o[0], o[1]))
            chosen = next((o for o in options if self.clear(o[4], net, end_joins=True)), None)
            if chosen is None:
                key = remaining.pop(0)
                clusters.append(_Cluster([key], vertices={info[key][0]}))
                continue
            _, _, key, ci, path, split = chosen
            cluster = clusters[ci]
            if split is not None:
                u, v = split
                cluster.segments.remove((u, v))
                self.wires.remove((u, v, net))
                for s in ((u, path[-1]), (path[-1], v)):
                    cluster.segments.append(s)
                    self.add_wire(*s, net)
            for p, q in zip(path, path[1:], strict=False):
                cluster.segments.append((p, q))
                self.add_wire(p, q, net)
            cluster.vertices.update(path)
            cluster.keys.append(key)
            remaining.remove(key)
        return clusters


def _wire_spot(router: Router, net: str, cluster: _Cluster) -> tuple[Point, Dir, Rect] | None:
    """A place on the cluster's longest free wire for the net label, text running along the wire."""
    need = label_length(net) + STEP
    best: tuple[float, Point, Dir, Rect] | None = None
    for u, v in cluster.segments:
        length = path_length([u, v])
        if length < need or (best is not None and length <= best[0]):
            continue
        if u[1] == v[1]:  # horizontal: reads left to right, above the wire
            start, d = (u if u[0] < v[0] else v), (1, 0)
        else:  # vertical: reads bottom to top, left of the wire
            start, d = (u if u[1] > v[1] else v), (0, -1)
        at = r4((start[0] + d[0] * GRID, start[1] + d[1] * GRID))
        rect = label_rect(at, d, net, stub=0.0)
        if any(overlaps(rect, b) for b in router.blocks):
            continue
        if any(owner != net and overlaps(rect, r) for owner, r in router.reserved.values()):
            continue
        best = (length, at, d, rect)
    return None if best is None else best[1:]


def junctions_of(router: Router) -> list[Point]:
    """Points where three or more wire ends and pins meet."""
    counts: dict[Point, int] = {}
    for a, b, _ in router.wires:
        counts[a] = counts.get(a, 0) + 1
        counts[b] = counts.get(b, 0) + 1
    for pt in router.pin_points:
        if pt in counts:
            counts[pt] += 1
    return sorted(pt for pt, n in counts.items() if n >= 3)


def wire_page(router: Router, nets: dict[str, list[tuple[Key, Point, Dir]]],
              rails: set[str] | frozenset[str] = frozenset()) -> Wiring:
    """Route every signal net on the page, then give each wired cluster one label with the net's name.

    Every cluster keeps a label even when it holds the whole net, so the net keeps its name in KiCad (and on
    the PCB) instead of an automatic ``Net-(...)`` name.

    A net that cannot be wired in one piece on the first pass is undone and tried again after every other net:
    by then the label space kept free for pins that got wired has been given back, so a short wire that only
    crossed such a space can now be drawn."""
    labels: list[Label] = []

    def spread(net: str) -> float:
        pts = [pt for _, pt, _ in nets[net]]
        x0, y0, x1, y1 = rect_of(pts)
        return (x1 - x0) + (y1 - y0)

    def route(net: str) -> list[_Cluster]:
        terminals = nets[net]
        if len(terminals) == 1:
            return [_Cluster([terminals[0][0]], vertices={terminals[0][1]})]
        return router.route_net(net, terminals)

    rail_pieces: list[tuple[str, _Cluster]] = []

    def label(net: str, clusters: list[_Cluster]) -> None:
        terminals = nets[net]
        if net in rails:  # the caller hangs a power symbol on each piece
            rail_pieces.extend((net, cl) for cl in clusters)
            for key, _, _ in terminals:
                router.reserved.pop(key, None)
            return
        info = {key: (pt, out) for key, pt, out in terminals}
        keep: set[Key] = set()
        for cluster in clusters:
            spot = _wire_spot(router, net, cluster)
            if spot is not None:
                at, d, rect = spot
                router.add_point(at, net)
                router.blocks.append(rect)
                labels.append(Label(net, at, d, cluster.keys[0], None))
                continue
            placed = False
            for key in cluster.keys:
                end, out = info[key]
                stub_end = r4((end[0] + out[0] * STUB, end[1] + out[1] * STUB))
                if router.clear([end, stub_end], net, end_joins=False):
                    router.add_wire(end, stub_end, net)
                    labels.append(Label(net, stub_end, out, key, end))
                    keep.add(key)
                    placed = True
                    break
            if not placed:
                key = cluster.keys[0]
                end, out = info[key]
                labels.append(Label(net, end, out, key, None))
        for key, _, _ in terminals:
            if key not in keep:
                router.reserved.pop(key, None)

    later: list[str] = []
    for net in sorted(nets, key=lambda n: (spread(n), n)):
        wires, points = len(router.wires), set(router.points)
        clusters = route(net)
        if len(clusters) > 1:
            del router.wires[wires:]
            for pt in set(router.points) - points:
                del router.points[pt]
            later.append(net)
            continue
        label(net, clusters)
    for net in later:
        label(net, route(net))
    return Wiring(router.wires, labels, junctions_of(router), rail_pieces)


def rail_port(router: Router, net: str, sym: LibSymbol, piece: _Cluster, ends: dict[Key, tuple[Point, Dir]],
              prefer: list[Key], taken: list[Rect]) -> Port:
    """The power symbol for one wired piece of a rail: upright on a point of the piece where it fits (a preferred
    pin first, e.g. the regulator's), else after a short wire, else lying along a pin like a label."""
    up = upright(sym)
    keys = sorted(piece.keys, key=lambda k: (k not in prefer, piece.keys.index(k)))
    spots = [ends[k][0] for k in keys] + sorted(piece.vertices - {ends[k][0] for k in keys})

    def fits(port: Port) -> bool:
        box = grow(port.rects[0], 0.3)
        if any(overlaps(box, b) for b in [*router.blocks, *taken]):
            return False
        if port.stub_end is not None and not router.clear([port.end, port.stub_end], net, end_joins=False):
            return False
        return all(not segment_hits_rect(a, b, box) for a, b, _ in router.wires)

    for stub in (0.0, STUB, 2 * STUB):
        for pt in spots:
            port = _port(PinView(f"rail:{pt[0]},{pt[1]}", pt, up, net), sym, stub, 0)
            if fits(port):
                return port
    k = keys[0]
    pt, out = ends[k]
    return _port(PinView(k[1], pt, out, net), sym, 0.0, orient(up, out) or 0)


# --- notes -----------------------------------------------------------------------------------

NOTE_FONT = FONT
NOTE_LINE = 3.81
"""Height kept free for one line of note text."""


def note_rect(at: Point, text: str) -> Rect:
    """Space a note takes, written left to right with its bottom-left corner at ``at``."""
    return at[0], at[1] - NOTE_FONT * 1.3, at[0] + max(len(text), 1) * CHAR, at[1] + 0.3


def place_note(text: str, targets: list[Rect], obstacles: list[Rect], wires: list[tuple[Point, Point]],
               points: list[Point]) -> tuple[Point, Rect] | None:
    """The first free spot for a note next to ``targets``: above them, then below, then beside, a little
    further out each round. Free means clear of every obstacle box, wire and connection point."""
    if not targets:
        return None
    x0, y0, x1, y1 = union(*targets)
    w = max(len(text), 1) * CHAR
    rise = NOTE_FONT * 1.3
    xs = [x0, (x0 + x1 - w) / 2, x1 - w] + [x0 + k * 2 * STUB for k in (1, 2, 3, 4)] + [x1 - w - k * 2 * STUB
                                                                                       for k in (1, 2)]

    def free(rect: Rect) -> bool:
        box = grow(rect, 0.5)
        if any(overlaps(box, b) for b in obstacles):
            return False
        if any(segment_hits_rect(a, b, box) for a, b in wires):
            return False
        return not any(box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3] for p in points)

    for ring in range(6):
        out = ring * STUB
        spots = [(x, y0 - GRID - out) for x in xs]
        spots += [(x, y1 + GRID + rise + out) for x in xs]
        spots += [(x1 + GRID + out, (y0 + y1) / 2 + rise / 2), (x0 - GRID - w - out, (y0 + y1) / 2 + rise / 2)]
        for sx, sy in spots:
            at = (snap(sx), snap(sy))
            rect = note_rect(at, text)
            if free(rect):
                return at, rect
    return None
