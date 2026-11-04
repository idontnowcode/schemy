"""Patch operations on a Circuit IR (plan v1.4 section 9 and Phase 2a).

Every change that is *proposed* (by AI, an import, or a person through the proposal screen)
is a list of these operations. Each operation states what it expects to find (its precondition);
if the circuit no longer matches, the whole patch is refused instead of being applied to
something else. Applying a patch never touches files: it returns a new, validated IR.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import CircuitIR, Component, NetName, Placement, StableId

PROPERTY_FIELDS = ("value", "footprint_id", "manufacturer", "manufacturer_part_number", "description")


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetProperty(_Op):
    op: Literal["set_property"] = "set_property"
    component_id: StableId
    field: Literal["value", "footprint_id", "manufacturer", "manufacturer_part_number", "description"]
    expect: str | None = Field(description="Current value; the patch is refused if it differs.")
    value: str | None


class AddComponent(_Op):
    op: Literal["add_component"] = "add_component"
    component: Component
    sheet_id: StableId | None = None
    placement: Placement | None = None


class RemoveComponent(_Op):
    op: Literal["remove_component"] = "remove_component"
    component_id: StableId
    expect_reference: str


class PinRef(_Op):
    component_id: StableId
    pin: str


class Connect(_Op):
    op: Literal["connect"] = "connect"
    net: NetName
    nodes: list[PinRef] = Field(min_length=1)


class Disconnect(_Op):
    op: Literal["disconnect"] = "disconnect"
    net: NetName
    nodes: list[PinRef] = Field(min_length=1)


class RenameNet(_Op):
    op: Literal["rename_net"] = "rename_net"
    net: NetName
    new_name: NetName


class SetNoConnect(_Op):
    op: Literal["set_no_connect"] = "set_no_connect"
    component_id: StableId
    pin: str
    no_connect: bool


class Move(_Op):
    op: Literal["move"] = "move"
    component_id: StableId
    placement: Placement | None = Field(description="None removes the fixed position (auto layout).")
    sheet_id: StableId | None = None


class ReplaceCircuit(_Op):
    """The whole circuit at once (a first design, an import). The precondition is the proposal's
    base revision, checked when the proposal is approved."""

    op: Literal["replace_circuit"] = "replace_circuit"
    circuit: CircuitIR


PatchOp = Annotated[SetProperty | AddComponent | RemoveComponent | Connect | Disconnect | RenameNet | SetNoConnect
                    | Move | ReplaceCircuit, Field(discriminator="op")]


class PatchFailure(BaseModel):
    index: int
    op: str
    code: str
    message: str


class PatchError(Exception):
    def __init__(self, failures: list[PatchFailure]):
        self.failures = failures
        super().__init__("; ".join(f"#{f.index + 1} {f.op}: {f.message}" for f in failures))


def _net_id(name: str, used: set[str]) -> str:
    base = "net-" + (re.sub(r"[^a-z0-9._-]+", "-", name.lower().replace("+", "p")).strip("-._") or "x")
    candidate, n = base[:60], 1
    while candidate in used:
        n += 1
        candidate = f"{base[:56]}-{n}"
    return candidate


def apply_patch(circuit: CircuitIR | None, ops: list) -> CircuitIR:
    """Apply ``ops`` in order. Raises :class:`PatchError` listing every failed precondition."""
    failures: list[PatchFailure] = []
    data = circuit.model_dump(mode="json") if circuit is not None else None

    def fail(i: int, op, code: str, message: str) -> None:
        failures.append(PatchFailure(index=i, op=op.op, code=code, message=message))

    for i, op in enumerate(ops):
        if isinstance(op, ReplaceCircuit):
            data = op.circuit.model_dump(mode="json")
            if circuit is not None:
                data["project_id"] = circuit.project_id
            continue
        if data is None:
            fail(i, op, "NO_CIRCUIT", "There is no circuit yet; the first change must be replace_circuit.")
            continue
        comps = {c["id"]: c for c in data["components"]}
        nets = {n["name"]: n for n in data["nets"]}

        def pin_ok(ref: PinRef, op=op, i=i, comps=comps) -> bool:
            comp = comps.get(ref.component_id)
            if comp is None:
                fail(i, op, "UNKNOWN_COMPONENT", f"Component '{ref.component_id}' does not exist.")
                return False
            if ref.pin not in {p["number"] for p in comp["pins"]}:
                fail(i, op, "UNKNOWN_PIN", f"{comp['reference']} has no pin '{ref.pin}'.")
                return False
            return True

        if isinstance(op, SetProperty):
            comp = comps.get(op.component_id)
            if comp is None:
                fail(i, op, "UNKNOWN_COMPONENT", f"Component '{op.component_id}' does not exist.")
            elif comp.get(op.field) != op.expect:
                fail(i, op, "PRECONDITION_FAILED", f"{comp['reference']}.{op.field} is {comp.get(op.field)!r}, "
                     f"expected {op.expect!r}.")
            elif op.field == "value" and not op.value:
                fail(i, op, "INVALID_VALUE", "A component value cannot be empty.")
            else:
                comp[op.field] = op.value
        elif isinstance(op, AddComponent):
            c = op.component.model_dump(mode="json")
            if c["id"] in comps:
                fail(i, op, "DUPLICATE_ID", f"Component id '{c['id']}' already exists.")
            elif any(x["reference"] == c["reference"] for x in data["components"]):
                fail(i, op, "DUPLICATE_REFERENCE", f"Reference {c['reference']} already exists.")
            else:
                data["components"].append(c)
                if op.placement is not None:
                    data["layout"][c["id"]] = op.placement.model_dump(mode="json")
                if op.sheet_id is not None:
                    sheet = next((s for s in data["sheets"] if s["id"] == op.sheet_id), None)
                    if sheet is None:
                        fail(i, op, "UNKNOWN_SHEET", f"Page '{op.sheet_id}' does not exist.")
                    else:
                        sheet["components"].append(c["id"])
        elif isinstance(op, RemoveComponent):
            comp = comps.get(op.component_id)
            if comp is None:
                fail(i, op, "UNKNOWN_COMPONENT", f"Component '{op.component_id}' does not exist.")
            elif comp["reference"] != op.expect_reference:
                fail(i, op, "PRECONDITION_FAILED", f"'{op.component_id}' is {comp['reference']}, "
                     f"expected {op.expect_reference}.")
            else:
                data["components"] = [c for c in data["components"] if c["id"] != op.component_id]
                for n in data["nets"]:
                    n["nodes"] = [x for x in n["nodes"] if x["component_id"] != op.component_id]
                data["nets"] = [n for n in data["nets"] if n["nodes"]]
                data["layout"].pop(op.component_id, None)
                for s in data["sheets"]:
                    s["components"] = [x for x in s["components"] if x != op.component_id]
        elif isinstance(op, Connect):
            if not all(pin_ok(ref) for ref in op.nodes):
                continue
            owner = {(x["component_id"], x["pin"]): n["name"] for n in data["nets"] for x in n["nodes"]}
            busy = [f"{comps[r.component_id]['reference']}.{r.pin}→{owner[(r.component_id, r.pin)]}"
                    for r in op.nodes if owner.get((r.component_id, r.pin)) not in (None, op.net)]
            if busy:
                fail(i, op, "PIN_ALREADY_CONNECTED", "Already in another net (disconnect first): " + ", ".join(busy))
                continue
            net = nets.get(op.net)
            if net is None:
                net = {"id": _net_id(op.net, {n["id"] for n in data["nets"]}), "name": op.net, "nodes": []}
                data["nets"].append(net)
            for r in op.nodes:
                node = {"component_id": r.component_id, "pin": r.pin}
                if node not in net["nodes"]:
                    net["nodes"].append(node)
                for p in comps[r.component_id]["pins"]:
                    if p["number"] == r.pin:
                        p["no_connect"] = False
        elif isinstance(op, Disconnect):
            net = nets.get(op.net)
            if net is None:
                fail(i, op, "UNKNOWN_NET", f"Net '{op.net}' does not exist.")
                continue
            listed = [{"component_id": r.component_id, "pin": r.pin} for r in op.nodes]
            missing = [f"{r.component_id}.{r.pin}" for r, node in zip(op.nodes, listed, strict=True)
                       if node not in net["nodes"]]
            if missing:
                fail(i, op, "PRECONDITION_FAILED", f"Not in net '{op.net}': {', '.join(missing)}")
                continue
            net["nodes"] = [x for x in net["nodes"] if x not in listed]
            data["nets"] = [n for n in data["nets"] if n["nodes"]]
        elif isinstance(op, RenameNet):
            if op.net not in nets:
                fail(i, op, "UNKNOWN_NET", f"Net '{op.net}' does not exist.")
            elif op.new_name in nets:
                fail(i, op, "DUPLICATE_NET", f"Net '{op.new_name}' already exists.")
            else:
                nets[op.net]["name"] = op.new_name
        elif isinstance(op, SetNoConnect):
            if pin_ok(PinRef(component_id=op.component_id, pin=op.pin)):
                for p in comps[op.component_id]["pins"]:
                    if p["number"] == op.pin:
                        p["no_connect"] = op.no_connect
        elif isinstance(op, Move):
            if op.component_id not in comps:
                fail(i, op, "UNKNOWN_COMPONENT", f"Component '{op.component_id}' does not exist.")
                continue
            if op.placement is None:
                data["layout"].pop(op.component_id, None)
            else:
                data["layout"][op.component_id] = op.placement.model_dump(mode="json")
            if op.sheet_id is not None:
                if not any(s["id"] == op.sheet_id for s in data["sheets"]):
                    fail(i, op, "UNKNOWN_SHEET", f"Page '{op.sheet_id}' does not exist.")
                    continue
                for s in data["sheets"]:
                    s["components"] = [x for x in s["components"] if x != op.component_id]
                    if s["id"] == op.sheet_id:
                        s["components"].append(op.component_id)

    if failures:
        raise PatchError(failures)
    if data is None:
        raise PatchError([PatchFailure(index=0, op="-", code="EMPTY_PATCH", message="The patch has no operations.")])
    try:
        return CircuitIR.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise PatchError([PatchFailure(index=len(ops) - 1, op="result", code="IR_INVALID",
                                       message=f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}")]) from exc
