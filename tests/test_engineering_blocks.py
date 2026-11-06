"""Block rules of the technical review (relays, MOSFETs, optocouplers, RS-485, ADC inputs, USB-C), checked on the
practical example boards and on copies of them with one defect planted."""

import json

import pytest

from circuit_core import CircuitIR
from circuit_core.engineering import _Board, engineering_review
from circuit_core.engineering_blocks import roles

from .conftest import EXAMPLE_DIR, PRACTICAL_NAMES


def board(name: str) -> dict:
    data = json.loads((EXAMPLE_DIR.parent / name / "circuit.json").read_text(encoding="utf-8"))
    data["decisions"], data["layout"], data["notes"] = [], {}, []
    return data


def cid(data: dict, ref: str) -> str:
    return next(c["id"] for c in data["components"] if c["reference"] == ref)


def drop(data: dict, ref: str) -> dict:
    gone = cid(data, ref)
    data["components"] = [c for c in data["components"] if c["id"] != gone]
    for net in data["nets"]:
        net["nodes"] = [n for n in net["nodes"] if n["component_id"] != gone]
    data["nets"] = [n for n in data["nets"] if n["nodes"]]
    return data


def move(data: dict, ref: str, pin: str, net_name: str) -> dict:
    """Connect a pin to another (existing) net instead of its own."""
    node = {"component_id": cid(data, ref), "pin": pin}
    for net in data["nets"]:
        net["nodes"] = [n for n in net["nodes"] if n != node]
    next(n for n in data["nets"] if n["name"] == net_name)["nodes"].append(node)
    data["nets"] = [n for n in data["nets"] if n["nodes"]]
    return data


def set_value(data: dict, ref: str, value: str) -> dict:
    next(c for c in data["components"] if c["reference"] == ref)["value"] = value
    return data


def found(data: dict, rule: str) -> list:
    return [f for f in engineering_review(CircuitIR.model_validate(data)) if f.rule == rule]


def worst(data: dict, rule: str) -> str:
    hits = found(data, rule)
    assert hits, f"no {rule} finding"
    return min((f.severity for f in hits), key=["fail", "warn", "info", "ok"].index)


@pytest.mark.parametrize("name", PRACTICAL_NAMES)
def test_practical_boards_have_no_failures_or_warnings(name):
    findings = engineering_review(CircuitIR.model_validate(board(name)))
    assert [f.title for f in findings if f.severity in ("fail", "warn")] == []
    assert any(f.rule == "footprint_missing" and f.severity == "ok" for f in findings)


def test_pin_roles_for_symbols_without_pin_names():
    data = CircuitIR.model_validate(board("relay-driver-12v"))
    k1 = next(c for c in data.components if c.reference == "K1")
    assert roles(k1)["COIL"] == ["2", "5"] and roles(k1)["COM"] == ["1"]
    iso = CircuitIR.model_validate(board("iso-24v-input"))
    u1 = next(c for c in iso.components if c.reference == "U1")
    assert {k: v[0] for k, v in roles(u1).items()} == {"A": "1", "K": "2", "E": "3", "C": "4"}


# --- relay and MOSFET -----------------------------------------------------------------------------------------------

def test_relay_flyback_missing_or_reversed_fails():
    assert worst(board("relay-driver-12v"), "relay_flyback") == "ok"
    assert worst(drop(board("relay-driver-12v"), "D1"), "relay_flyback") == "fail"
    reversed_ = move(move(board("relay-driver-12v"), "D1", "1", "COIL_LO"), "D1", "2", "+12V")
    assert worst(reversed_, "relay_flyback") == "fail"


def test_relay_coil_voltage_against_the_supply():
    data = board("relay-driver-12v")
    rail = next(n for n in data["nets"] if n["name"] == "+12V")
    rail.update(voltage=24.0, voltage_min=None, voltage_max=None)
    assert worst(data, "relay_coil") in ("warn", "fail")


def test_mosfet_gate_pulldown_and_drive():
    assert worst(drop(board("relay-driver-12v"), "R2"), "mosfet_gate") == "warn"
    data = set_value(board("relay-driver-12v"), "Q1", "2N7002")
    q1 = next(c for c in data["components"] if c["reference"] == "Q1")
    q1["symbol_id"], q1["manufacturer_part_number"] = "Transistor_FET:2N7002", None
    assert worst(data, "mosfet_drive") in ("warn", "fail")  # 3.3 V < 4.5 V where 2N7002's Rds(on) is specified


def test_led_on_a_low_side_switch_is_checked_with_the_switch_on():
    led = [f for f in found(board("relay-driver-12v"), "led_current")]
    assert led and "Q1 켜짐 기준" in led[0].title and led[0].severity == "ok"


# --- optocoupler ----------------------------------------------------------------------------------------------------

def test_opto_threshold_rises_with_a_shunt_across_the_led():
    with_shunt = found(board("iso-24v-input"), "opto_threshold")
    without = found(drop(board("iso-24v-input"), "R5"), "opto_threshold")
    assert "6.1 V" in with_shunt[0].title
    u1 = next(f for f in without if f.title.startswith("U1"))
    assert "4.2 V" in u1.title  # 3.7 V + 6.2 kΩ × 0.31 mA / 400 %: on before the required 5 V OFF level


def test_opto_led_current_subtracts_the_shunt_and_the_resistor_carries_all():
    data = board("iso-24v-input")
    inputs = found(data, "opto_input")
    assert inputs[0].title.endswith("LED 전류 1.09 mA ~ 3.94 mA")
    power = {f.title for f in found(data, "r_power")}
    assert "R1 (6.2k): 소비 전력 112 mW / 정격 0.25 W" in power  # (30 − 3.7) / 6.2k = 4.24 mA, not the LED's 3.94 mA


def test_opto_isolation_and_ctr():
    data = board("iso-24v-input")
    assert worst(data, "opto_isolation") == "ok"
    merged = board("iso-24v-input")
    gnd24 = next(n for n in merged["nets"] if n["name"] == "GND_24V")
    gnd = next(n for n in merged["nets"] if n["name"] == "GND")
    gnd["nodes"] += gnd24["nodes"]
    merged["nets"].remove(gnd24)
    assert worst(merged, "opto_isolation") == "warn"
    assert worst(set_value(board("iso-24v-input"), "R3", "1k"), "opto_ctr") == "fail"  # 3.1 mA > 200 % × 1.09 mA


def test_opto_input_resistor_heat_at_the_top_of_the_range():
    hot = set_value(drop(board("iso-24v-input"), "R5"), "R1", "4.7k")
    assert any(f.severity == "warn" and f.title.startswith("R1 (4.7k)") for f in found(hot, "r_power"))


def test_reversed_diode_in_the_opto_input_fails():
    data = move(move(board("iso-24v-input"), "D3", "1", "IN1"), "D3", "2", "IN1_D")
    assert any(f.severity == "fail" and f.title.startswith("U1") for f in found(data, "opto_input"))


# --- RS-485 ---------------------------------------------------------------------------------------------------------

def test_rs485_bias_with_both_ends_terminated():
    assert worst(board("rs485-3v3"), "rs485_bias") == "ok"
    weak = set_value(set_value(board("rs485-3v3"), "R2", "680"), "R3", "680")
    assert worst(weak, "rs485_bias") == "warn"
    assert "139 mV" in found(weak, "rs485_bias")[0].title


def test_rs485_bias_polarity_and_direction_pin():
    # R2 pulls A up (pin 1 on +3V3), R3 pulls B down (pin 2 on GND); swapped, the idle bus reads '0'.
    swapped = move(move(board("rs485-3v3"), "R2", "1", "GND"), "R3", "2", "+3V3")
    assert worst(swapped, "rs485_bias") == "fail"
    up = move(board("rs485-3v3"), "R4", "2", "+3V3")
    assert worst(up, "rs485_dir") == "warn"


# --- ADC input, USB-C, LED -----------------------------------------------------------------------------------------

def test_adc_input_without_clamp_fails():
    assert worst(board("analog-0-10v"), "adc_divider") == "ok"
    assert worst(board("analog-0-10v"), "adc_clamp") == "info"  # 850 µA into +3V3 at 24 V
    assert worst(drop(board("analog-0-10v"), "D1"), "adc_clamp") == "fail"


def test_usb_cc_pull_downs_and_vbus_capacitance():
    assert worst(board("usbc-5v-3v3"), "usb_cc") == "ok"
    assert worst(drop(board("usbc-5v-3v3"), "R2"), "usb_cc") == "fail"
    shared = drop(board("usbc-5v-3v3"), "R2")
    cc1 = next(n for n in shared["nets"] if n["name"] == "CC1")
    cc2 = next(n for n in shared["nets"] if n["name"] == "CC2")
    cc1["nodes"] += cc2["nodes"]
    shared["nets"].remove(cc2)
    assert worst(shared, "usb_cc") == "fail"
    assert worst(set_value(board("usbc-5v-3v3"), "C3", "10uF"), "usb_vbus_cap") == "warn"


def test_a_dim_led_at_the_highest_vf_is_noted():
    green = set_value(board("usbc-5v-3v3"), "D2", "GREEN")
    led = found(green, "led_current")[0]
    assert led.severity == "info" and "어두움" in led.title and led.title.startswith("D2 (GREEN): 전류 100 µA")
    assert found(board("usbc-5v-3v3"), "led_current")[0].severity == "ok"


def test_voltage_carries_through_a_fuse_and_supply_nets_are_not_guessed_from_signals():
    b = _Board(CircuitIR.model_validate(board("usbc-5v-3v3")))
    assert b.v("VBUS") == 5.0 and b.vmax("VBUS") == 5.25  # through the PTC from +5V, with its range
    from circuit_core.engineering import _is_supply

    assert _is_supply(b, "+5V") and _is_supply(b, "+3V3")
    a = _Board(CircuitIR.model_validate(board("analog-0-10v")))
    assert not _is_supply(a, "AIN")  # a connector pin with only a 10 nF filter: a signal, not a supply
    r = _Board(CircuitIR.model_validate(board("rs485-3v3")))
    assert not any(_is_supply(r, n.name) for n in r.c.nets if n.name in ("DE_RE", "RO", "DI"))
