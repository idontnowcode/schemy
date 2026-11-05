"""Fixes that need no AI: floating input pins get a pull resistor or a direct connection, as a proposal."""

import json

from circuit_core import CircuitIR
from circuit_core.patch import apply_patch
from circuit_core.quick_fixes import fix_ops, floating_pins

from .backend_helpers import make_client, new_project, save_ir
from .conftest import MOTOR_DIR

DRV = {"1": "power_in", "2": "input", "3": "input", "4": "passive", "5": "power_in", "6": "output", "7": "passive",
       "8": "output", "9": "passive"}


def motor_ir(drop_header: bool) -> dict:
    data = json.loads((MOTOR_DIR / "circuit.json").read_text(encoding="utf-8"))
    if drop_header:  # the control header is gone: IN1 and IN2 are alone on their nets
        data["components"] = [c for c in data["components"] if c["id"] != "j-ctl"]
        for n in data["nets"]:
            n["nodes"] = [x for x in n["nodes"] if x["component_id"] != "j-ctl"]
        data["decisions"] = [{**d, "components": [c for c in d["components"] if c != "j-ctl"]}
                             for d in data["decisions"]]
        data["notes"] = [n for n in data["notes"] if "j-ctl" not in n["components"]]
    return data


def types_of(circuit):
    return {(c.id, p.number): DRV[p.number] for c in circuit.components if c.id == "u-drv" for p in c.pins}


def test_floating_inputs_and_suggestions():
    circuit = CircuitIR.model_validate(motor_ir(drop_header=True))
    fixes = floating_pins(circuit, types_of(circuit))
    assert [(f.reference, f.pin, f.net) for f in fixes] == [("U1", "2", "IN2"), ("U1", "3", "IN1")]
    first = fixes[0].options[0]
    assert (first.kind, first.net, first.value, first.recommended) == ("resistor", "GND", "10k", True)
    assert not floating_pins(CircuitIR.model_validate(motor_ir(drop_header=False)), types_of(circuit))

    ops, text = fix_ops(circuit, fixes[0], "resistor", "GND", "10k")
    after = apply_patch(circuit, ops)
    r = next(c for c in after.components if c.id.startswith("r-fix-"))
    assert r.reference == "R3" and text == "U1.2 IN2: R3 10kΩ로 GND에 풀다운"
    nets = {n.name: {(x.component_id, x.pin) for x in n.nodes} for n in after.nets}
    assert nets["IN2"] == {("u-drv", "2"), (r.id, "1")} and (r.id, "2") in nets["GND"]
    assert after.decisions[-1].by == "engineer" and "풀다운" in after.decisions[-1].choice
    assert not floating_pins(after, types_of(after))[0].pin == "2"


def test_pin_names_choose_the_usual_pull():
    from circuit_core.models import Component, Pin

    pins = [("1", "BOOT0"), ("2", "~{SLEEP}"), ("3", "VDD"), ("4", "VSS"), ("5", "DATA")]
    comp = Component(id="u1", reference="U1", value="X", symbol_id="MCU:X",
                     pins=tuple(Pin(number=n, name=m) for n, m in pins))
    circuit = CircuitIR.model_validate({"project_id": "t", "components": [comp.model_dump(mode="json")], "nets": [
        {"id": "n1", "name": "+3V3", "nodes": [{"component_id": "u1", "pin": "3"}]},
        {"id": "n2", "name": "GND", "nodes": [{"component_id": "u1", "pin": "4"}]}]})
    types = {("u1", "1"): "input", ("u1", "2"): "input", ("u1", "3"): "power_in", ("u1", "4"): "power_in",
             ("u1", "5"): "input"}
    fixes = {f.pin_name: f for f in floating_pins(circuit, types)}
    assert fixes["BOOT0"].options[0].label.startswith("10kΩ로 GND에 풀다운")
    assert fixes["~{SLEEP}"].options[0].label.startswith("10kΩ로 +3V3에 풀업")
    assert "VDD" in fixes and fixes["VDD"].options[0].net == "+3V3"  # alone on +3V3: still floating
    assert fixes["DATA"].options[0].net == "GND" and any(o.net == "+3V3" for o in fixes["DATA"].options)


def test_fix_endpoint_for_the_saved_circuit_and_for_a_proposal(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid, {**motor_ir(drop_header=False), "project_id": pid})
    url = f"/api/projects/{pid}/boards/a"
    assert client.get(f"{url}/quick-fixes").json()["fixes"] == []

    # A proposal that removes the control header leaves IN1/IN2 floating; fix it with a follow-up proposal.
    r = client.post(f"{url}/proposals", json={"title": "J2 빼기", "ops": [
        {"op": "remove_component", "component_id": "j-ctl", "expect_reference": "J2"}]})
    parent = r.json()["proposal"]["id"]
    client.app.state.jobs.wait(r.json()["job"]["id"], timeout=300)
    found = client.get(f"{url}/quick-fixes", params={"proposal_id": parent}).json()
    assert [f["id"] for f in found["fixes"]] == ["float:u-drv:2", "float:u-drv:3"] and "GND" in found["nets"]
    r = client.post(f"{url}/quick-fixes", json={"proposal_id": parent, "choices": [
        {"fix_id": "float:u-drv:2", "kind": "resistor", "net": "GND", "value": "10k"},
        {"fix_id": "float:u-drv:3", "kind": "direct", "net": "GND"}]})
    assert r.status_code == 201, r.text
    child = r.json()["proposal"]
    client.app.state.jobs.wait(r.json()["job"]["id"], timeout=300)
    child = client.get(f"{url}/proposals/{child['id']}").json()
    assert child["parent"] == parent and child["source"] == "user" and "(제안 #1 수정)" in child["title"]
    assert [c["reference"] for c in child["diff_from_parent"]["components_added"]] == ["R3"]
    assert [c["reference"] for c in child["diff"]["components_removed"]] == ["J2"]
    assert client.get(f"{url}/quick-fixes", params={"proposal_id": child["id"]}).json()["fixes"] == []

    bad = client.post(f"{url}/quick-fixes", json={"proposal_id": parent, "choices": [
        {"fix_id": "float:u-drv:2", "kind": "direct", "net": "NOPE"}]})
    assert bad.json()["error_code"] == "UNKNOWN_NET"
    gone = client.post(f"{url}/quick-fixes",
                       json={"choices": [{"fix_id": "float:u-drv:2", "kind": "direct", "net": "GND"}]})
    assert gone.json()["error_code"] == "FIX_NOT_FOUND"  # the saved circuit has no floating pin
