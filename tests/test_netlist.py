"""Netlist comparison against a netlist that KiCad 10.0.8 really exported."""

from circuit_core import CircuitIR
from kicad_adapter import compare_with_ir, parse_kicad_netlist

from .conftest import FIXTURES

NETLIST = FIXTURES / "netlists" / "led_resistor_connector.kicad10.net"


def load():
    return parse_kicad_netlist(NETLIST.read_text(encoding="utf-8"))


def test_parse_real_netlist():
    netlist = load()
    assert set(netlist.components) == {"J1", "R1", "D1"}
    assert netlist.components["D1"].lib_id == "Device:LED"
    assert netlist.nets == {
        "VIN": frozenset({("J1", "1"), ("R1", "1")}),
        "LED_A": frozenset({("R1", "2"), ("D1", "2")}),
        "GND": frozenset({("D1", "1"), ("J1", "2")}),
    }
    assert netlist.unconnected == frozenset()


def test_matches_ir(led_circuit):
    diff = compare_with_ir(led_circuit, load())
    assert diff.ok, diff.describe()


def test_detects_moved_connection(led_circuit_data):
    # IR says the LED cathode goes to VIN; the KiCad file says GND.
    led_circuit_data["nets"][0]["nodes"].append({"component_id": "led-001", "pin": "1"})
    led_circuit_data["nets"][2]["nodes"] = [{"component_id": "conn-001", "pin": "2"}]
    diff = compare_with_ir(CircuitIR.model_validate(led_circuit_data), load())
    assert not diff.ok
    assert set(diff.changed_nets) == {"VIN", "GND"}


def test_detects_renamed_net_and_value_change(led_circuit_data):
    led_circuit_data["nets"][1]["name"] = "LED_ANODE"
    led_circuit_data["components"][1]["value"] = "1k"
    diff = compare_with_ir(CircuitIR.model_validate(led_circuit_data), load())
    assert set(diff.missing_nets) == {"LED_ANODE"}
    assert set(diff.unexpected_nets) == {"LED_A"}
    assert diff.component_mismatches == ["R1: value '330' != IR '1k'"]
