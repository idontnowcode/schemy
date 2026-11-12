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
        ("R1", "manufacturer_part_number", "RC0603FR-07330RL"), ("R1", "manufacturer", "Yageo"),
        ("D1", "manufacturer_part_number", "LTST-C191KRKT"), ("D1", "manufacturer", "Lite-On"),
        ("J1", "manufacturer_part_number", "PH-2P"),  # no maker in the list: left empty
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


def test_stock_ratings_become_part_specs(led_circuit):
    """Found in a real-flow run: stocked parts came into the circuit with no ratings, so a 1 % resistor or a 35 V
    electrolytic from the stock list was unknown to the calculation review and to the AI reviewer."""
    from circuit_core.inventory import InventoryPart, specs_from_stock

    def part(category, rating, tolerance=None):
        return InventoryPart(id="x", category=category, rating=rating, tolerance=tolerance, source_row=2)

    assert specs_from_stock(part("resistor", "1%")) == {"tolerance": 1.0}
    assert specs_from_stock(part("resistor", "1/10W", "0.1%")) == {"tolerance": 0.1, "power_rating": 0.1}
    assert specs_from_stock(part("capacitor", "50V C0G")) == {"voltage_rating": 50.0, "dielectric": "C0G"}
    assert specs_from_stock(part("capacitor_polarized", "35V")) == {"voltage_rating": 35.0,
                                                                   "dielectric": "electrolytic"}
    assert specs_from_stock(part("diode", "40V 3A")) == {"voltage_rating": 40.0, "current_rating": 3.0}
    assert specs_from_stock(part("fuse", "60V 100mA")) == {"voltage_rating": 60.0, "current_rating": 0.1}
    assert specs_from_stock(part("ic", "40V")) is None  # an IC's supply range is not a part rating
    assert specs_from_stock(part("resistor", None)) is None

    # Applying the stock list brings the ratings along (only where the part says nothing yet).
    rated = inventory_from("품번,제조사,종류,값,패키지,정격,재고\nRC0603FR-07330RL,Yageo,칩저항,330R,0603,1%,5000\n")
    updated, _ = apply_inventory(led_circuit, rated)
    assert updated.component("res-001").specs.tolerance == 1.0


# --- template parts of an AI design get stocked part numbers and ratings ---------------------------------------------

STOCK_HEAD = "품번,제조사,종류,값,패키지,풋프린트,정격,재고\n"
BUCK_STOCK = STOCK_HEAD + (
    "EEU-FR1V681,Panasonic,전해콘덴서,680uF,10x20 래디얼,,35V,40\n"
    "EEE-FK1V221P,Panasonic,전해콘덴서,220uF,8x10.2 SMD,,35V,60\n"
    "SRR1260-330M,Bourns,인덕터,33uH,12x12mm,,3A,25\n")
"""The stock list of the real-flow runs: the 680 µF is a 10x20 radial part, the template's footprint is 10x10.5 SMD."""


def buck_block(library, **vin) -> CircuitIR:
    """The reviewed LM2596 buck template alone on a board; ``vin`` sets the input net's voltages."""
    from circuit_core.templates import TEMPLATES, as_circuit, instantiate

    inst = instantiate(TEMPLATES["buck-lm2596-5v"], {"vin": "VIN"}, None,
                       lambda s: [(p.number, p.name) for p in library.get(s).pins])
    data = as_circuit(inst, "buck").model_dump(mode="json")
    next(n for n in data["nets"] if n["name"] == "VIN").update(vin)
    return CircuitIR.model_validate(data)


def cap_voltage(circuit, reference):
    from circuit_core.engineering import engineering_review

    cid = next(c.id for c in circuit.components if c.reference == reference)
    return next((f.severity, f.title, f.calc) for f in engineering_review(circuit)
                if f.rule == "cap_voltage" and f.components == (cid,))


def test_template_parts_take_stocked_part_numbers_and_ratings(library):
    """Found in real-flow runs (#36, #50, #61): the LM2596 template's 680 µF, 220 µF and 33 µH came out with no part
    number and no ratings although the stock list had them, so the BOM had blanks and no review could judge them."""
    from circuit_core.inventory import fill_template_parts, stock_report_lines

    base = buck_block(library, voltage=24, voltage_max=30)
    by_ref = {c.reference: c for c in base.components}
    assert (by_ref["C1"].value, by_ref["C2"].value, by_ref["L1"].value) == ("680uF", "220uF", "33uH")
    filled, report = fill_template_parts(base, inventory_from(BUCK_STOCK))
    got = {c.reference: c for c in filled.components}

    # What the stock list has in the template's package is filled in: part number, maker and ratings.
    assert (got["C2"].manufacturer_part_number, got["C2"].manufacturer) == ("EEE-FK1V221P", "Panasonic")
    assert got["C2"].specs.voltage_rating == 35.0 and got["C2"].specs.dielectric == "electrolytic"
    assert (got["L1"].manufacturer_part_number, got["L1"].specs.current_rating) == ("SRR1260-330M", 3.0)
    assert [(a["references"], a["mpn"], a["how"], a["notes"]) for a in report["applied"]] == [
        (["C2"], "EEE-FK1V221P", "종류·값·패키지 일치", []), (["L1"], "SRR1260-330M", "종류·값·패키지 일치", [])]
    assert report["applied"][0]["filled"] == ["manufacturer_part_number", "manufacturer", "specs"]

    # The 680 µF in another package is not put in: nothing of the part changes, and the report says what there is.
    assert got["C1"] == by_ref["C1"] and got["C1"].manufacturer_part_number is None and got["C1"].specs is None
    [left] = report["unconfirmed"]
    assert left["references"] == ["C1"] and "CP_Elec_10x10.5" in left["why"]
    [candidate] = left["candidates"]
    assert (candidate["mpn"], candidate["rating"], candidate["package"]) == ("EEU-FR1V681", "35V", "10x20 래디얼")
    assert candidate["reason"] == "종류·값 일치, 패키지 다름 (10x20)"
    # ... with what the calculation review would say if that stocked part were used: 35 V on a 30 V input.
    assert [(f["severity"], f["title"], f["calc"]) for f in candidate["if_used"]] == [
        ("warn", "C1 (680uF): 정격 전압 여유 부족", "35 / 30 = 1.17배")]
    text = stock_report_lines(report, applied=False)
    assert len(text) == 1 and "\n" not in text[0]
    assert "C1 (680uF)에는 보유 자재를 넣지 않았습니다" in text[0]
    assert "EEU-FR1V681 (Panasonic, 35V, 10x20 래디얼)" in text[0]
    assert "정격 전압 여유 부족 (35 / 30 = 1.17배)" in text[0] and "자재 탭" in text[0]
    assert len(stock_report_lines(report)) == 3 and stock_report_lines(None) == []

    # No template footprint changed, nothing but those two parts changed, and connections are as they were.
    assert [c.footprint_id for c in filled.components] == [c.footprint_id for c in base.components]
    assert [c for c in filled.components if c.reference not in ("C2", "L1")] == \
        [c for c in base.components if c.reference not in ("C2", "L1")]
    assert filled.nets == base.nets and filled.decisions == base.decisions

    # The calculation review now knows the ratings: 35 V on the 5 V output is plenty; the 680 µF is still unrated.
    assert cap_voltage(filled, "C2")[:2] == ("ok", "C2 (220uF): 정격 전압 여유 충분")
    assert cap_voltage(filled, "C1")[:2] == ("info", "C1 (680uF): 정격 전압 미기재")

    # Doing it again changes nothing (the same object comes back) and still reports the 680 µF.
    again, report2 = fill_template_parts(filled, inventory_from(BUCK_STOCK))
    assert again is filled and report2["applied"] == [] and report2["unconfirmed"] == report["unconfirmed"]


def test_a_stocked_capacitor_in_the_template_package_is_flagged_by_its_rating(library):
    """With the ratings in the circuit the calculation review checks them: a 35 V part on an input that reaches
    30 V has 1.17 times the voltage, where the review (and guide G-PWR-06) asks for 1.5 times."""
    from circuit_core.inventory import fill_template_parts

    base = buck_block(library, voltage=24, voltage_max=30)
    assert cap_voltage(base, "C1")[:2] == ("info", "C1 (680uF): 정격 전압 미기재")
    smd = BUCK_STOCK.replace("EEU-FR1V681,Panasonic,전해콘덴서,680uF,10x20 래디얼",
                             "EEV-FK1V681Q,Panasonic,전해콘덴서,680uF,10x10.2")
    filled, report = fill_template_parts(base, inventory_from(smd))
    c1 = next(c for c in filled.components if c.reference == "C1")
    assert c1.manufacturer_part_number == "EEV-FK1V681Q" and c1.specs.voltage_rating == 35.0
    assert c1.footprint_id == "Capacitor_SMD:CP_Elec_10x10.5" and report["unconfirmed"] == []
    assert cap_voltage(filled, "C1") == ("warn", "C1 (680uF): 정격 전압 여유 부족", "35 / 30 = 1.17배")


def test_a_template_keeps_what_it_fixed(library):
    from circuit_core.inventory import fill_template_parts

    base = buck_block(library)
    d1 = next(c for c in base.components if c.reference == "D1")
    assert (d1.manufacturer_part_number, d1.manufacturer) == ("1N5822", "Vishay")

    # The stock list names another KiCad footprint for the same package: the template's stays, and that is noted.
    other_fp = STOCK_HEAD + "EEE-FK1V221P,Panasonic,전해콘덴서,220uF,,Capacitor_SMD:CP_Elec_8x10,35V,60\n"
    filled, report = fill_template_parts(base, inventory_from(other_fp))
    c2 = next(c for c in filled.components if c.reference == "C2")
    assert c2.manufacturer_part_number == "EEE-FK1V221P" and c2.footprint_id == "Capacitor_SMD:CP_Elec_8x10.5"
    assert report["applied"][0]["notes"] == [
        "풋프린트는 템플릿의 CP_Elec_8x10.5 그대로입니다 (보유 목록: CP_Elec_8x10)."]

    # The template's own part number in the stock list: the ratings come along, the template's maker stays.
    same = STOCK_HEAD + "1n5822,ON Semi,쇼트키 다이오드,1N5822,DO-201AD,,40V 3A,100\n"
    filled, report = fill_template_parts(base, inventory_from(same))
    got = next(c for c in filled.components if c.reference == "D1")
    assert (got.manufacturer_part_number, got.manufacturer) == ("1N5822", "Vishay")
    assert (got.specs.voltage_rating, got.specs.current_rating) == (40.0, 3.0)
    assert report["applied"][0]["filled"] == ["specs"] and report["applied"][0]["how"] == "제조사 품번 일치"

    # Another stocked part number with the same value and package does not replace the one the template names.
    other = STOCK_HEAD + ("1N5822-E3/54,Vishay,쇼트키 다이오드,1N5822,,Diode_THT:D_DO-201AD_P15.24mm_Horizontal,"
                          "40V 3A,100\n")
    assert match_inventory(base, inventory_from(other), only={d1.id})[0].status is MatchStatus.MATCHED
    filled, report = fill_template_parts(base, inventory_from(other))
    assert filled is base and report["applied"] == []
    assert report["unconfirmed"][0]["why"] == "템플릿이 정한 품번(1N5822)과 다른 보유 품번입니다."
    assert report["unconfirmed"][0]["candidates"][0]["mpn"] == "1N5822-E3/54"


def test_only_untouched_template_parts_are_filled(library):
    """The AI's own parts are the AI's choice (it saw the stock list), and a template part someone has since
    chosen one by one stays as chosen: a stocked 35 V part a review had replaced must not come back."""
    from circuit_core.inventory import fill_template_parts

    data = buck_block(library).model_dump(mode="json")
    c2 = next(c for c in data["components"] if c["reference"] == "C2")
    data["components"] += [{**c2, "id": "c9", "reference": "C9", "origin": "ai"},
                           {**c2, "id": "c10", "reference": "C10", "origin": None}]
    circuit = CircuitIR.model_validate(data)
    filled, report = fill_template_parts(circuit, inventory_from(BUCK_STOCK))
    got = {c.reference: c.manufacturer_part_number for c in filled.components}
    assert (got["C2"], got["C9"], got["C10"]) == ("EEE-FK1V221P", None, None)
    assert [a["references"] for a in report["applied"]] == [["C2"], ["L1"]]

    # An AI revision set C2 itself (its edit leaves a decision about that one part): it is left alone.
    data["decisions"].append({"id": f"dec-{c2['id']}", "topic": "C2 값", "choice": "220uF", "by": "ai",
                              "reason": "출력 리플 전류 정격이 더 큰 부품으로", "components": [c2["id"]]})
    filled, report = fill_template_parts(CircuitIR.model_validate(data), inventory_from(BUCK_STOCK))
    assert next(c for c in filled.components if c.reference == "C2").manufacturer_part_number is None
    assert [a["references"] for a in report["applied"]] == [["L1"]]
    assert all("C2" not in u["references"] for u in report["unconfirmed"])

    # No template parts, or nothing in stock for them: nothing happens.
    plain = CircuitIR.model_validate({**data, "components": [{**c, "origin": "ai"} for c in data["components"]]})
    assert fill_template_parts(plain, inventory_from(BUCK_STOCK)) == (plain, {"applied": [], "unconfirmed": []})
    assert fill_template_parts(circuit, inventory_from(KOREAN_CSV)) == (circuit, {"applied": [], "unconfirmed": []})


def test_a_stocked_part_the_calculation_fails_is_not_put_in(library):
    """The stock match looks at kind, value and package, not at ratings. The app does not put in a part its own
    calculation fails in this circuit: a 16 V capacitor in the right package on an input that reaches 30 V."""
    from circuit_core.inventory import fill_template_parts

    low = STOCK_HEAD + "EEE-FK1C681P,Panasonic,전해콘덴서,680uF,10x10.2,,16V,90\n"
    base = buck_block(library, voltage=24, voltage_max=30)
    assert match_inventory(base, inventory_from(low))[0].status is MatchStatus.MATCHED
    filled, report = fill_template_parts(base, inventory_from(low))
    assert filled is base and report["applied"] == []
    [left] = report["unconfirmed"]
    assert left["references"] == ["C1"]
    assert left["why"] == "보유 부품의 정격이 이 회로에서는 부족합니다 (앱 계산 검토 실패)."
    assert [(f["severity"], f["title"], f["calc"]) for f in left["candidates"][0]["if_used"]] == [
        ("fail", "C1 (680uF): 정격 전압 부족", "16 V < 30 V")]
    # On a 12 V input the same part is fine and goes in.
    filled, report = fill_template_parts(buck_block(library, voltage=12), inventory_from(low))
    assert next(c for c in filled.components if c.reference == "C1").manufacturer_part_number == "EEE-FK1C681P"
    assert report["unconfirmed"] == []
