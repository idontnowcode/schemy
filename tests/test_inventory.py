"""Inventory import, matching and applying (보유 자재 우선 사용)."""

import io

import pytest

from circuit_core import CircuitIR
from circuit_core.inventory import (
    Inventory,
    InventoryApplyError,
    MatchStatus,
    apply_inventory,
    match_inventory,
    parse_inventory,
    summarize,
)

KOREAN_CSV = """품번,제조사,종류,값,패키지,재고,우선순위,비고,사용처
RC0603FR-07330RL,Yageo,칩저항,330R,0603,5000,1,표준품,A보드
RC0805FR-07330RL,Yageo,칩저항,330,0805,100,2,,
LTST-C191KRKT,Lite-On,LED,LED,0603,40,1,빨강,
CL10B104KB8NNNC,Samsung,MLCC,100nF,0603,abc,1,,
,,,,,,,,
PH-2P,,커넥터,2핀 헤더,1x02 2.54mm,10,,,
"""


def inventory_from(text: str, name: str = "parts.csv", encoding: str = "utf-8") -> Inventory:
    parsed = parse_inventory(name, text.encode(encoding))
    assert parsed.ok, parsed.errors
    return Inventory(source_file=name, imported_at="2026-11-04T00:00:00+09:00", parts=parsed.parts)


def test_parse_korean_csv():
    parsed = parse_inventory("parts.csv", KOREAN_CSV.encode("utf-8"))
    assert parsed.ok
    assert [p.mpn for p in parsed.parts] == [
        "RC0603FR-07330RL", "RC0805FR-07330RL", "LTST-C191KRKT", "CL10B104KB8NNNC", "PH-2P"]
    assert parsed.unknown_columns == ["사용처"]
    assert parsed.mapped_columns["품번"] == "mpn"
    first = parsed.parts[0]
    assert (first.category, first.value, first.package, first.stock, first.priority) == (
        "resistor", "330R", "0603", 5000, 1)
    assert parsed.parts[3].stock is None
    assert any("재고 'abc'" in w.message for w in parsed.warnings)
    assert parsed.parts[4].category == "connector"
    assert parsed.parts[0].id == "inv-00002"  # row number in the sheet


def test_parse_cp949_csv_from_korean_excel():
    parsed = parse_inventory("parts.csv", KOREAN_CSV.encode("cp949"))
    assert parsed.ok and len(parsed.parts) == 5
    assert parsed.parts[0].manufacturer == "Yageo"


def test_parse_english_headers_and_semicolons():
    text = "Part Number;Manufacturer;Type;Value;Package;Qty\nX1;ACME;Resistor;1k;0402;7\n"
    parsed = parse_inventory("p.csv", text.encode())
    assert parsed.ok
    assert (parsed.parts[0].mpn, parsed.parts[0].category, parsed.parts[0].stock) == ("X1", "resistor", 7)


def test_parse_xlsx():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["품번", "종류", "값", "패키지", "재고"])
    ws.append(["RC0603FR-07330RL", "저항", "330", "0603", 1200])
    ws.append([None, None, None, None, None])
    ws.append(["LTST-C191KRKT", "LED", "LED", "0603", 3.0])
    buf = io.BytesIO()
    wb.save(buf)
    parsed = parse_inventory("자재.xlsx", buf.getvalue())
    assert parsed.ok
    assert [(p.mpn, p.stock) for p in parsed.parts] == [("RC0603FR-07330RL", 1200), ("LTST-C191KRKT", 3)]


@pytest.mark.parametrize(
    "name, data, message",
    [("parts.pdf", b"x", "지원하지 않는 파일 형식"),
     ("parts.csv", b"", "빈 파일"),
     ("parts.csv", "이름,색\nA,red\n".encode(), "'값' 또는 '품번' 열이 필요")],
)
def test_parse_errors(name, data, message):
    parsed = parse_inventory(name, data)
    assert not parsed.ok
    assert message in parsed.errors[0].message


def test_match_led_circuit(led_circuit):
    lines = match_inventory(led_circuit, inventory_from(KOREAN_CSV))
    by_ref = {line.references[0]: line for line in lines}
    # R1: 330 / R_0603 -> 0603 resistor preferred over the 0805 one.
    assert by_ref["R1"].status is MatchStatus.MATCHED
    assert by_ref["R1"].chosen.mpn == "RC0603FR-07330RL"
    assert [c.part.mpn for c in by_ref["R1"].candidates] == ["RC0603FR-07330RL", "RC0805FR-07330RL"]
    assert "패키지 다름 (0805)" in by_ref["R1"].candidates[1].reasons[0]
    assert by_ref["D1"].chosen.mpn == "LTST-C191KRKT"
    assert by_ref["J1"].status is MatchStatus.MATCHED  # 1x02 2.54mm grid
    assert summarize(lines) == {"matched": 3, "candidate": 0, "missing": 0, "stock_short": 0}


def test_candidate_missing_and_stock(led_circuit_data):
    led_circuit_data["components"][1]["footprint_id"] = "Resistor_SMD:R_1206_3216Metric"
    led_circuit_data["components"][2]["value"] = "LED_Blue"
    circuit = CircuitIR.model_validate(led_circuit_data)
    lines = match_inventory(circuit, inventory_from(KOREAN_CSV), boards=10)
    by_ref = {line.references[0]: line for line in lines}
    assert by_ref["R1"].status is MatchStatus.CANDIDATE
    assert by_ref["R1"].chosen is None
    assert by_ref["D1"].status is MatchStatus.MISSING
    assert by_ref["J1"].stock_short is False  # 10 boards x 1, stock 10
    lines = match_inventory(circuit, inventory_from(KOREAN_CSV), boards=11)
    j1 = next(line for line in lines if line.references == ["J1"])
    assert j1.stock_short is True
    assert "재고 부족: 필요 11, 재고 10" in j1.reasons


def test_exact_mpn_wins(led_circuit_data):
    led_circuit_data["components"][1]["manufacturer_part_number"] = "rc0805fr07330rl"
    lines = match_inventory(CircuitIR.model_validate(led_circuit_data), inventory_from(KOREAN_CSV))
    r1 = next(line for line in lines if line.references == ["R1"])
    assert r1.chosen.mpn == "RC0805FR-07330RL"
    assert r1.candidates[0].reasons == ["제조사 품번 일치"]


def test_apply_matched_and_choices(led_circuit):
    inventory = inventory_from(KOREAN_CSV)
    updated, changes = apply_inventory(led_circuit, inventory)
    assert {(c.reference, c.field, c.after) for c in changes} == {
        ("R1", "manufacturer_part_number", "RC0603FR-07330RL"),
        ("D1", "manufacturer_part_number", "LTST-C191KRKT"),
        ("J1", "manufacturer_part_number", "PH-2P"),
    }
    # Connections, values and symbols are untouched.
    assert updated.nets == led_circuit.nets
    assert [(c.value, c.symbol_id) for c in updated.components] == [
        (c.value, c.symbol_id) for c in led_circuit.components]

    # A user choice overrides the automatic one.
    r0805 = next(p for p in inventory.parts if p.mpn == "RC0805FR-07330RL")
    updated2, _ = apply_inventory(led_circuit, inventory, apply_matched=False, choices={"res-001": r0805.id})
    assert updated2.component("res-001").manufacturer_part_number == "RC0805FR-07330RL"
    assert updated2.component("led-001").manufacturer_part_number is None

    # Applying twice changes nothing.
    assert apply_inventory(updated, inventory)[1] == []
    with pytest.raises(InventoryApplyError):
        apply_inventory(led_circuit, inventory, choices={"nope": r0805.id})
    with pytest.raises(InventoryApplyError):
        apply_inventory(led_circuit, inventory, choices={"res-001": "inv-99999"})
