"""Patch operations with preconditions, and the code-computed diff (Phase 2a)."""

import pytest
from pydantic import TypeAdapter

from circuit_core import CircuitIR, validate_circuit
from circuit_core.diff import diff_circuits
from circuit_core.patch import PatchError, PatchOp, apply_patch

OPS = TypeAdapter(list[PatchOp])


def ops(*raw):
    return OPS.validate_python(list(raw))


def test_set_property_checks_the_current_value(led_circuit):
    patch = ops({"op": "set_property", "component_id": "res-001", "field": "value", "expect": "330", "value": "1k"})
    after = apply_patch(led_circuit, patch)
    assert after.component("res-001").value == "1k" and led_circuit.component("res-001").value == "330"
    with pytest.raises(PatchError) as exc:
        apply_patch(after, patch)  # it is 1k now, not 330
    assert exc.value.failures[0].code == "PRECONDITION_FAILED"


def test_add_connect_disconnect_remove(led_circuit):
    after = apply_patch(led_circuit, ops(
        {"op": "add_component", "component": {"id": "c1", "reference": "C1", "value": "100nF", "symbol_id": "Device:C",
                                              "pins": [{"number": "1"}, {"number": "2"}]}},
        {"op": "connect", "net": "VIN", "nodes": [{"component_id": "c1", "pin": "1"}]},
        {"op": "connect", "net": "GND", "nodes": [{"component_id": "c1", "pin": "2"}]},
    ))
    assert validate_circuit(after).errors == []
    d = diff_circuits(led_circuit, after)
    assert d["summary"]["components_added"] == 1 and d["summary"]["connections_changed"] == 2
    assert d["connections"] == [{"reference": "C1", "pin": "1", "pin_name": None, "before": None, "after": "VIN"},
                                {"reference": "C1", "pin": "2", "pin_name": None, "before": None, "after": "GND"}]
    back = apply_patch(after, ops(
        {"op": "disconnect", "net": "VIN", "nodes": [{"component_id": "c1", "pin": "1"}]},
        {"op": "remove_component", "component_id": "c1", "expect_reference": "C1"},
    ))
    assert back == led_circuit


@pytest.mark.parametrize("raw, code", [
    ({"op": "connect", "net": "GND", "nodes": [{"component_id": "res-001", "pin": "1"}]}, "PIN_ALREADY_CONNECTED"),
    ({"op": "connect", "net": "X", "nodes": [{"component_id": "res-001", "pin": "9"}]}, "UNKNOWN_PIN"),
    ({"op": "remove_component", "component_id": "res-001", "expect_reference": "R9"}, "PRECONDITION_FAILED"),
    ({"op": "disconnect", "net": "GND", "nodes": [{"component_id": "res-001", "pin": "1"}]}, "PRECONDITION_FAILED"),
    ({"op": "rename_net", "net": "VIN", "new_name": "GND"}, "DUPLICATE_NET"),
    ({"op": "add_component", "component": {"id": "x1", "reference": "R1", "value": "1", "symbol_id": "Device:R",
                                           "pins": [{"number": "1"}]}}, "DUPLICATE_REFERENCE"),
])
def test_failed_preconditions_refuse_the_whole_patch(led_circuit, raw, code):
    with pytest.raises(PatchError) as exc:
        apply_patch(led_circuit, ops(raw))
    assert exc.value.failures[0].code == code


def test_replace_circuit_and_first_design(led_circuit):
    first = ops({"op": "replace_circuit", "circuit": led_circuit.model_dump(mode="json")})
    assert apply_patch(None, first) == led_circuit
    with pytest.raises(PatchError) as exc:
        apply_patch(None, ops({"op": "rename_net", "net": "A", "new_name": "B"}))
    assert exc.value.failures[0].code == "NO_CIRCUIT"
    d = diff_circuits(None, led_circuit)
    assert d["is_new_circuit"] and d["summary"]["components_added"] == 3 and d["nets_added"] == ["GND", "LED_A", "VIN"]


def test_no_connect_rename_and_move(led_circuit):
    after = apply_patch(led_circuit, ops(
        {"op": "rename_net", "net": "VIN", "new_name": "VBUS"},
        {"op": "move", "component_id": "led-001", "placement": {"x": 101.6, "y": 50.8, "rotation": 90}},
    ))
    d = diff_circuits(led_circuit, after)
    assert d["nets_added"] == ["VBUS"] and d["nets_removed"] == ["VIN"] and d["moved"] == ["D1"]
    data = led_circuit.model_dump(mode="json")
    data["nets"] = [n for n in data["nets"] if n["name"] != "VIN"]
    opened = CircuitIR.model_validate(data)
    nc = apply_patch(opened, ops({"op": "set_no_connect", "component_id": "conn-001", "pin": "1", "no_connect": True}))
    assert nc.component("conn-001").pins[0].no_connect
    assert diff_circuits(opened, nc)["properties_changed"][0]["field"] == "pin 1 no_connect"
