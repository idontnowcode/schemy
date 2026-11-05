"""Changes made in KiCad, as patch operations (external edits, step 2: connections, values, parts).

The netlist KiCad extracts from the edited schematic (``kicad-cli sch export netlist``) is compared with the
Circuit IR. Symbols are matched to IR components by their hidden ``AICS_ID`` field (the reference can change);
a symbol without one was added in KiCad. What comes out is a list of ordinary patch operations - remove, add,
set a value or footprint, rename a net, disconnect, connect - so it goes through the same proposal, diff,
checks and approval as any other change. Nothing here writes the circuit.

Connectivity is compared pin by pin. KiCad names a net after its label or power symbol; a net it had to name
itself (``Net-(R1-Pad2)``) keeps the IR name of the net it overlaps most, else gets a new name from KiCad's.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from circuit_core import CircuitIR
from circuit_core.models import Component, Pin
from circuit_core.patch import (
    AddComponent,
    Connect,
    Disconnect,
    PinRef,
    RemoveComponent,
    RenameNet,
    SetProperty,
)

from . import sexpr
from .netlist import parse_kicad_netlist
from .sexpr import child, children, unquote
from .symbols import SymbolLibrary, SymbolLibraryError

_REF = re.compile(r"^[A-Z]+[0-9]+$")
_AUTO_NET = re.compile(r"^(Net-\(|unconnected-\()")


@dataclass(frozen=True)
class KiCadPart:
    reference: str
    value: str
    footprint: str | None
    lib_id: str
    aics_id: str | None


@dataclass
class KiCadEdits:
    ops: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """What was read, in Korean, for the proposal's rationale."""
    skipped: list[str] = field(default_factory=list)
    """Changes found but not imported (and why)."""
    counts: dict[str, int] = field(default_factory=dict)


def read_parts(netlist_text: str) -> dict[str, KiCadPart]:
    """Symbols of a KiCad netlist with their AICS_ID (power symbols and flags, ``#...``, are left out)."""
    root = sexpr.parse(netlist_text)
    out: dict[str, KiCadPart] = {}
    comps = child(root, "components")
    for comp in children(comps, "comp") if comps else ():
        ref = unquote(child(comp, "ref")[1])
        if ref.startswith("#"):
            continue
        value = unquote(child(comp, "value")[1]) if child(comp, "value") else ""
        fp = child(comp, "footprint")
        libsource = child(comp, "libsource")
        lib = f"{unquote(child(libsource, 'lib')[1])}:{unquote(child(libsource, 'part')[1])}" if libsource else ""
        aics = None
        fields = child(comp, "fields")
        for f in children(fields, "field") if fields else ():
            name = child(f, "name")
            if name is not None and unquote(name[1]) == "AICS_ID" and len(f) > 2:
                aics = unquote(f[2])
        out[ref] = KiCadPart(ref, value, unquote(fp[1]) if fp and len(fp) > 1 and unquote(fp[1]) else None, lib, aics)
    return out


def _net_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_+\-]+", "_", name).strip("_")[:60] or "N"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}_{n}", n + 1
    return out


def edits_from_kicad(circuit: CircuitIR, netlist_text: str, library: SymbolLibrary | None) -> KiCadEdits:
    """Patch operations that make ``circuit`` match the KiCad netlist (connections, values, parts)."""
    result = KiCadEdits()
    parts = read_parts(netlist_text)
    netlist = parse_kicad_netlist(netlist_text)
    comps = {c.id: c for c in circuit.components}

    # Parts: matched by AICS_ID, added (no AICS_ID), removed (AICS_ID gone).
    id_of_ref: dict[str, str] = {}
    added: list[Component] = []
    for ref, part in parts.items():
        if part.aics_id and part.aics_id in comps:
            id_of_ref[ref] = part.aics_id
            if ref != comps[part.aics_id].reference:
                result.skipped.append(f"{comps[part.aics_id].reference} → {ref}: 기호 변경은 가져오지 않습니다.")
            if part.lib_id and part.lib_id != comps[part.aics_id].symbol_id:
                result.skipped.append(f"{ref}: 심볼 교체({comps[part.aics_id].symbol_id} → {part.lib_id})는 가져오지 "
                                      "않습니다. 제안에서 부품을 지우고 새로 넣으세요.")
            continue
        if part.aics_id:
            result.skipped.append(f"{ref}: AICS_ID '{part.aics_id}'에 맞는 부품이 회로에 없습니다.")
            continue
        if not _REF.match(ref):
            result.skipped.append(f"{ref}: 기호가 정해지지 않았습니다(KiCad에서 주석 달기 후 저장).")
            continue
        if any(c.reference == ref for c in circuit.components):
            result.skipped.append(f"{ref}: 회로의 다른 부품과 기호가 같습니다.")
            continue
        try:
            lib_pins = library.get(part.lib_id).pins if library is not None else None
        except SymbolLibraryError:
            lib_pins = None
        if lib_pins is None:
            result.skipped.append(f"{ref}: 심볼 {part.lib_id}을(를) 라이브러리에서 찾지 못해 추가하지 않습니다.")
            continue
        cid = _net_name(f"kicad-{ref.lower()}", set(comps) | {c.id for c in added}).lower()
        seen: set[str] = set()
        pins = []
        for p in lib_pins:
            if p.number not in seen:
                seen.add(p.number)
                pins.append(Pin(number=p.number, name=p.name or None))
        added.append(Component(id=cid, reference=ref, value=part.value or part.lib_id.split(":")[-1],
                               symbol_id=part.lib_id, footprint_id=part.footprint, pins=tuple(pins),
                               description="KiCad에서 추가"))
        id_of_ref[ref] = cid
    kept = set(id_of_ref.values())
    removed = [c for c in circuit.components if c.id not in kept]

    ops: list = [RemoveComponent(component_id=c.id, expect_reference=c.reference) for c in removed]
    ops += [AddComponent(component=c) for c in added]
    for ref, cid in id_of_ref.items():
        if cid not in comps:
            continue
        part, comp = parts[ref], comps[cid]
        if part.value and part.value != comp.value:
            ops.append(SetProperty(component_id=cid, field="value", expect=comp.value, value=part.value))
        if part.footprint != comp.footprint_id and part.footprint is not None:
            ops.append(SetProperty(component_id=cid, field="footprint_id", expect=comp.footprint_id,
                                   value=part.footprint))

    # Connections, pin by pin.
    current: dict[tuple[str, str], str] = {(n.component_id, n.pin): net.name for net in circuit.nets
                                          for n in net.nodes if n.component_id in kept}
    ir_nodes: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for key, name in current.items():
        ir_nodes[name].add(key)
    kicad_nets: dict[str, set[tuple[str, str]]] = {}
    for name, nodes in netlist.nets.items():
        mapped = {(id_of_ref[r], p) for r, p in nodes if r in id_of_ref}
        if mapped:
            kicad_nets[name] = mapped

    names_in_kicad = set(kicad_nets)
    renames: dict[str, str] = {}
    final_of: dict[str, str] = {}
    taken = set(ir_nodes) | names_in_kicad
    claimed: set[str] = set()
    for name in sorted(kicad_nets, key=lambda n: (bool(_AUTO_NET.match(n)), n)):
        nodes = kicad_nets[name]
        if name in ir_nodes:
            final_of[name] = name
            claimed.add(name)
            continue
        if not _AUTO_NET.match(name):
            # A label written in KiCad: the same pins as an IR net whose name is gone means a rename.
            same = next((n for n, ns in ir_nodes.items() if ns == nodes and n not in names_in_kicad
                         and n not in claimed), None)
            if same is not None and re.fullmatch(r"[A-Za-z0-9_+\-]+", name):
                renames[same] = name
                final_of[name] = name
                claimed.add(same)
                continue
            final_of[name] = _net_name(name, taken - {name}) if not re.fullmatch(r"[A-Za-z0-9_+\-]+", name) else name
            taken.add(final_of[name])
            continue
        best = max(((len(nodes & ns), n) for n, ns in ir_nodes.items() if n not in claimed and n not in names_in_kicad),
                   default=(0, None))
        if best[0] > 0:
            final_of[name] = best[1]
            claimed.add(best[1])
        else:
            final_of[name] = _net_name(name, taken)
            taken.add(final_of[name])

    for old, new in renames.items():
        ops.append(RenameNet(net=old, new_name=new))
    current = {k: renames.get(v, v) for k, v in current.items()}
    desired = {key: final_of[name] for name, nodes in kicad_nets.items() for key in nodes}

    leave: dict[str, list[PinRef]] = defaultdict(list)
    join: dict[str, list[PinRef]] = defaultdict(list)
    for key in sorted(set(current) | set(desired)):
        was, now = current.get(key), desired.get(key)
        if was == now:
            continue
        if was is not None:
            leave[was].append(PinRef(component_id=key[0], pin=key[1]))
        if now is not None:
            join[now].append(PinRef(component_id=key[0], pin=key[1]))
    ops += [Disconnect(net=net, nodes=nodes) for net, nodes in sorted(leave.items())]
    ops += [Connect(net=net, nodes=nodes) for net, nodes in sorted(join.items())]

    refs = {c.id: c.reference for c in [*circuit.components, *added]}
    result.ops = ops
    result.counts = {"removed": len(removed), "added": len(added),
                     "properties": sum(isinstance(o, SetProperty) for o in ops), "renamed": len(renames),
                     "pins_changed": sum(len(v) for v in join.values()) + sum(
                         1 for net, nodes in leave.items() for n in nodes if (n.component_id, n.pin) not in desired)}
    if removed:
        result.notes.append("KiCad에서 지운 부품: " + ", ".join(c.reference for c in removed) + ".")
    if added:
        result.notes.append("KiCad에서 추가한 부품: " + ", ".join(f"{c.reference}({c.symbol_id})" for c in added)
                            + ". 위치는 자동 배치로 둡니다.")
    props = [o for o in ops if isinstance(o, SetProperty)]
    if props:
        result.notes.append("값·풋프린트 변경: " + ", ".join(
            f"{refs[o.component_id]} {'값' if o.field == 'value' else '풋프린트'} {o.expect or '없음'} → {o.value}"
            for o in props) + ".")
    if renames:
        result.notes.append("Net 이름 변경: " + ", ".join(f"{a} → {b}" for a, b in renames.items()) + ".")
    if result.counts["pins_changed"]:
        result.notes.append(f"연결이 바뀐 핀 {result.counts['pins_changed']}개.")
    return result
