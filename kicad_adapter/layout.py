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

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from circuit_core.datasheets import is_ground_name, net_voltage
from circuit_core.parts import parse_value

from .drawing import (
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
    # A connector hangs off the IC it serves (pins facing it) when there is one; otherwise it anchors a group.
    anchors = [c for c in big if kinds.get(c) != "connector"] or big
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
        farad = parse_value(values.get(s))
        bulk = farad is not None and farad > 1.5e-6 and len(pins[s]) == 2
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
    groups = [_block(key, ms, key in anchors, ctx) for key, ms in members.items()]
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


def _block(key: str, members: list[str], anchored: bool, ctx: _Ctx) -> Group:
    """Place a group around its root so that the wires the drawing step will try are short and straight.

    Members hang off the pin they share a wired (signal) net with: a series part continues the line away from
    that pin, a part to ground (or a pull-up / decoupling R or C to a supply) stands across the line with its
    power symbol pointing the usual way. Parts only on rails stand upright next to the pin of their rail."""
    pins = ctx.pins

    def net(c: str, n: str) -> str | None:
        return ctx.net_of.get((c, n))

    def wired(n: str | None) -> bool:
        return n is not None and n not in ctx.upright

    def reach(n: str | None) -> float:
        """Room kept along a pin's wire for the net's label (every wired net keeps its name)."""
        return STUB + label_length(n) if n else 0.0

    def pin(c: str, n: str) -> PinGeom:
        return next(p for p in pins[c] if p.number == n)

    def same_side(c: str) -> bool:
        return len(pins[c]) >= 2 and len({p.out for p in pins[c]}) == 1

    def two_opposite(c: str) -> bool:
        ps = pins[c]
        return len(ps) == 2 and ps[0].out == (-ps[1].out[0], -ps[1].out[1])

    # Tree over wired nets, breadth first from the root.
    parent: dict[str, tuple[str, str, str]] = {}
    tree = [key]
    i = 0
    while i < len(tree):
        owner = tree[i]
        i += 1
        for p in pins[owner]:
            n = net(owner, p.number)
            if not wired(n):
                continue
            for m in members:
                if m == key or m in parent:
                    continue
                mp = next((q for q in pins[m] if net(m, q.number) == n), None)
                if mp is not None:
                    parent[m] = (owner, p.number, mp.number)
                    tree.append(m)
    children: dict[tuple[str, str], list[str]] = defaultdict(list)
    for m in tree[1:]:
        children[parent[m][:2]].append(m)

    placed: dict[str, tuple[float, float, int]] = {}
    mirrored: set[str] = set()
    boxes: dict[str, Extent] = {}
    scores: dict[tuple[float, float], int] = {}

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
            for k in range(1, 17):
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
                if any(overlaps(box, b) for b in boxes.values()):
                    continue
                if line and j == 0 and any(segment_hits_rect(anchor, (tx, ty), b)
                                           for m, b in boxes.items() if m not in skip):
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
    placed[key] = (0.0, 0.0, r0)
    boxes[key] = ctx.extent_of(key, r0, q0)

    def plan(c: str, cpin: str, owner: str, opin: str, d: Dir) -> tuple[int | None, bool, bool, str | None]:
        """Rotation and mirroring for a member hanging off ``owner``'s pin ``opin`` (pointing ``d``), whether it
        stands across the line, and for a bridge the owner's other pin it spans to."""
        s = pin(c, cpin)
        back = (-d[0], -d[1])
        if two_opposite(c):
            o = next(p for p in pins[c] if p.number != cpin)
            n2 = net(c, o.number)
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
                        return orient(o.out, toward), False, True, q.number
            if n2 in ctx.upright and d[1] == 0 and (is_ground_name(n2) or ctx.kinds.get(c) == "rc"):
                return orient(o.out, ctx.upright[n2]), False, True, None
            return orient(s.out, back), False, False, None
        if len(pins[c]) == 1 or same_side(c):
            return (*facing(c, s, back), False, None)
        return None, False, False, None

    bridges: set[str] = set()

    def under_bridge(c: str, cpin: str, n: str | None, quiet: dict[str, float], skip: set[str]):
        if not two_opposite(c):
            return None
        rail = net(c, next(q.number for q in pins[c] if q.number != cpin))
        if rail not in ctx.upright:
            return None
        for b in sorted(bridges):
            for bp in pins[b]:
                if net(b, bp.number) != n:
                    continue
                a, bd = abs_pin(b, bp.number)
                if bd != ctx.upright[rail]:
                    continue
                rotation = orient(pin(c, cpin).out, (-bd[0], -bd[1])) or 0
                e = ctx.extent_of(c, rotation, quiet, False)
                rel, _ = _turned(pin(c, cpin), rotation, False)
                spot = search(e, a, bd, rel, True, skip | {b})
                if on_line(spot, rel, a, bd):
                    return rotation, False, e, rel, spot, a, bd
        return None

    # Wired members, nearest first; at one pin, parts across the line come before the one continuing it.
    queue = [key]
    while queue:
        owner = queue.pop(0)
        for p in pins[owner]:
            kids = children.get((owner, p.number), [])
            if not kids:
                continue
            anchor, d = abs_pin(owner, p.number)
            plans = {c: plan(c, parent[c][2], owner, p.number, d) for c in kids}
            for c in sorted(kids, key=lambda c: (not plans[c][2], kids.index(c))):
                rotation = plans[c][0] if plans[c][0] is not None else 0
                mirror, twin = plans[c][1], plans[c][3]
                line_at = anchor
                cpin = parent[c][2]
                quiet = {cpin: 0.0}
                for q in pins[c]:
                    if (c, q.number) in children:
                        quiet[q.number] = reach(net(c, q.number))
                    elif twin is not None:  # a bridge's other end is wired to the owner's other row, no label
                        quiet[q.number] = 0.0
                e = ctx.extent_of(c, rotation, quiet, mirror)
                rel, _ = _turned(pin(c, cpin), rotation, mirror)
                skip = {owner} | {x for s in kids if s in placed and plans[s][2] for x in (s, f"~{s}")}
                if twin is not None:  # centre the bridge between the two pin rows
                    b, _ = abs_pin(owner, twin)
                    o_rel, _ = _turned(next(q for q in pins[c] if q.number != cpin), rotation, mirror)
                    i = 1 if d[1] == 0 else 0
                    offset = snap((b[i] - anchor[i]) / 2 - ((rel[i] + o_rel[i]) / 2 - rel[i]))
                    start = ((anchor[0], round(anchor[1] + offset, 4)) if i
                             else (round(anchor[0] + offset, 4), anchor[1]))
                    spot = search(e, start, d, rel, False, skip)
                    bridges.add(c)
                elif (below := under_bridge(c, cpin, net(owner, p.number), quiet, skip)) is not None:
                    # A capacitor to ground on a net a bridge (a crystal) also ends on, where that end points
                    # toward ground: it hangs straight below the bridge, the textbook crystal circuit.
                    rotation, mirror, e, rel, spot, line_at, _ = below
                else:
                    spot = search(e, anchor, d, rel, True, skip)
                    turn = orient(pin(c, cpin).out, (-d[0], -d[1])) if plans[c][2] and two_opposite(c) else None
                    if turn is not None and turn != rotation:
                        # No room to stand across the line near the pin (other parts take the space beside
                        # it): continue the line instead when that keeps the wire straight and lands clearly
                        # closer - e.g. an LED to ground after its resistor, next to a crystal block.
                        e2 = ctx.extent_of(c, turn, quiet, False)
                        rel2, _ = _turned(pin(c, cpin), turn, False)
                        spot2 = search(e2, anchor, d, rel2, True, skip)
                        if on_line(spot2, rel2, anchor, d) and (
                                not on_line(spot, rel, anchor, d) or scores[spot2] + 3 <= scores[spot]):
                            rotation, mirror, e, rel, spot = turn, False, e2, rel2, spot2
                put(c, rotation, e, spot, mirror)
                if spot is not None:  # keep the straight wire's path free for later parts
                    end = (spot[0] + rel[0], spot[1] + rel[1])
                    if end[0] == line_at[0] or end[1] == line_at[1]:
                        boxes[f"~{c}"] = grow(rect_of([line_at, end]), 1.0)
                queue.append(c)

    # Members only on rails: upright, in a row beside the pin of their rail (supply before ground), parts
    # first and connectors outermost. Generator-added PWR_FLAGs go right above the part feeding their rail.
    in_tree = list(placed)
    flags = [m for m in members if m in ctx.hints and m not in placed]
    rest = sorted((m for m in members if m not in placed and m not in flags),
                  key=lambda m: (ctx.kinds.get(m) == "connector", members.index(m)))
    loose = []
    for m in rest:
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
        if d[0] == 0:  # a pin on top or bottom: also try beside the owner; the closest spot wins
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
        e = ctx.extent_of(m, 0, {})
        x0, _, x1, y1 = union(*boxes.values())
        put(m, 0, e, search(e, ((x0 + x1) / 2, y1), (0, 1), ((e[0] + e[2]) / 2, e[1]), False, set(),
                            weights=(3, 1), step=STEP))

    real = {m: b for m, b in boxes.items() if not m.startswith("~")}
    return Group(key, union(*real.values()), placed, mirrored)


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
