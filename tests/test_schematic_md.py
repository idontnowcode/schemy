"""Importing "회로도 MD v0.1" (AI transcription of a PDF schematic) into a Circuit IR."""

import pytest

from circuit_core.schematic_md import import_schematic_markdown
from kicad_adapter import SchematicGenerationError, SymbolLibraryError, generate_schematic

from .conftest import EXAMPLE_DIR

EXAMPLE_MD = (EXAMPLE_DIR / "schematic.md").read_text(encoding="utf-8")


def lookup_from(library):
    def lookup(symbol_id):
        try:
            return [(p.number, p.name) for p in library.get(symbol_id).pins]
        except SymbolLibraryError:
            return None
    return lookup


def fake_lookup(symbols: dict):
    return lambda symbol_id: symbols.get(symbol_id)


def md(parts: str, pins: str, nets: str | None = None, extra: str = "") -> str:
    text = ("# 회로도: test\n\n## 2. 부품\n| 기호 | 값 | 종류 | 패키지 | 제조사 품번 | KiCad 심볼 | 핀 수 | 페이지 | 비고 |\n"
            "|---|---|---|---|---|---|---|---|---|\n" + parts +
            "\n\n## 3. 핀 연결\n| 기호 | 핀 번호 | 핀 이름 | Net | 비고 |\n|---|---|---|---|---|\n" + pins)
    if nets is not None:
        text += "\n\n## 4. Net\n| Net | 연결 | 종류 | 비고 |\n|---|---|---|---|\n" + nets
    return text + "\n" + extra


def codes(result, severity=None):
    return sorted(i.code for i in result.issues if severity is None or i.severity == severity)


def nets_by_ref(circuit):
    refs = {c.id: c.reference for c in circuit.components}
    return {n.name: {(refs[x.component_id], x.pin) for x in n.nodes} for n in circuit.nets}


def test_example_round_trips_to_the_same_connectivity(library, led_circuit):
    result = import_schematic_markdown(EXAMPLE_MD, lookup_from(library), source_name="led.md")
    assert result.ok, [i.to_dict() for i in result.issues]
    assert codes(result, "error") == [] and codes(result, "warning") == []
    assert nets_by_ref(result.circuit) == nets_by_ref(led_circuit)
    d1 = next(c for c in result.circuit.components if c.reference == "D1")
    assert {(p.number, p.name) for p in d1.pins} == {("1", "K"), ("2", "A")}
    assert d1.pin_map_evidence.notes == "이름으로 찾은 핀: A→2, K→1"
    assert d1.pin_map_evidence.source == "PDF 전사: led_demo.pdf, p.1"
    assert result.circuit.validation.status.value == "unverified"
    assert result.circuit.title == "LED + series resistor + 2-pin connector"
    # And it generates (auto layout, no page split).
    generated = generate_schematic(result.circuit, library)
    assert len(generated.pages) == 1


def test_polarized_capacitor_and_inferred_symbols():
    lookup = fake_lookup({"Device:C_Polarized": [("1", ""), ("2", "")], "Device:R": [("1", ""), ("2", "")],
                          "Connector_Generic:Conn_01x03": [("1", "Pin_1"), ("2", "Pin_2"), ("3", "Pin_3")]})
    result = import_schematic_markdown(md(
        "| C1 | 100uF | 전해 콘덴서 | | | | 2 | 1 | |\n| R1 | 4k7 | 저항 | 0603 | | | 2 | 1 | |\n"
        "| J1 | 3P | 커넥터 | | | | 3 | 1 | |",
        "| C1 | ? | + | +5V | |\n| C1 | ? | - | GND | |\n| R1 | 1 | | +5V | |\n| R1 | 2 | | GND | |\n"
        "| J1 | 1 | | +5V | |\n| J1 | 2 | | GND | |\n| J1 | 3 | | NC | |",
        "| +5V | C1.+, R1.1, J1.1 | 전원 | |\n| GND | C1.-, R1.2, J1.2 | GND | |"), lookup)
    assert result.ok, codes(result)
    by_ref = {c.reference: c for c in result.circuit.components}
    assert by_ref["C1"].symbol_id == "Device:C_Polarized"
    assert by_ref["J1"].symbol_id == "Connector_Generic:Conn_01x03"
    assert nets_by_ref(result.circuit) == {"+5V": {("C1", "1"), ("R1", "1"), ("J1", "1")},
                                           "GND": {("C1", "2"), ("R1", "2"), ("J1", "2")}}
    assert "SYMBOL_INFERRED" in codes(result, "info")
    assert codes(result, "warning") == []


def test_polarity_conflict_is_flagged_and_blocks_generation(library):
    text = EXAMPLE_MD.replace("| D1 | ? | A | LED_A | |", "| D1 | 1 | A | LED_A | |").replace(
        "| D1 | ? | K | GND | |", "| D1 | 2 | K | GND | |")
    result = import_schematic_markdown(text, lookup_from(library))
    assert result.ok
    assert codes(result, "warning").count("PIN_NAME_CONFLICT") == 2
    assert result.circuit.validation.status.value == "needs_review"
    with pytest.raises(SchematicGenerationError) as exc:
        generate_schematic(result.circuit, library)
    assert {i.error_code for i in exc.value.issues} == {"PIN_NAME_MISMATCH"}


def test_missing_pins_are_added_unconnected():
    lookup = fake_lookup({"Lib:IC4": [("1", "IN"), ("2", "GND"), ("3", "OUT"), ("4", "NC")]})
    result = import_schematic_markdown(md("| U1 | X | IC | | | Lib:IC4 | 4 | 1 | |",
                                          "| U1 | 1 | IN | VIN | |\n| U1 | 3 | OUT | VOUT | |"), lookup)
    assert result.ok
    assert "PIN_MISSING_IN_MD" in codes(result, "warning")
    assert sorted(p.number for p in result.circuit.components[0].pins) == ["1", "2", "3", "4"]
    assert result.circuit.validation.status.value == "needs_review"


def test_unknown_symbol_keeps_numbered_pins_but_needs_a_symbol():
    result = import_schematic_markdown(md("| U1 | STM32 | MCU | LQFP-48 | STM32F103C8T6 | | 48 | 1 | |",
                                          "| U1 | 10 | PA0 | SIG | |\n| U1 | 11 | PA1 | SIG | |"), fake_lookup({}))
    assert result.ok
    u1 = result.circuit.components[0]
    assert u1.symbol_id == "Unknown:U1"
    assert u1.manufacturer_part_number == "STM32F103C8T6"
    assert "SYMBOL_REQUIRED" in codes(result, "warning")


@pytest.mark.parametrize(
    "parts, pins, code",
    [("| R1 | 1k | 저항 | | | | 2 | 1 | |\n| R1 | 2k | 저항 | | | | 2 | 1 | |", "| R1 | 1 | | A | |", "DUPLICATE_REFERENCE"),
     ("| R-1 | 1k | 저항 | | | | 2 | 1 | |", "| R-1 | 1 | | A | |", "INVALID_REFERENCE"),
     ("| R1 | 1k | 저항 | | | | 2 | 1 | |", "| R9 | 1 | | A | |", "UNKNOWN_REFERENCE"),
     ("| U1 | X | IC | | | | 2 | 1 | |", "| U1 | ? | VCC | A | |", "PIN_NUMBER_UNKNOWN"),
     ("| D1 | LED | LED | | | Device:LED | 2 | 1 | |", "| D1 | ? | X | A | |", "PIN_NAME_UNRESOLVED"),
     ("| R1 | 1k | 저항 | | | | 2 | 1 | |", "| R1 | 1 | | A | |\n| R1 | 1 | | B | |", "PIN_IN_TWO_NETS")],
)
def test_errors_return_no_circuit(parts, pins, code):
    lookup = fake_lookup({"Device:R": [("1", ""), ("2", "")], "Device:LED": [("1", "K"), ("2", "A")]})
    result = import_schematic_markdown(md(parts, pins), lookup)
    assert not result.ok
    assert code in codes(result, "error")


def test_missing_required_section():
    result = import_schematic_markdown("# 회로도: x\n\n## 2. 부품\n| 기호 | 값 |\n|---|---|\n", fake_lookup({}))
    assert not result.ok
    assert set(codes(result, "error")) >= {"MISSING_COLUMNS", "MISSING_SECTION"}


def test_net_names_are_made_safe_and_tables_cross_checked():
    lookup = fake_lookup({"Device:R": [("1", ""), ("2", "")]})
    result = import_schematic_markdown(md(
        "| R1 | 1k | 저항 | | | | 2 | 1 | |\n| R2 | 1k | 저항 | | | | 2 | 1 | |",
        "| R1 | 1 | | LED A | |\n| R1 | 2 | | +3V3 | |\n| R2 | 1 | | LED A | |\n| R2 | 2 | | ? | |",
        "| LED A | R1.1 | 신호 | |\n| +3V3 | R1.2 | 전원 | |",
        "## 8. 자체 점검\n| 항목 | 값 |\n|---|---|\n| 부품 수 | 3 |\n"), lookup)
    assert result.ok
    assert set(nets_by_ref(result.circuit)) == {"LED_A", "+3V3"}
    assert "NET_RENAMED" in codes(result, "info")
    assert {"NET_TABLE_MISMATCH", "SELF_CHECK_MISMATCH"} <= set(codes(result, "warning"))
    assert result.circuit.validation.status.value == "needs_review"  # R2.2 is '?'


def test_pages_become_sheets_and_uncertain_items_need_review():
    lookup = fake_lookup({"Device:R": [("1", ""), ("2", "")]})
    result = import_schematic_markdown(md(
        "| R1 | 1k | 저항 | | | | 2 | 1 | |\n| R2 | 1k | 저항 | | | | 2 | 2 | |",
        "| R1 | 1 | | A | |\n| R1 | 2 | | B | |\n| R2 | 1 | | A | |\n| R2 | 2 | | B | |",
        "| A | R1.1, R2.1 | 신호 | |\n| B | R1.2, R2.2 | 신호 | |",
        "## 7. 불확실 항목\n| 번호 | 페이지 | 위치 | 내용 | 후보 |\n|---|---|---|---|---|\n| 1 | 2 | 우측 하단 | R2 값 흐림 | 1k / 7k |\n"),
        lookup)
    assert result.ok
    assert [(s.id, s.name, s.components) for s in result.circuit.sheets] == [
        ("p1", "Page 1", ("r1",)), ("p2", "Page 2", ("r2",))]
    assert result.uncertain[0]["내용"] == "R2 값 흐림"
    assert result.circuit.validation.status.value == "needs_review"
    assert "불확실 항목 1개" in result.circuit.validation.notes


def test_chip_footprints_are_inferred_for_passives(library):
    result = import_schematic_markdown(EXAMPLE_MD, lookup_from(library))
    fp = {c.reference: c.footprint_id for c in result.circuit.components}
    assert fp == {"J1": None, "R1": "Resistor_SMD:R_0603_1608Metric", "D1": "LED_SMD:LED_0603_1608Metric"}
    assert codes(result, "info").count("FOOTPRINT_INFERRED") == 2
