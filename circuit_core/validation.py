"""L0 (schema/reference) and L1 (topology) checks on a Circuit IR.

Pydantic already enforces types and required fields. These checks cover what a
single model cannot: uniqueness, cross references, and pin/net consistency.
Passing them says nothing about electrical correctness (plan section 10).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from .models import CircuitIR


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """Mirrors the API error contract: code, message, affected ids, recovery hint."""

    error_code: str
    severity: Severity
    message: str
    affected_component_ids: tuple[str, ...] = ()
    recovery_hint: str | None = None


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, *args, **kwargs) -> None:
        self.issues.append(Issue(*args, **kwargs))


def _duplicates(values) -> list[str]:
    return sorted(v for v, n in Counter(values).items() if n > 1)


def validate_circuit(circuit: CircuitIR) -> ValidationResult:
    r = ValidationResult()
    E, W = Severity.ERROR, Severity.WARNING

    # --- L0: identity and references -------------------------------------
    for dup in _duplicates(c.id for c in circuit.components):
        r.add("DUPLICATE_COMPONENT_ID", E, f"Component id '{dup}' is used more than once.", (dup,))
    for dup in _duplicates(c.reference for c in circuit.components):
        ids = tuple(c.id for c in circuit.components if c.reference == dup)
        r.add("DUPLICATE_REFERENCE", E, f"Reference '{dup}' is used more than once.", ids,
              "Give every component a unique reference designator.")
    for dup in _duplicates(n.id for n in circuit.nets):
        r.add("DUPLICATE_NET_ID", E, f"Net id '{dup}' is used more than once.")
    for dup in _duplicates(n.name for n in circuit.nets):
        r.add("DUPLICATE_NET_NAME", E, f"Net name '{dup}' is used more than once.",
              recovery_hint="Merge the nets or rename one of them.")
    for comp in circuit.components:
        for dup in _duplicates(comp.pin_numbers()):
            r.add("DUPLICATE_PIN", E, f"{comp.reference} declares pin '{dup}' more than once.", (comp.id,))

    components = {c.id: c for c in circuit.components}
    for comp_id in circuit.layout:
        if comp_id not in components:
            r.add("LAYOUT_UNKNOWN_COMPONENT", E, f"Layout refers to unknown component '{comp_id}'.", (comp_id,))
    for comp_id, placement in circuit.layout.items():
        if placement.rotation not in (0, 90, 180, 270):
            r.add("UNSUPPORTED_ROTATION", E,
                  f"Rotation {placement.rotation} for '{comp_id}' is not one of 0/90/180/270.", (comp_id,))

    for dup in _duplicates(sh.id for sh in circuit.sheets):
        r.add("DUPLICATE_SHEET_ID", E, f"Page id '{dup}' is used more than once.")
    for dup in _duplicates(sh.name for sh in circuit.sheets):
        r.add("DUPLICATE_SHEET_NAME", E, f"Page name '{dup}' is used more than once.",
              recovery_hint="Give every page a unique name.")
    placed_on: dict[str, str] = {}
    for sheet in circuit.sheets:
        for comp_id in sheet.components:
            if comp_id not in components:
                r.add("SHEET_UNKNOWN_COMPONENT", E,
                      f"Page '{sheet.name}' lists unknown component '{comp_id}'.", (comp_id,))
            elif comp_id in placed_on and placed_on[comp_id] != sheet.id:
                r.add("COMPONENT_ON_TWO_SHEETS", E,
                      f"{components[comp_id].reference} is listed on more than one page.", (comp_id,))
            placed_on.setdefault(comp_id, sheet.id)

    net_names = {n.name for n in circuit.nets}
    for dup in _duplicates(note.id for note in circuit.notes):
        r.add("DUPLICATE_NOTE_ID", E, f"Note id '{dup}' is used more than once.")
    for note in circuit.notes:
        if note.components and note.net:
            r.add("NOTE_TWO_TARGETS", E, f"Note '{note.id}' names both parts and a net.",
                  recovery_hint="Attach a note to parts or to one net, not both.")
        for comp_id in note.components:
            if comp_id not in components:
                r.add("NOTE_UNKNOWN_COMPONENT", E, f"Note '{note.id}' refers to unknown component '{comp_id}'.",
                      (comp_id,))
        if note.net and note.net not in net_names:
            r.add("NOTE_UNKNOWN_NET", E, f"Note '{note.id}' refers to unknown net '{note.net}'.")

    # --- L1: topology ------------------------------------------------------
    pin_owner: dict[tuple[str, str], str] = {}
    for net in circuit.nets:
        for node in net.nodes:
            comp = components.get(node.component_id)
            if comp is None:
                r.add("UNKNOWN_COMPONENT", E,
                      f"Net '{net.name}' refers to unknown component '{node.component_id}'.",
                      (node.component_id,))
                continue
            if node.pin not in comp.pin_numbers():
                r.add("UNKNOWN_PIN", E,
                      f"Net '{net.name}' uses pin '{node.pin}' which {comp.reference} does not declare.",
                      (comp.id,), "Connect by the symbol's pin number, not by pin name.")
                continue
            key = (node.component_id, node.pin)
            if key in pin_owner and pin_owner[key] != net.name:
                r.add("PIN_IN_MULTIPLE_NETS", E,
                      f"{comp.reference} pin {node.pin} is in nets '{pin_owner[key]}' and '{net.name}'.",
                      (comp.id,), "A pin can belong to exactly one net; merge the nets if intended.")
            elif key in pin_owner:
                r.add("DUPLICATE_NODE", W,
                      f"{comp.reference} pin {node.pin} is listed twice in net '{net.name}'.", (comp.id,))
            pin_owner[key] = net.name
        if len(net.nodes) < 2:
            r.add("SINGLE_NODE_NET", W, f"Net '{net.name}' connects only one pin.",
                  tuple(n.component_id for n in net.nodes))

    for comp in circuit.components:
        for pin in comp.pins:
            connected = (comp.id, pin.number) in pin_owner
            if pin.no_connect and connected:
                r.add("NO_CONNECT_PIN_IN_NET", E,
                      f"{comp.reference} pin {pin.number} is marked no-connect but is in net "
                      f"'{pin_owner[(comp.id, pin.number)]}'.", (comp.id,),
                      "Remove it from the net or clear no_connect.")
            elif not connected and not pin.no_connect:
                r.add("UNCONNECTED_PIN", W, f"{comp.reference} pin {pin.number} is not connected.",
                      (comp.id,), "Connect it, or set \"no_connect\": true on the pin if it is unused on purpose.")

    return r
