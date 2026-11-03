import os

import pytest

from circuit_core import CircuitIR
from kicad_adapter import SCHEMATIC_FILE_VERSION, SchematicGenerationError, generate_schematic, sexpr
from kicad_adapter.sexpr import child, children, head, unquote

from .circuits import rotation_ring
from .conftest import GOLDEN

GOLDEN_SCH = GOLDEN / "led_resistor_connector.kicad_sch"


def error_codes(exc_info):
    return sorted(i.error_code for i in exc_info.value.issues)


def test_matches_golden_file(led_circuit, library):
    text = generate_schematic(led_circuit, library).text
    if os.environ.get("AICS_UPDATE_GOLDEN"):
        GOLDEN_SCH.write_text(text, encoding="utf-8", newline="\n")
    assert text == GOLDEN_SCH.read_text(encoding="utf-8"), (
        "output changed: inspect the diff, then rerun with AICS_UPDATE_GOLDEN=1"
    )


def test_deterministic(led_circuit, library):
    a = generate_schematic(led_circuit, library)
    b = generate_schematic(led_circuit, library)
    assert a.text == b.text
    assert a.root_uuid == b.root_uuid


def test_file_structure(led_circuit, library):
    doc = sexpr.parse(generate_schematic(led_circuit, library).text)
    assert head(doc) == "kicad_sch"
    assert child(doc, "version")[1] == SCHEMATIC_FILE_VERSION
    embedded = sorted(unquote(s[1]) for s in children(child(doc, "lib_symbols"), "symbol"))
    assert embedded == ["Connector_Generic:Conn_01x02", "Device:LED", "Device:R"]
    placed = {
        unquote(child(s, "lib_id")[1]): s for s in children(doc, "symbol")
    }
    led = placed["Device:LED"]
    props = {unquote(p[1]): unquote(p[2]) for p in children(led, "property")}
    assert props["Reference"] == "D1"
    assert props["Footprint"] == "LED_SMD:LED_0603_1608Metric"
    assert props["AICS_ID"] == "led-001"
    labels = sorted(unquote(lbl[1]) for lbl in children(doc, "label"))
    assert labels == ["GND", "GND", "LED_A", "LED_A", "VIN", "VIN"]


def test_pin_anchors(led_circuit, library):
    anchors = {(a.reference, a.pin): (a.x, a.y, a.net) for a in generate_schematic(led_circuit, library).anchors}
    # R1 at (88.9, 76.2), rotation 0: pin 1 up, pin 2 down.
    assert anchors[("R1", "1")] == (88.9, 72.39, "VIN")
    assert anchors[("R1", "2")] == (88.9, 80.01, "LED_A")
    # D1 at (88.9, 104.14), rotation 90: anode (pin 2) on top.
    assert anchors[("D1", "2")] == (88.9, 100.33, "LED_A")
    assert anchors[("D1", "1")] == (88.9, 107.95, "GND")
    # J1 at (63.5, 81.28), rotation 180: pins face right.
    assert anchors[("J1", "1")] == (68.58, 81.28, "VIN")
    assert anchors[("J1", "2")] == (68.58, 78.74, "GND")


def test_connectivity_is_independent_of_layout(led_circuit_data, library):
    led_circuit_data["layout"] = {}
    auto = generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    nets = {}
    for a in auto.anchors:
        nets.setdefault(a.net, set()).add((a.reference, a.pin))
    assert nets == {"VIN": {("J1", "1"), ("R1", "1")},
                    "LED_A": {("R1", "2"), ("D1", "2")},
                    "GND": {("D1", "1"), ("J1", "2")}}


def test_symbol_sources_recorded(led_circuit, library):
    sources = generate_schematic(led_circuit, library).symbol_sources
    assert set(sources) == {"Connector_Generic:Conn_01x02", "Device:LED", "Device:R"}
    assert sources["Device:R"]["version"] == "20251024"


def test_rotation_ring_generates(library):
    generated = generate_schematic(rotation_ring(), library)
    assert len(generated.anchors) == 16


# --- refusals: wrong pin maps never reach a KiCad file -----------------------

def test_rejects_pin_names_used_as_numbers(led_circuit_data, library):
    led = led_circuit_data["components"][2]
    led["pins"] = [{"number": "K"}, {"number": "A"}]
    led_circuit_data["nets"][1]["nodes"][1]["pin"] = "A"
    led_circuit_data["nets"][2]["nodes"][0]["pin"] = "K"
    with pytest.raises(SchematicGenerationError) as exc:
        generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    assert error_codes(exc) == ["PIN_NOT_DECLARED", "PIN_NOT_IN_SYMBOL", "PIN_NOT_IN_SYMBOL"]


def test_rejects_swapped_anode_cathode(led_circuit_data, library):
    led_circuit_data["components"][2]["pins"] = [{"number": "1", "name": "A"}, {"number": "2", "name": "K"}]
    with pytest.raises(SchematicGenerationError) as exc:
        generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    assert error_codes(exc) == ["PIN_NAME_MISMATCH", "PIN_NAME_MISMATCH"]


def test_rejects_unknown_symbol(led_circuit_data, library):
    led_circuit_data["components"][1]["symbol_id"] = "Device:R_Imaginary"
    with pytest.raises(SchematicGenerationError) as exc:
        generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    assert error_codes(exc) == ["SYMBOL_NOT_FOUND"]


def test_rejects_ir_errors(led_circuit_data, library):
    led_circuit_data["components"][2]["reference"] = "R1"
    with pytest.raises(SchematicGenerationError) as exc:
        generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    assert "DUPLICATE_REFERENCE" in error_codes(exc)


def test_rejects_layout_that_would_short_nets(led_circuit_data, library):
    # Put D1 right under R1 so the LED_A and GND stubs land on R1's pins.
    led_circuit_data["layout"]["led-001"] = {"x": 88.9, "y": 76.2, "rotation": 90}
    with pytest.raises(SchematicGenerationError) as exc:
        generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    assert set(error_codes(exc)) == {"LAYOUT_COLLISION"}


def test_warns_on_reference_prefix_and_off_grid(led_circuit_data, library):
    led_circuit_data["components"][1]["reference"] = "X1"
    led_circuit_data["layout"]["res-001"]["x"] = 88.0
    generated = generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    assert sorted(w.error_code for w in generated.warnings) == ["OFF_GRID", "REFERENCE_PREFIX_MISMATCH"]


def test_write_creates_schematic_and_project(led_circuit, library, workdir):
    sch = generate_schematic(led_circuit, library, project_name="demo").write(workdir)
    assert sch.name == "demo.kicad_sch"
    assert (workdir / "demo.kicad_pro").is_file()
    assert '"demo"' in sch.read_text(encoding="utf-8")  # instance project name


@pytest.mark.parametrize("name", ["../evil", "a/b", "", "with space"])
def test_rejects_unsafe_project_name(led_circuit, library, name):
    with pytest.raises(ValueError, match="project name"):
        generate_schematic(led_circuit, library, project_name=name)
