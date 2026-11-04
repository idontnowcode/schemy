"""circuit_core.export: canonical JSON, Markdown and BOM are deterministic."""

from circuit_core import CircuitIR
from circuit_core.export import bom_csv, bom_rows, canonical_json, circuit_markdown, natural_key


def test_canonical_json_round_trips(led_circuit):
    text = canonical_json(led_circuit)
    assert CircuitIR.model_validate_json(text) == led_circuit
    assert text == canonical_json(CircuitIR.model_validate_json(text))
    assert text.endswith("}\n")


def test_natural_sort():
    assert sorted(["R10", "R2", "C1", "R1"], key=natural_key) == ["C1", "R1", "R2", "R10"]


def test_bom_groups_identical_parts(led_circuit_data):
    data = led_circuit_data
    extra = dict(data["components"][1], id="res-010", reference="R10")
    second = dict(data["components"][1], id="res-002", reference="R2")
    data["components"] += [extra, second]
    rows = bom_rows(CircuitIR.model_validate(data))
    resistor = next(r for r in rows if r["Symbol"] == "Device:R")
    assert resistor["References"] == "R1 R2 R10"
    assert resistor["Quantity"] == "3"
    assert resistor["Footprint"] == "Resistor_SMD:R_0603_1608Metric"
    assert resistor["PinMapStatus"] == "unverified"


def test_bom_csv(led_circuit):
    lines = bom_csv(led_circuit).splitlines()
    assert lines[0] == "References,Quantity,Value,Symbol,Footprint,Manufacturer,MPN,PinMapStatus"
    assert lines[1].startswith("D1,1,LED,Device:LED,LED_SMD:LED_0603_1608Metric,")
    assert len(lines) == 4


def test_markdown_content_and_determinism(led_circuit):
    md = circuit_markdown(led_circuit, revision=3)
    assert md == circuit_markdown(led_circuit, revision=3)
    assert md.startswith("# LED + series resistor + 2-pin connector (Phase 0 PoC)\n")
    assert "| Revision | 3 |" in md
    assert "| 엔지니어링 검토 상태 | 미확인 (unverified) |" in md
    assert "| GND | D1.1, J1.2 |" in md
    assert "| D1 | 2 | A | LED_A |" in md
    assert "전기적 설계 검증 결과가 아닙니다" in md


def test_markdown_escapes_table_cells(led_circuit_data):
    led_circuit_data["components"][1]["description"] = "a | b\nnext"
    md = circuit_markdown(CircuitIR.model_validate(led_circuit_data))
    assert "a \\| b next" in md
