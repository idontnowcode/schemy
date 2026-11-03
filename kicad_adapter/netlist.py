"""Compare the connectivity KiCad extracted from a schematic with the Circuit IR.

The netlist comes from ``kicad-cli sch export netlist --format kicadsexpr``.
Nets are compared as sets of ``(reference, pin number)`` nodes, and net names
are compared after dropping the root-sheet prefix ``/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from circuit_core import CircuitIR

from . import sexpr
from .sexpr import child, children, unquote

Node = tuple[str, str]


@dataclass(frozen=True)
class NetlistComponent:
    reference: str
    value: str
    lib_id: str


@dataclass(frozen=True)
class Netlist:
    components: dict[str, NetlistComponent]
    nets: dict[str, frozenset[Node]]
    """Named nets. KiCad's per-pin ``unconnected-(...)`` nets are excluded."""
    unconnected: frozenset[Node]


def parse_kicad_netlist(text: str) -> Netlist:
    root = sexpr.parse(text)
    components: dict[str, NetlistComponent] = {}
    comps = child(root, "components")
    for comp in children(comps, "comp") if comps else ():
        ref = unquote(child(comp, "ref")[1])
        value = unquote(child(comp, "value")[1])
        libsource = child(comp, "libsource")
        lib = unquote(child(libsource, "lib")[1]) if libsource else ""
        part = unquote(child(libsource, "part")[1]) if libsource else ""
        components[ref] = NetlistComponent(ref, value, f"{lib}:{part}")

    nets: dict[str, frozenset[Node]] = {}
    unconnected: set[Node] = set()
    net_block = child(root, "nets")
    for net in children(net_block, "net") if net_block else ():
        name = unquote(child(net, "name")[1])
        nodes = frozenset(
            (unquote(child(n, "ref")[1]), unquote(child(n, "pin")[1])) for n in children(net, "node")
        )
        if name.startswith("unconnected-(") and len(nodes) == 1:
            unconnected |= nodes
            continue
        nets[name[1:] if name.startswith("/") and name.count("/") == 1 else name] = nodes
    return Netlist(components, nets, frozenset(unconnected))


def ir_nets(circuit: CircuitIR) -> dict[str, frozenset[Node]]:
    refs = {c.id: c.reference for c in circuit.components}
    return {
        net.name: frozenset((refs[n.component_id], n.pin) for n in net.nodes)
        for net in circuit.nets
    }


@dataclass
class ConnectivityDiff:
    missing_nets: dict[str, frozenset[Node]] = field(default_factory=dict)
    unexpected_nets: dict[str, frozenset[Node]] = field(default_factory=dict)
    changed_nets: dict[str, tuple[frozenset[Node], frozenset[Node]]] = field(default_factory=dict)
    component_mismatches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing_nets or self.unexpected_nets or self.changed_nets or self.component_mismatches)

    def describe(self) -> str:
        if self.ok:
            return "netlist matches the Circuit IR"
        lines = []
        for name, nodes in sorted(self.missing_nets.items()):
            lines.append(f"missing net {name}: {sorted(nodes)}")
        for name, nodes in sorted(self.unexpected_nets.items()):
            lines.append(f"unexpected net {name}: {sorted(nodes)}")
        for name, (want, got) in sorted(self.changed_nets.items()):
            lines.append(f"net {name}: IR {sorted(want)} != KiCad {sorted(got)}")
        lines += self.component_mismatches
        return "\n".join(lines)


def compare_with_ir(circuit: CircuitIR, netlist: Netlist) -> ConnectivityDiff:
    diff = ConnectivityDiff()
    want = ir_nets(circuit)
    for name, nodes in want.items():
        got = netlist.nets.get(name)
        if got is None:
            diff.missing_nets[name] = nodes
        elif got != nodes:
            diff.changed_nets[name] = (nodes, got)
    for name, nodes in netlist.nets.items():
        if name not in want:
            diff.unexpected_nets[name] = nodes

    for comp in circuit.components:
        nc = netlist.components.get(comp.reference)
        if nc is None:
            diff.component_mismatches.append(f"{comp.reference} missing from netlist")
            continue
        if nc.value != comp.value:
            diff.component_mismatches.append(f"{comp.reference}: value {nc.value!r} != IR {comp.value!r}")
        if nc.lib_id != comp.symbol_id:
            diff.component_mismatches.append(f"{comp.reference}: symbol {nc.lib_id} != IR {comp.symbol_id}")
    extra = set(netlist.components) - {c.reference for c in circuit.components}
    for ref in sorted(extra):
        diff.component_mismatches.append(f"{ref} in netlist but not in IR")
    return diff
