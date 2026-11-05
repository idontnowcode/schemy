"""Connectivity-aware automatic placement: related parts are drawn together as blocks.

Placing every part as its own box in circuit order scatters a circuit across the sheet. Here:

1. **Groups.** Parts with 3+ pins (ICs, regulators, transistors, larger connectors) are *anchors*.
   Every other part joins the anchor it shares a *signal* net with (a termination resistor joins its
   transceiver, a pull-up joins its MCU). Rails - supply and ground nets, nets with a power pin and
   several nodes, or very large nets - do not pull parts together, except that a part sitting only
   between rails (a decoupling capacitor) joins an IC whose power pin is on that rail. Parts with no
   anchor (an R-LED chain) form their own chain groups. Generator-added PWR_FLAGs join the part that
   brings the power in. Connectors hang off the IC they serve rather than anchoring a group of their own.
2. **Blocks.** In a group, each part hangs off the pin it shares a wired net with, lined up so the wire
   is straight: a series part continues the line, a part to ground or a pull-up stands across it, and
   parts only on rails stand upright next to their rail pin (see ``_block``).
3. Groups are then packed on the page like single parts, connected groups next to each other.

Connectivity never depends on placement: the drawing step wires pins only where the path is clear and
labels the rest. This only decides where symbols go. Offsets stay on the 1.27 mm grid.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from circuit_core.datasheets import is_ground_name, net_voltage
from circuit_core.parts import parse_value

from .drawing import (
    GRID,
    STUB,
    Dir,
    Point,
    grow,
    label_length,
    orient,
    overlaps,
    rect_of,
    segment_hits_rect,
    shift,
    snap,
    transform,
    transform_dir,
    union,
)

STEP = 2.54
GROUP_GAP = 2.54
MAX_CHAIN = 6
RAIL_NODES = 9
_RAIL_NAME = re.compile(r"^(V(CC|DD|EE|SS|IN|BAT|BUS|SYS|MOT|PP|REF)\w*|AVDD|DVDD|VDDA|VDDIO|VPWR)$", re.I)

Extent = tuple[float, float, float, float]


@dataclass(frozen=True)
class PinGeom:
    number: str
    x: float
    y: float
    """Connection point relative to the symbol origin, sheet frame (y down)."""
    out: tuple[int, int]
    """Outward direction from the body (sheet frame)."""
    etype: str


@dataclass
class Group:
    key: str
    extent: Extent
    offsets: dict[str, tuple[float, float, int]] = field(default_factory=dict)
    """member -> (dx, dy, rotation)"""
    mirrored: set[str] = field(default_factory=set)
    """Members drawn mirrored (``mirror y``, before the rotation)."""
    wired_rails: set[str] = field(default_factory=set)
    """Rails drawn as wires inside this block (a regulator's input and output lines)."""


def _up(v: float) -> float:
    return round(-((-v) // STEP) * STEP, 4)


def plan_groups(order: list[str], pins: dict[str, list[PinGeom]],
                extent_of: Callable[[str, int, dict[str, float]], Extent],
                net_of: dict[tuple[str, str], str], net_nodes: dict[str, list[tuple[str, str]]],
                hints: dict[str, str] | None = None, values: dict[str, str] | None = None,
                upright: dict[str, Dir] | None = None, kinds: dict[str, str] | None = None) -> list[Group]:
    """Groups with member offsets for the parts in ``order`` (all on one page, none with a fixed position).

    ``extent_of(id, rotation, quiet)`` gives the space a part takes at a rotation, where ``quiet`` maps pins
    that will be wired to the room to keep beyond them instead of a label. ``values`` lets bulk capacitors go
    to the regulator that drives a rail and small ones to the IC they decouple. ``upright`` maps each net
    drawn with power symbols to the way its symbol points; ``kinds`` marks parts as ``"rc"`` (a resistor
    or capacitor, which may stand across a line as a pull-up or decoupling part)."""
    hints = hints or {}
    values = values or {}
    upright = upright or {}
    here = set(order)
    etype = {(cid, p.number): p.etype for cid in order for p in pins[cid]}

    def is_rail(net: str) -> bool:
        nodes = net_nodes.get(net, [])
        if (len(nodes) >= RAIL_NODES or net in upright or net_voltage(net) is not None or is_ground_name(net)
                or _RAIL_NAME.match(net)):
            return True
        powered = any(etype.get(n) in ("power_in", "power_out") for n in nodes)
        return powered and len(nodes) >= 3

    rail = {net: is_rail(net) for net in {n for (c, _), n in net_of.items() if c in here}}

    def nets(cid: str, signal: bool | None = None) -> list[str]:
        out = []
        for p in pins[cid]:
            net = net_of.get((cid, p.number))
            if net and (signal is None or rail[net] != signal):
                out.append(net)
        return out

    kinds = kinds or {}
    big = [c for c in order if len(pins[c]) >= 3 and c not in hints]
    # A connector that only brings power in (every pin on a rail, a supply among them) anchors a power-input
    # block: the bulk capacitors and power indicator on its rails gather around it instead of scattering.
    driven = {net_of.get((c, p.number)) for c in order if c not in hints for p in pins[c] if p.etype == "power_out"}
    feeds = [c for c in order if kinds.get(c) == "connector" and c not in hints and nets(c)
             and not nets(c, signal=True) and any(not is_ground_name(n) and n not in driven for n in nets(c))]
    # Any other connector hangs off the IC it serves (pins facing it) when there is one; otherwise it anchors.
    anchors = [c for c in big if kinds.get(c) != "connector"] or [c for c in big if c not in feeds]
    anchors += [c for c in feeds if c not in anchors]
    group_of: dict[str, str] = {a: a for a in anchors}
    satellites = [c for c in order if c not in group_of and c not in hints]

    def propagate() -> None:
        changed = True
        while changed:
            changed = False
            for s in satellites:
                if s in group_of:
                    continue
                for net in nets(s, signal=True):
                    joined = next((group_of[c] for c, _ in net_nodes[net] if c in group_of and c != s), None)
                    if joined:
                        group_of[s] = joined
                        changed = True
                        break

    # 1. Signal nets shared with an anchor (a termination resistor, a pull-up, a switch-node diode).
    for s in satellites:
        score: Counter[str] = Counter()
        for net in nets(s, signal=True):
            for c, _ in net_nodes[net]:
                if group_of.get(c) == c:
                    score[c] += 1
        if score:
            best = max(score.values())
            group_of[s] = next(a for a in anchors if score.get(a) == best)
    # 2. Through other parts (an LED behind its resistor).
    propagate()
    # 2b. A regulator (a small IC with a supply input and a supply output) is drawn like the textbook: its
    #     input and output rails are wires inside its block, with the parts on them hanging off those lines -
    #     input capacitors, output capacitors, a protection diode, a power LED, the output connector. Parts on
    #     the output rail only join when no other IC is powered from it (then decoupling stays by that IC).
    local: dict[str, set[str]] = {}
    for a in anchors:
        if len(pins[a]) > 5 or kinds.get(a) == "connector":
            continue
        ins = {net_of.get((a, p.number)) for p in pins[a] if p.etype == "power_in"} - {None}
        outs = {net_of.get((a, p.number)) for p in pins[a] if p.etype == "power_out"} - {None}
        ins = {n for n in ins if not is_ground_name(n)}
        if ins and outs:
            local[a] = {n for n in ins | outs if n}
    for s in satellites:
        if s in group_of:
            continue
        mine = set(nets(s, signal=False))
        for a, lines in local.items():
            if not mine & lines or not all(n in lines or is_ground_name(n) for n in mine):
                continue
            ins_a = {net_of.get((a, p.number)) for p in pins[a] if p.etype == "power_in"}
            if not mine & ins_a and any(etype.get((c, q)) == "power_in" for n in mine & lines
                                        for c, q in net_nodes[n] if c != a and c in here):
                continue
            group_of[s] = a
            break
    propagate()
    # 3. Parts only between rails: bulk capacitors to the part driving the rail, small ones (decoupling) and
    #    connectors to a part powered from it. Ground is ignored: every IC has one.
    load: Counter[str] = Counter()
    for s in satellites:
        if s in group_of or nets(s, signal=True):
            continue
        supplies = [n for n in nets(s, signal=False) if not is_ground_name(n)]
        powered_by = [a for a in anchors if any(etype.get((a, p)) == "power_in"
                                                for n in supplies for c, p in net_nodes[n] if c == a)]
        drivers = [a for a in anchors if any(etype.get((a, p)) == "power_out"
                                             for n in supplies for c, p in net_nodes[n] if c == a)]
        drivers += [f for f in feeds if f not in drivers and any(n in nets(f) for n in supplies)]
        farad = parse_value(values.get(s))
        bulk = farad is not None and farad > 1.5e-6 and len(pins[s]) == 2
        if kinds.get(s) == "connector" and not bulk:
            powered_by = [a for a in powered_by if a not in feeds]
        candidates = (drivers or powered_by) if bulk else (powered_by or drivers)
        if candidates:
            target = min(candidates, key=lambda a: (load[a], anchors.index(a)))
            group_of[s] = target
            load[target] += 1
    propagate()
    # 4. The rest: chains among themselves, at most MAX_CHAIN per group.
    rest = [s for s in satellites if s not in group_of]
    seen: set[str] = set()
    for s in rest:
        if s in seen:
            continue
        chain, queue = [], [s]
        while queue:
            c = queue.pop(0)
            if c in seen:
                continue
            seen.add(c)
            chain.append(c)
            for net in nets(c, signal=True):
                queue += [o for o, _ in net_nodes[net] if o in rest and o not in seen]
        # A chain only between one power input's rails (a power LED and its resistor) joins that input.
        rails_used = {n for c in chain for n in nets(c, signal=False)}
        feed = next((f for f in feeds if rails_used and rails_used <= set(nets(f))), None)
        if feed is not None and len(chain) <= MAX_CHAIN:
            for c in chain:
                group_of[c] = feed
            continue
        for i in range(0, len(chain), MAX_CHAIN):
            head = chain[i]
            for c in chain[i:i + MAX_CHAIN]:
                group_of[c] = head
    for v, target in hints.items():
        if v in here:
            group_of[v] = group_of.get(target, target) if target in here else v

    members: dict[str, list[str]] = defaultdict(list)
    for c in order:
        members[group_of[c]].append(c)
    ctx = _Ctx(pins, extent_of, net_of, net_nodes, upright, kinds, hints, values)
    groups = [_block(key, ms, key in anchors, ctx, local.get(key, set())) for key, ms in members.items()]
    return _order_groups(groups, group_of, net_of, net_nodes, rail)


@dataclass
class _Ctx:
    pins: dict[str, list[PinGeom]]
    extent_of: Callable[..., Extent]
    """(id, rotation, quiet pins, mirror) -> space taken"""
    net_of: dict[tuple[str, str], str]
    net_nodes: dict[str, list[tuple[str, str]]]
    upright: dict[str, Dir]
    kinds: dict[str, str]
    hints: dict[str, str]
    values: dict[str, str] = field(default_factory=dict)


def _turned(p: PinGeom, rotation: int, mirror: bool = False) -> tuple[Point, Dir]:
    x, y = transform(p.x, p.y, rotation, mirror)
    return (round(x, 4), round(y, 4)), transform_dir(p.out, rotation, mirror)


def _block(key: str, members: list[str], anchored: bool, ctx: _Ctx, local: set[str] | None = None) -> Group:
    """Place a group around its root so that the wires the drawing step will try are short and straight.

    Members hang off the pin they share a wired (signal) net with: a series part continues the line away from
    that pin, a part to ground (or a pull-up / decoupling R or C to a supply) stands across the line with its
    power symbol pointing the usual way. Parts only on rails stand upright next to the pin of their rail."""
    pins = ctx.pins

    def net(c: str, n: str) -> str | None:
        return ctx.net_of.get((c, n))

    local = local or set()

    def wired(n: str | None) -> bool:
        return n is not None and (n not in ctx.upright or n in local)

    def reach(n: str | None) -> float:
        """Room kept along a pin's wire for the net's label (every wired net keeps its name; a wired rail gets a
        power symbol on the wire instead)."""
        return STUB + label_length(n) if n and n not in local else 0.0

    def pin(c: str, n: str) -> PinGeom:
        return next(p for p in pins[c] if p.number == n)

    def same_side(c: str) -> bool:
        return len(pins[c]) >= 2 and len({p.out for p in pins[c]}) == 1

    def two_opposite(c: str) -> bool:
        ps = pins[c]
        return len(ps) == 2 and ps[0].out == (-ps[1].out[0], -ps[1].out[1])

    # Tree over wired nets, breadth first from the root (later also from parts placed beside a rail pin).
    parent: dict[str, tuple[str, str, str]] = {}
    children: dict[tuple[str, str], list[str]] = defaultdict(list)

    def to_rail(m: str, mpin: str) -> str | None:
        """For a two-pin part hanging by ``mpin``: the rail (drawn with power symbols) its other pin is on."""
        if not two_opposite(m):
            return None
        other = net(m, next(q.number for q in pins[m] if q.number != mpin))
        return other if other in ctx.upright and other not in local else None

    def grow_tree(start: str, done: set[str]) -> None:
        found = [start]
        i = 0
        while i < len(found):
            owner = found[i]
            i += 1
            for p in pins[owner]:
                n = net(owner, p.number)
                if not wired(n):
                    continue
                for m in members:
                    if m in (key, start) or m in parent or m in done or m in ctx.hints:
                        continue
                    mp = next((q for q in pins[m] if net(m, q.number) == n), None)
                    if mp is not None:
                        parent[m] = (owner, p.number, mp.number)
                        found.append(m)
        new = found[1:]
        # A part to a rail on a net that a two-pin part spanning two of the owner's lines (a crystal) also
        # ends on hangs off that part's end, so the crystal and its load capacitors stay one small block.
        def same_side_lines(m: str) -> bool:
            """Both of ``m``'s nets are on pins of its owner that point the same way (two rows of one side)."""
            o = parent[m][0]
            outs = [{p.out for p in pins[o] if net(o, p.number) == net(m, q.number)} for q in pins[m]]
            return bool(outs[0] & outs[1])

        spans = [m for m in new if two_opposite(m) and len({net(m, q.number) for q in pins[m]}) == 2
                 and all(wired(net(m, q.number)) for q in pins[m]) and same_side_lines(m)]
        for m in new:
            o, opin, mpin = parent[m]
            if m in spans or not is_ground_name(to_rail(m, mpin) or ""):
                continue
            b = next((b for b in spans if parent[b][0] == o and any(net(b, q.number) == net(o, opin)
                                                                   for q in pins[b])), None)
            if b is not None:
                parent[m] = (b, next(q.number for q in pins[b] if net(b, q.number) == net(o, opin)), mpin)
        for m in new:
            children[parent[m][:2]].append(m)

    grow_tree(key, set())

    placed: dict[str, tuple[float, float, int]] = {}
    mirrored: set[str] = set()
    boxes: dict[str, Extent] = {}
    shapes: dict[str, list[Extent]] = {}
    """Finer outlines for collisions: an IC as its body and the strips its labels and power symbols take."""
    scores: dict[tuple[float, float], int] = {}

    def outline(m: str) -> list[Extent]:
        return shapes.get(m) or [boxes[m]]

    def collides(box: Extent) -> bool:
        return any(overlaps(box, r) for m in boxes for r in outline(m))

    def abs_pin(c: str, n: str) -> tuple[Point, Dir]:
        dx, dy, r = placed[c]
        (x, y), out = _turned(pin(c, n), r, c in mirrored)
        return (round(dx + x, 4), round(dy + y, 4)), out

    def facing(c: str, p: PinGeom, want: Dir) -> tuple[int | None, bool]:
        """Turn ``c`` so pin ``p`` points ``want``; a connector is mirrored rather than turned half way round,
        which keeps pin 1 on top."""
        if same_side(c) and p.out[1] == 0 and want == (-p.out[0], 0):
            return 0, True
        return orient(p.out, want), False

    def search(e: Extent, anchor: Point, d: Dir, rel: Point, line: bool, skip: set[str],
               weights: tuple[int, int] = (1, 3), step: float = 0.0) -> tuple[float, float] | None:
        perp = (-d[1], d[0])
        best: tuple[int, float, float] | None = None
        for j in (0, 1, -1, 2, -2, 3, -3, 4, -4, 6, -6, 8, -8):
            for k in range(1, 25):
                score = weights[0] * k + weights[1] * abs(j)
                if best is not None and score >= best[0]:
                    break
                tx = anchor[0] + (d[0] * k + perp[0] * j) * STEP
                ty = anchor[1] + (d[1] * k + perp[1] * j) * STEP
                ox, oy = tx - rel[0], ty - rel[1]
                if step:
                    ox, oy = snap(ox, step), snap(oy, step)
                ox, oy = round(ox, 4), round(oy, 4)
                box = shift(e, ox, oy)
                if collides(box):
                    continue
                if line and j == 0 and any(segment_hits_rect(anchor, (tx, ty), r)
                                           for m in boxes if m not in skip for r in outline(m)):
                    continue
                best = (score, ox, oy)
                break
        if best is not None:
            scores[(best[1], best[2])] = best[0]
        return None if best is None else (best[1], best[2])

    def on_line(spot: tuple[float, float] | None, rel: Point, anchor: Point, d: Dir) -> bool:
        """The member's attaching pin landed on the owner pin's line (a straight wire is possible)."""
        if spot is None:
            return False
        end = (spot[0] + rel[0], spot[1] + rel[1])
        return abs(end[1] - anchor[1]) < 1e-6 if d[1] == 0 else abs(end[0] - anchor[0]) < 1e-6

    def put(c: str, rotation: int, e: Extent, spot: tuple[float, float] | None, mirror: bool = False) -> None:
        if spot is None:  # nowhere near: below everything placed so far
            x0 = min(b[0] for b in boxes.values())
            y1 = max(b[3] for b in boxes.values())
            spot = (_up(x0 - e[0]), _up(y1 + GROUP_GAP - e[1]))
        placed[c] = (spot[0], spot[1], rotation)
        if mirror:
            mirrored.add(c)
        boxes[c] = shift(e, *spot)

    # Root.
    r0 = 0
    if not anchored and two_opposite(key):
        rails = sorted((p for p in pins[key] if net(key, p.number) in ctx.upright),
                       key=lambda p: is_ground_name(net(key, p.number)))
        if rails:
            r0 = orient(rails[0].out, ctx.upright[net(key, rails[0].number) or ""]) or 0
        else:
            r0 = orient(pins[key][0].out, (-1, 0)) or 0
    q0 = {p.number: reach(net(key, p.number)) for p in pins[key] if (key, p.number) in children}
    m0 = (ctx.kinds.get(key) == "connector" and same_side(key) and pins[key][0].out == (-1, 0)
          and not any(wired(net(key, p.number)) for p in pins[key]))  # a power input faces its block
    placed[key] = (0.0, 0.0, r0)
    if m0:
        mirrored.add(key)
    boxes[key] = ctx.extent_of(key, r0, q0, m0)
    if len(pins[key]) >= 3:
        try:
            shapes[key] = list(ctx.extent_of(key, r0, q0, m0, True))
        except TypeError:  # an extent function without outlines
            pass

    def plan(c: str, cpin: str, owner: str, opin: str, d: Dir) -> tuple[int | None, bool, bool, str | None, bool]:
        """Rotation and mirroring for a member hanging off ``owner``'s pin ``opin`` (pointing ``d``), whether it
        stands across the line, for a bridge the owner's other pin it spans to, and whether it stands beside
        the pin instead (a capacitor to ground on a pin pointing up keeps its ground symbol pointing down)."""
        s = pin(c, cpin)
        back = (-d[0], -d[1])
        if two_opposite(c):
            o = next(p for p in pins[c] if p.number != cpin)
            n2 = net(c, o.number)
            here = net(owner, opin)
            if n2 in local and here in local and n2 != here:
                # Between a regulator's input and output (a protection diode): it lies over the regulator,
                # each end above its own pin.
                for q in pins[owner]:
                    if net(owner, q.number) == n2 and abs_pin(owner, q.number)[1] == back:
                        spans_over[c] = q.number
                        return orient(o.out, back), False, True, None, False
            if here in local and wired(n2) and n2 not in local:
                # A chain from a regulator's line to ground (resistor, then LED): it stands across the line
                # and runs down to ground.
                rest = [m for m in members if m != c and any(net(m, q.number) == n2 for q in pins[m])]
                ends = [to_rail(m, next(q.number for q in pins[m] if net(m, q.number) == n2)) for m in rest]
                if rest and all(e and is_ground_name(e) for e in ends):
                    return orient(o.out, ctx.upright[ends[0]]), False, True, None, False
            if wired(n2) and n2 != net(owner, opin):
                # A bridge between two pins on the same side of the owner (a termination resistor between
                # CANH and CANL): it stands across both lines, centred between them, so the lines can reach
                # its ends and still run straight on to a connector beyond it.
                a, _ = abs_pin(owner, opin)
                for q in pins[owner]:
                    if q.number == opin or net(owner, q.number) != n2:
                        continue
                    b, qd = abs_pin(owner, q.number)
                    if qd != d:
                        continue
                    gap = (b[1] - a[1]) if d[1] == 0 else (b[0] - a[0])
                    if gap:
                        toward = (0, 1 if gap > 0 else -1) if d[1] == 0 else (1 if gap > 0 else -1, 0)
                        return orient(o.out, toward), False, True, q.number, False
            if n2 in ctx.upright and (is_ground_name(n2) or ctx.kinds.get(c) == "rc"):
                if d[1] == 0:
                    return orient(o.out, ctx.upright[n2]), False, True, None, False
                if ctx.upright[n2] == back:
                    return orient(o.out, ctx.upright[n2]), False, True, None, True
            return orient(s.out, back), False, False, None, False
        if len(pins[c]) == 1 or same_side(c):
            return (*facing(c, s, back), False, None, False)
        return None, False, False, None, False

    spans_over: dict[str, str] = {}
    """Parts lying over their owner from one pin to its pin on the other side (owner pin number)."""
    bridges: set[str] = set()
    flat: set[str] = set()
    """Bridges laid along the owner's lines (a crystal with a capacitor to ground hanging off each end)."""

    def place_tree(start: str) -> None:
        """Wired members, nearest first; at one pin, a bridge first, then parts across the line, then the one
        continuing it."""
        queue = [start]
        while queue:
            owner = queue.pop(0)
            for p in pins[owner]:
                kids = children.get((owner, p.number), [])
                todo = [k for k in kids if k not in placed]
                if todo:
                    place_kids(owner, p.number, todo)
                queue += kids

    def place_kids(owner: str, opin: str, kids: list[str]) -> None:
        anchor, d = abs_pin(owner, opin)
        back = (-d[0], -d[1])
        plans = {c: plan(c, parent[c][2], owner, opin, d) for c in kids}
        for c in sorted(kids, key=lambda c: (not plans[c][2], plans[c][3] is None, kids.index(c))):
            rotation = plans[c][0] if plans[c][0] is not None else 0
            mirror, twin, beside = plans[c][1], plans[c][3], plans[c][4]
            line_at: Point | None = anchor
            cpin = parent[c][2]
            quiet = {cpin: 0.0}
            for q in pins[c]:
                if twin is not None:  # a bridge's ends are wired to the owner's rows: no label room needed
                    quiet[q.number] = 0.0
                elif (c, q.number) in children:
                    quiet[q.number] = reach(net(c, q.number))
            e = ctx.extent_of(c, rotation, quiet, mirror)
            rel, _ = _turned(pin(c, cpin), rotation, mirror)
            skip = {owner} | {x for s in kids if s in placed and plans[s][2] for x in (s, f"~{s}")}
            rail = to_rail(c, cpin)
            grounded_ends = [q for q in pins[c] if any(is_ground_name(to_rail(k, parent[k][2]) or "")
                                                       for k in children.get((c, q.number), []))]
            if twin is not None and d[1] == 0 and len(grounded_ends) == 2:
                # A crystal with a load capacitor at each end: it lies along the lines below the lower one, the
                # lower line's end toward the owner, so both lines drop straight onto its ends and the
                # capacitors hang straight down from them.
                b, _ = abs_pin(owner, twin)
                low_net, low_y = (net(owner, twin), b[1]) if b[1] > anchor[1] else (net(owner, opin), anchor[1])
                near = next(q for q in pins[c] if net(c, q.number) == low_net)
                rotation, mirror = orient(near.out, back) or 0, False
                e = ctx.extent_of(c, rotation, {q.number: 0.0 for q in pins[c]}, False)
                rel_near, _ = _turned(near, rotation, False)
                # Room before the near end: the lower line's label, then the near capacitor's text (it is
                # mirrored to face out).
                cap = next((k for k in children.get((c, near.number), []) if to_rail(k, parent[k][2])), None)
                band = [r for r in outline(owner) if r[3] > low_y - STEP and r[1] < low_y + 6 * STEP]
                edge = (max(r[2] for r in band) if d[0] > 0 else min(r[0] for r in band)) if band else anchor[0]
                room = max(reach(low_net) + GRID, (edge - anchor[0]) * d[0])
                if cap is not None:
                    up = ctx.upright[to_rail(cap, parent[cap][2]) or ""]
                    ce = ctx.extent_of(cap, orient(pin(cap, parent[cap][2]).out, (-up[0], -up[1])) or 0,
                                       {parent[cap][2]: 0.0}, True)
                    room += -ce[0]
                start = (round(anchor[0] + d[0] * -(-room // STEP) * STEP, 4), low_y)
                spot = search(e, start, (0, 1), rel_near, False, skip, weights=(1, 1))
                bridges.add(c)
                flat.add(c)
                line_at = None
            elif c in spans_over:
                b, _ = abs_pin(owner, spans_over[c])
                ends = [_turned(q, rotation, False)[0] for q in pins[c]]
                rel_mid = ((ends[0][0] + ends[1][0]) / 2, (ends[0][1] + ends[1][1]) / 2)
                top = min(r[1] for r in outline(owner))
                start = (snap((anchor[0] + b[0]) / 2), round(STEP * math.floor(top / STEP), 4))
                e = ctx.extent_of(c, rotation, {q.number: 0.0 for q in pins[c]}, False)
                spot = search(e, start, (0, -1), rel_mid, False, skip)
                line_at = None
            elif twin is not None:  # centre the bridge between the two pin rows
                b, _ = abs_pin(owner, twin)
                o_rel, _ = _turned(next(q for q in pins[c] if q.number != cpin), rotation, mirror)
                i = 1 if d[1] == 0 else 0
                offset = snap((b[i] - anchor[i]) / 2 - ((rel[i] + o_rel[i]) / 2 - rel[i]))
                start = ((anchor[0], round(anchor[1] + offset, 4)) if i
                         else (round(anchor[0] + offset, 4), anchor[1]))
                spot = search(e, start, d, rel, False, skip)
                bridges.add(c)
                line_at = None
            elif owner in bridges and rail is not None and (owner in flat or d == ctx.upright[rail]):
                # A capacitor to ground hangs straight down from its end of the crystal (or from the lower end of
                # an upright bridge). At the left end of a crystal lying along the lines it is mirrored, so its
                # text faces out and the two capacitors fit side by side under the crystal.
                down = ctx.upright[rail]
                rotation = orient(pin(c, cpin).out, (-down[0], -down[1])) or 0
                # Text faces away from the rest of the block: out at the left end of a lying crystal, away from
                # the IC under an upright bridge.
                ic = parent.get(owner, (owner,))[0]
                mirror = (d[0] < 0) if owner in flat else (
                    ic in boxes and (boxes[ic][0] + boxes[ic][2]) / 2 > anchor[0])
                e = ctx.extent_of(c, rotation, quiet, mirror)
                rel, _ = _turned(pin(c, cpin), rotation, mirror)
                spot = search(e, anchor, down, rel, True, skip)
            elif beside:
                # Beside the pin, standing upright; the wire goes out of the pin, across and into the part.
                options = [sp for side in ((1, 0), (-1, 0)) if (sp := search(e, anchor, side, rel, False, skip))]
                spot = min(options, key=lambda sp: scores[sp]) if options else None
                line_at = None
            else:
                if plans[c][2] and d[0] < 0 and ctx.kinds.get(c) == "rc" and twin is None:
                    # Across a line running left: mirrored, so its text faces out, not back at the owner.
                    mirror = True
                    e = ctx.extent_of(c, rotation, quiet, mirror)
                    rel, _ = _turned(pin(c, cpin), rotation, mirror)
                spot = search(e, anchor, d, rel, True, skip, weights=(1, 5))  # stay on the line if at all possible
                turn = (orient(pin(c, cpin).out, back)
                        if plans[c][2] and two_opposite(c) and ctx.kinds.get(c) != "rc" else None)
                if turn is not None and turn != rotation:
                    # No room to stand across the line near the pin (other parts take the space beside it):
                    # continue the line instead when that keeps the wire straight and lands clearly closer
                    # - e.g. an LED to ground after its resistor, next to a crystal block.
                    e2 = ctx.extent_of(c, turn, quiet, False)
                    rel2, _ = _turned(pin(c, cpin), turn, False)
                    spot2 = search(e2, anchor, d, rel2, True, skip, weights=(1, 5))
                    if on_line(spot2, rel2, anchor, d) and (
                            not on_line(spot, rel, anchor, d) or scores[spot2] + 3 <= scores[spot]):
                        rotation, mirror, e, rel, spot = turn, False, e2, rel2, spot2
            put(c, rotation, e, spot, mirror)
            if spot is not None and line_at is not None:  # keep the straight wire's path free for later parts
                end = (spot[0] + rel[0], spot[1] + rel[1])
                if end[0] == line_at[0] or end[1] == line_at[1]:
                    boxes[f"~{c}"] = grow(rect_of([line_at, end]), 1.0)
            if c in bridges:  # a crystal's capacitors come with it, before other parts take the room around it
                for q in pins[c]:
                    todo = [k for k in children.get((c, q.number), []) if k not in placed]
                    if todo:
                        place_kids(c, q.number, todo)

    place_tree(key)

    # Members only on rails: upright, in a row beside the pin of their rail (supply before ground), parts
    # first and connectors outermost. Generator-added PWR_FLAGs go right above the part feeding their rail.
    in_tree = list(placed)
    flags = [m for m in members if m in ctx.hints and m not in placed]
    rest = sorted((m for m in members if m not in placed and m not in flags),
                  key=lambda m: (ctx.kinds.get(m) == "connector", members.index(m)))
    loose = []
    for m in rest:
        if m in placed:  # already hung off a part placed earlier in this loop (an LED after its resistor)
            continue
        rails = sorted({n for q in pins[m] if (n := net(m, q.number)) in ctx.upright},
                       key=lambda n: (is_ground_name(n), n))
        farad = parse_value(ctx.values.get(m)) if ctx.kinds.get(m) == "rc" else None
        decoupling = farad is not None and farad <= 1.5e-6  # a small capacitor: by the IC it serves
        owners = sorted(reversed(in_tree), key=lambda o: (ctx.kinds.get(o) == "connector",
                                                          decoupling and len(pins[o]) < 3))
        found = next(((o, q.number) for n in rails for o in owners for q in pins[o] if net(o, q.number) == n), None)
        if found is None:
            loose.append(m)
            continue
        anchor, d = abs_pin(*found)
        sides = [(anchor, d)]
        if d[0] == 0:  # a pin on top or bottom: also try beside it (beside the owner); the closest spot wins
            if found[0] in shapes:  # the owner's outline leaves the corners next to its pins free
                sides += [(anchor, (1, 0)), (anchor, (-1, 0))]
            else:
                ob = boxes[found[0]]
                sides += [((ob[2], anchor[1]), (1, 0)), ((ob[0], anchor[1]), (-1, 0))]
        options = []
        for side_anchor, sd in sides:
            mirror = False
            if same_side(m):
                turn, mirror = facing(m, pins[m][0], (-sd[0], -sd[1]))
                rotation = turn or 0
            else:
                rail_pins = sorted((q for q in pins[m] if net(m, q.number) in ctx.upright),
                                   key=lambda q: is_ground_name(net(m, q.number)))
                rotation = orient(rail_pins[0].out, ctx.upright[net(m, rail_pins[0].number) or ""]) or 0
            e = ctx.extent_of(m, rotation, {}, mirror)
            rel = (e[0] if sd[0] > 0 else e[2] if sd[0] < 0 else (e[0] + e[2]) / 2,
                   e[1] if sd[1] > 0 else e[3] if sd[1] < 0 else (e[1] + e[3]) / 2)
            spot = search(e, side_anchor, sd, rel, False, set(), step=STEP)
            if spot is not None:
                options.append((scores[spot], len(options), rotation, mirror, e, spot))
        if options:
            _, _, rotation, mirror, e, spot = min(options)
            put(m, rotation, e, spot, mirror)
        else:
            put(m, 0, ctx.extent_of(m, 0, {}), None)
        # Parts wired only to this one (a power LED after its resistor) hang off it like any other member.
        grow_tree(m, set(placed))
        place_tree(m)
    for m in flags:
        n = next((net(m, q.number) for q in pins[m] if net(m, q.number)), None)
        rotation = 0
        if n in ctx.upright and len(pins[m]) == 1:
            rotation = orient(pins[m][0].out, ctx.upright[n]) or 0
        e = ctx.extent_of(m, rotation, {})
        target = ctx.hints[m] if ctx.hints[m] in placed else key
        fb = boxes[target]
        put(m, rotation, e, search(e, ((fb[0] + fb[2]) / 2, fb[1]), (0, -1), ((e[0] + e[2]) / 2, e[3]), False,
                                   set(), weights=(2, 1), step=STEP))
    for m in loose:
        if m in placed:
            continue
        e = ctx.extent_of(m, 0, {})
        x0, _, x1, y1 = union(*boxes.values())
        put(m, 0, e, search(e, ((x0 + x1) / 2, y1), (0, 1), ((e[0] + e[2]) / 2, e[1]), False, set(),
                            weights=(3, 1), step=STEP))

    real = {m: b for m, b in boxes.items() if not m.startswith("~")}
    return Group(key, union(*real.values()), placed, mirrored, set(local))


def _order_groups(groups: list[Group], group_of: dict[str, str], net_of, net_nodes, rail) -> list[Group]:
    """Keep the circuit's order, but put groups that share a signal net next to each other."""
    index = {g.key: g for g in groups}
    adjacent: dict[str, list[str]] = defaultdict(list)
    for net, nodes in net_nodes.items():
        if rail.get(net, True):
            continue
        keys = list(dict.fromkeys(group_of[c] for c, _ in nodes if c in group_of))
        for a in keys:
            for b in keys:
                if a != b and b not in adjacent[a]:
                    adjacent[a].append(b)
    ordered, seen = [], set()
    for g in groups:
        queue = [g.key]
        while queue:
            k = queue.pop(0)
            if k in seen:
                continue
            seen.add(k)
            ordered.append(index[k])
            queue += [n for n in adjacent[k] if n not in seen]
    return ordered
