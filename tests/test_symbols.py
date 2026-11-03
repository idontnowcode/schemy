import pytest

from kicad_adapter import SymbolLibrary, SymbolLibraryError


def test_led_pin_numbers_come_from_the_library(library):
    led = library.get("Device:LED")
    assert {(p.number, p.name) for p in led.pins} == {("1", "K"), ("2", "A")}
    k = led.pin("1")
    assert (k.x, k.y, k.angle, k.electrical_type) == (-3.81, 0.0, 0, "passive")


def test_resistor_and_connector(library):
    r = library.get("Device:R")
    assert [(p.number, p.y, p.angle) for p in r.pins] == [("1", 3.81, 270), ("2", -3.81, 90)]
    j = library.get("Connector_Generic:Conn_01x02")
    assert [(p.number, p.name, p.x, p.y) for p in j.pins] == [
        ("1", "Pin_1", -5.08, 0.0), ("2", "Pin_2", -5.08, -2.54)]
    assert j.field("Reference").value == "J"


def test_embedded_copy_is_renamed_and_independent(library):
    r = library.get("Device:R")
    node = r.embedded_node()
    assert node[1] == '"Device:R"'
    node[1] = '"changed"'
    assert r.node[1] == '"R"'


def test_provenance_records_library_format(library):
    info = library.provenance("Device")
    assert info["version"] == "20251024"
    assert info["generator_version"] == "10.0"


def test_missing_library_and_symbol(library):
    with pytest.raises(SymbolLibraryError, match="Power.kicad_sym"):
        library.get("Power:GND")
    with pytest.raises(SymbolLibraryError, match="not found"):
        library.get("Device:R_Small_Imaginary")
    with pytest.raises(SymbolLibraryError, match="invalid lib_id"):
        SymbolLibrary([]).get("NoColon")
