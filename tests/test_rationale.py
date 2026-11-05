"""Design decisions (IR 0.7.0), the calculated technical review and the rationale report."""

import pytest
from pydantic import TypeAdapter

from circuit_core import CircuitIR, validate_circuit
from circuit_core.diff import diff_circuits
from circuit_core.engineering import engineering_review, review_summary
from circuit_core.patch import PatchError, PatchOp, apply_patch
from circuit_core.rationale import rationale_markdown

from .backend_helpers import make_client, new_project, run_export, save_ir

OPS = TypeAdapter(list[PatchOp])
TWO = [{"number": "1"}, {"number": "2"}]


def ops(*raw):
    return OPS.validate_python(list(raw))


def part(cid, ref, value, sym, pins=None, fp=None, desc=None):
    return {"id": cid, "reference": ref, "value": value, "symbol_id": sym, "footprint_id": fp, "description": desc,
            "pins": pins or TWO}


LED_PINS = [{"number": "1", "name": "K"}, {"number": "2", "name": "A"}]


def board(parts, nets, decisions=(), specs=None):
    specs = specs or {}
    return CircuitIR.model_validate({
        "project_id": "t", "components": parts, "decisions": list(decisions),
        "nets": [{"id": f"n{i}", "name": name, "nodes": [{"component_id": c, "pin": p} for c, p in nodes],
                  **specs.get(name, {})}
                 for i, (name, nodes) in enumerate(nets.items())]})


DEC = {"id": "dec-r1", "topic": "R1 전류 제한", "choice": "330Ω", "by": "ai", "reason": "약 10mA",
       "calc": "(5 - 2) / 330 = 9mA", "components": ["res-001"]}


# --- decisions in the IR ---------------------------------------------------------------------------

def test_decision_rules_and_patch(led_circuit):
    after = apply_patch(led_circuit, ops({"op": "set_decision", "decision": DEC}))
    assert after.decisions[0].calc.startswith("(5 - 2)")
    with pytest.raises(PatchError) as exc:  # a change must state the current choice
        apply_patch(after, ops({"op": "set_decision", "decision": {**DEC, "choice": "1k"}}))
    assert exc.value.failures[0].code == "PRECONDITION_FAILED"
    changed = apply_patch(after, ops({"op": "set_decision", "expect_choice": "330Ω",
                                      "decision": {**DEC, "choice": "1kΩ", "by": "changed", "request": "LED 10mA"}}))
    d = diff_circuits(after, changed)
    assert d["summary"]["decisions_changed"] == 1
    assert d["decisions"][0] | {} == {"id": "dec-r1", "topic": "R1 전류 제한", "by": "changed", "before": "330Ω",
                                      "after": "1kΩ", "reason": "약 10mA"}
    # Removing the part keeps the decision on record, without the part.
    gone = apply_patch(changed, ops({"op": "remove_component", "component_id": "res-001", "expect_reference": "R1"}))
    assert gone.decisions[0].components == ()
    removed = apply_patch(changed, ops({"op": "remove_decision", "decision_id": "dec-r1", "expect_choice": "1kΩ"}))
    assert removed.decisions == ()
    data = led_circuit.model_dump(mode="json")
    data["decisions"] = [{**DEC, "id": "x", "components": ["nope"]}, {**DEC, "id": "y", "by": "changed"}]
    codes = sorted(i.error_code for i in validate_circuit(CircuitIR.model_validate(data)).issues)
    assert "DECISION_UNKNOWN_COMPONENT" in codes and "DECISION_REQUEST_MISSING" in codes


# --- calculated technical review --------------------------------------------------------------------

def rules(circuit):
    return {(f.rule, f.severity) for f in engineering_review(circuit)}


def test_capacitor_voltage_and_polarity():
    c = board([part("c1", "C1", "10uF/10V", "Device:C"), part("c2", "C2", "100uF/16V", "Device:C_Polarized"),
               part("c3", "C3", "100nF", "Device:C")],
              {"+12V": [("c1", "1"), ("c2", "2"), ("c3", "1")], "GND": [("c1", "2"), ("c2", "1"), ("c3", "2")]})
    found = rules(c)
    assert ("cap_voltage", "fail") in found  # 10 V part on 12 V
    assert ("cap_voltage", "warn") in found  # 16 V on 12 V: less than 1.5 x
    assert ("cap_voltage", "info") in found  # no rating written
    assert ("polarity", "fail") in found  # + pin on ground


def test_led_direction_series_resistor_and_current():
    c = board([part("d1", "D1", "RED", "Device:LED", LED_PINS), part("d2", "D2", "GREEN", "Device:LED", LED_PINS),
               part("r2", "R2", "100", "Device:R", fp="Resistor_SMD:R_0603_1608Metric"),
               part("d3", "D3", "RED", "Device:LED", LED_PINS)],
              {"+5V": [("d1", "2"), ("r2", "1"), ("d3", "1")], "GND": [("d1", "1"), ("d2", "1"), ("d3", "2")],
               "LED_A": [("r2", "2"), ("d2", "2")]})
    findings = engineering_review(c)
    found = {(f.rule, f.severity) for f in findings}
    assert ("led_series_r", "fail") in found  # D1 straight across the rail
    assert ("led_direction", "fail") in found  # D3 backwards
    led = next(f for f in findings if f.rule == "led_current")
    assert led.severity == "warn" and "100 Ω" in led.calc  # (5 - 2..3.2) / 100 = 18..30 mA
    power = next(f for f in findings if f.rule == "r_power")
    assert power.severity in ("warn", "fail") and "0.1 W" in power.title


def test_regulator_dropout_heat_and_decoupling():
    reg_pins = [{"number": "1", "name": "IN"}, {"number": "2", "name": "GND"}, {"number": "3", "name": "OUT"}]
    to220 = "Package_TO_SOT_THT:TO-220-3_Vertical"
    c = board([part("u1", "U1", "L7805", "Regulator_Linear:L7805", reg_pins, fp=to220),
               part("u2", "U2", "L7805", "Regulator_Linear:L7805", reg_pins),
               part("c1", "C1", "100nF", "Device:C")],
              {"+24V": [("u1", "1")], "+5V": [("u1", "3"), ("c1", "1")], "+6V": [("u2", "1")], "+5V0": [("u2", "3")],
               "GND": [("u1", "2"), ("u2", "2"), ("c1", "2")]})
    findings = engineering_review(c)
    heat = next(f for f in findings if f.rule == "reg_heat" and "U1" in f.title)
    assert heat.severity == "warn" and "1.9 W" in heat.calc and "+95 °C" in heat.calc
    dropout = next(f for f in findings if f.rule == "reg_dropout" and "U2" in f.title)
    assert dropout.severity == "fail"  # 6 V -> 5 V is under a 78xx's ~2 V dropout
    missing = [f for f in findings if f.rule == "decoupling" and f.severity == "warn"]
    assert {f.title.split(":")[0] for f in missing} >= {"U1 +24V", "U2 +6V"}


REG_PINS = [{"number": "1", "name": "IN"}, {"number": "2", "name": "GND"}, {"number": "3", "name": "OUT"}]
DIODE_PINS = [{"number": "1", "name": "K"}, {"number": "2", "name": "A"}]
TO220 = "Package_TO_SOT_THT:TO-220-3_Vertical"


def supply_board(specs, d1="1N4007", d2="1N4001"):
    """J1 -> PWR_IN -> D1 -> +24V -> U1 (7805) -> +5V, D2 from +5V back to +24V (the 7805 example's power path)."""
    return board([part("j1", "J1", "IN", "Connector_Generic:Conn_01x02"),
                  part("d1", "D1", d1, f"Diode:{d1}", DIODE_PINS), part("d2", "D2", d2, f"Diode:{d2}", DIODE_PINS),
                  part("u1", "U1", "L7805", "Regulator_Linear:L7805", REG_PINS, fp=TO220),
                  part("c1", "C1", "330nF/50V", "Device:C"), part("c2", "C2", "100nF/16V", "Device:C")],
                 {"PWR_IN": [("j1", "1"), ("d1", "2")], "+24V": [("d1", "1"), ("u1", "1"), ("c1", "1"), ("d2", "1")],
                  "+5V": [("u1", "3"), ("c2", "1"), ("d2", "2")],
                  "GND": [("j1", "2"), ("u1", "2"), ("c1", "2"), ("c2", "2")]}, specs=specs)


def test_written_voltage_and_load_current_drive_the_calculations():
    from circuit_core.engineering import supply_nets

    unknown = engineering_review(supply_board({}))
    assert any(f.rule == "unknown_voltage" and "PWR_IN" in f.title for f in unknown)
    assert next(f for f in unknown if f.rule == "reg_heat").title.endswith("100 mA당 1.9 W")
    assert not any(f.rule == "diode_current" for f in unknown)  # PWR_IN's voltage unknown: not guessed

    c = supply_board({"PWR_IN": {"voltage": 24}, "+24V": {"voltage": 23.1}, "+5V": {"load_current": 0.1}})
    findings = engineering_review(c)
    by = {f.rule: f for f in findings}
    assert "unknown_voltage" not in by
    heat = by["reg_heat"]
    assert heat.severity == "warn" and heat.title == "U1 (L7805): 발열 1.81 W (출력 100 mA)"
    assert "접합 약 116 °C" in heat.calc and "+5V 부하 100 mA(회로에 적은 값)" in heat.calc
    assert by["reg_current"].severity == "ok" and "최대 1.5 A" in by["reg_current"].title
    # D1 carries what U1 draws from +24V; D2 sits reversed between +24V and +5V.
    assert by["diode_current"].title == "D1 (1N4007): 전류 100 mA / 정격 1 A"
    assert "+24V 전류 = U1 → +5V 100 mA" in by["diode_current"].calc
    assert by["diode_reverse"].title == "D2 (1N4001): 역전압 18.1 V / 정격 50 V"
    rails = {r["net"]: r for r in supply_nets(c)}
    assert set(rails) == {"PWR_IN", "+24V", "+5V"}
    assert rails["+24V"]["voltage_from_name"] == 24 and rails["+24V"]["derived_current"] == 0.1
    assert rails["+5V"]["load_current"] == 0.1 and rails["PWR_IN"]["voltage"] == 24

    over = engineering_review(supply_board({"PWR_IN": {"voltage": 24}, "+5V": {"load_current": 2.0}}, d2="1N5817"))
    by = {(f.rule, f.severity) for f in over}
    assert ("reg_current", "fail") in by and ("diode_current", "fail") in by  # 2 A > 1.5 A, > 1 A
    assert ("diode_reverse", "warn") in by  # 1N5817: 24 V - 5 V = 19 V is 95% of its 20 V


def test_i2c_pullups_crystal_caps_and_connector_signals():
    mcu_pins = [{"number": "1", "name": "VCC"}, {"number": "2", "name": "GND"}, {"number": "3", "name": "SDA"},
                {"number": "4", "name": "SCL"}, {"number": "5", "name": "XTAL1"}, {"number": "6", "name": "XTAL2"},
                {"number": "7", "name": "TXD"}]
    c = board([part("u1", "U1", "MCU", "MCU:X", mcu_pins), part("r1", "R1", "4.7k", "Device:R"),
               part("y1", "Y1", "16MHz", "Device:Crystal"), part("c1", "C1", "22pF", "Device:C"),
               part("c2", "C2", "100nF", "Device:C"), part("j1", "J1", "UART", "Connector_Generic:Conn_01x02")],
              {"+3V3": [("u1", "1"), ("r1", "1"), ("c2", "1")], "GND": [("u1", "2"), ("c1", "2"), ("c2", "2")],
               "I2C_SDA": [("u1", "3"), ("r1", "2")], "SCL": [("u1", "4")],
               "XTAL1": [("u1", "5"), ("y1", "1"), ("c1", "1")], "XTAL2": [("u1", "6"), ("y1", "2")],
               "TXD": [("u1", "7"), ("j1", "1")]})
    findings = engineering_review(c)
    found = {(f.rule, f.severity, f.title) for f in findings}
    assert ("i2c_pullup", "ok", "I2C_SDA: 풀업 있음") in found
    assert ("i2c_pullup", "warn", "SCL: 풀업 저항 없음") in found
    assert ("crystal_caps", "warn", "Y1 (16MHz): 부하 콘덴서 없음 (2번(XTAL2))") in found
    assert not any(f.rule == "unknown_voltage" for f in findings)  # TXD on a connector is a signal, not a supply

    both = board([part("y1", "Y1", "8MHz", "Device:Crystal"), part("c1", "C1", "22pF", "Device:C"),
                  part("c2", "C2", "22pF", "Device:C")],
                 {"X1": [("y1", "1"), ("c1", "1")], "X2": [("y1", "2"), ("c2", "1")],
                  "GND": [("c1", "2"), ("c2", "2")]})
    cl = next(f for f in engineering_review(both) if f.rule == "crystal_caps")
    assert cl.severity == "ok" and "= 11 pF + 3~5 pF = 14 pF ~ 16 pF" in cl.calc


def test_net_spec_patch_diff_and_validation(led_circuit):
    net = led_circuit.nets[0].name
    after = apply_patch(led_circuit, ops({"op": "set_net_spec", "net": net, "voltage": 5.0, "load_current": 0.02}))
    spec = next(n for n in after.nets if n.name == net)
    assert (spec.voltage, spec.load_current) == (5.0, 0.02)
    with pytest.raises(PatchError) as exc:  # must state what is there now
        apply_patch(after, ops({"op": "set_net_spec", "net": net, "voltage": 3.3}))
    assert exc.value.failures[0].code == "PRECONDITION_FAILED"
    cleared = apply_patch(after, ops({"op": "set_net_spec", "net": net, "expect_voltage": 5.0,
                                      "expect_load_current": 0.02}))
    assert next(n for n in cleared.nets if n.name == net).voltage is None
    d = diff_circuits(led_circuit, after)
    assert d["summary"]["net_specs_changed"] == 2
    assert d["net_specs"][0] == {"net": net, "field": "voltage", "before": None, "after": 5.0}
    with pytest.raises(PatchError):
        apply_patch(led_circuit, ops({"op": "set_net_spec", "net": "NOPE", "voltage": 1.0}))

    data = led_circuit.model_dump(mode="json")
    data["nets"].append({"id": "n-x", "name": "+5V", "voltage": 3.3, "nodes": data["nets"][0]["nodes"][:0] or
                         [{"component_id": "led-001", "pin": "1"}]})
    data["nets"][0]["nodes"] = [n for n in data["nets"][0]["nodes"] if n != {"component_id": "led-001", "pin": "1"}]
    data["nets"] = [n for n in data["nets"] if n["nodes"]]
    issues = validate_circuit(CircuitIR.model_validate(data)).issues
    assert any(i.error_code == "NET_VOLTAGE_MISMATCH" and i.severity == "warning" for i in issues)


def test_motor_driver_supply_current_limit_and_loss():
    drv = [{"number": "1", "name": "GND"}, {"number": "2", "name": "IN2"}, {"number": "3", "name": "IN1"},
           {"number": "4", "name": "ILIM"}, {"number": "5", "name": "VM"}, {"number": "6", "name": "OUT1"},
           {"number": "7", "name": "GND"}, {"number": "8", "name": "OUT2"}, {"number": "9", "name": "GND"}]

    def make(vm_net, r_ilim, specs):
        return board([part("u1", "U1", "DRV8871DDA", "Driver_Motor:DRV8871DDA", drv),
                      part("r1", "R1", r_ilim, "Device:R"), part("c1", "C1", "100nF/50V", "Device:C")],
                     {vm_net: [("u1", "5"), ("c1", "1")], "ILIM": [("u1", "4"), ("r1", "1")],
                      "GND": [("u1", "1"), ("u1", "7"), ("u1", "9"), ("r1", "2"), ("c1", "2")]}, specs=specs)

    found = {f.rule: f for f in engineering_review(make("+12V", "30k", {"+12V": {"load_current": 1.0}}))}
    assert found["motor_vm"].severity == "ok" and found["motor_ilim"].title.endswith("전류 제한 2.13 A (R R1 30k)")
    assert found["motor_heat"].severity == "ok" and "565 mW" in found["motor_heat"].title
    assert found["decoupling"].severity == "ok"  # VM counts as a supply pin
    bad = {(f.rule, f.severity) for f in engineering_review(make("+48V", "15k", {"+48V": {"load_current": 2.0}}))}
    assert {("motor_vm", "fail"), ("motor_ilim", "warn"), ("motor_heat", "warn")} <= bad  # 48 V > 45 V, 4.3 A, 2.3 W


def test_examples_have_no_failures():
    for ex in ("lm7805-24v-to-5v", "ne555-led-board", "atmega328p-led-board", "dc-motor-drv8871"):
        from pathlib import Path

        c = CircuitIR.model_validate_json(Path(f"examples/{ex}/circuit.json").read_text(encoding="utf-8"))
        assert review_summary(engineering_review(c))["fail"] == 0, ex


# --- the report ---------------------------------------------------------------------------------------

def test_report_groups_decisions_and_marks_parts_without_a_reason(led_circuit):
    c = apply_patch(led_circuit, ops(
        {"op": "set_decision", "decision": {**DEC, "by": "requirement", "request": "LED는 빨간색"}},
        {"op": "set_decision", "decision": {
            "id": "dec-j1", "topic": "입력 커넥터", "choice": "2핀 헤더", "by": "changed", "request": "USB-C",
            "reason": "부품 수를 줄이려고", "components": ["conn-001"]}}))
    md = rationale_markdown(c, requirements="LED 보드\n- 빨간 LED", proposals=[
        {"id": "0001", "title": "첫 설계", "source": "ai_design", "status": "approved", "result_revision": 1,
         "rationale": "요약", "ai": {"model": "m", "assumptions": ["5V 입력"], "citations": [
             {"reference": "D1", "page": 3, "text": "Vf 2.0V"}]}, "diff": {"components_added": [1, 2]}}])
    assert "### 사용자 요구를 그대로 반영한 것 (1)" in md and "### 사용자 요구를 바꾼 것 (1)" in md
    assert "- 요청: USB-C" in md and "- 계산: (5 - 2) / 330 = 9mA" in md
    assert "| D1 | LED |" in md and "**근거 기록 없음**" in md  # D1 has no decision
    assert "AI 가정: 5V 입력" in md and "데이터시트 인용 (D1 p.3): Vf 2.0V" in md
    assert "- [ ] 요구 수정 확인: 입력 커넥터 — 2핀 헤더" in md
    assert "## 5. 기술 검토 (자동 계산)" in md


def test_template_and_ai_design_record_decisions(library):
    from ai_engine.design import ResolvedPart, _decisions
    from circuit_core.templates import TEMPLATES, as_circuit, instantiate

    def pins_of(symbol_id):
        return [(p.number, p.name) for p in library.get(symbol_id).pins]

    inst = instantiate(TEMPLATES["ldo-ams1117-3v3"], {}, None, pins_of)
    d = inst.decision
    assert d.by == "template" and "AMS1117" in (d.source or "") and set(d.components) == {c.id for c in inst.components}
    assert as_circuit(inst, "x").decisions == (d,)

    r = ResolvedPart(ref="R1", role="LED 전류 제한", category="resistor", value="1k", symbol_id="Device:R", pins=[],
                     how="generic", reason="3mA 목표", basis="changed", request="10mA", calc="(5-2)/1k")
    blank = ResolvedPart(ref="C1", role="디커플링", category="capacitor", value="100nF", symbol_id="Device:C", pins=[],
                         how="generic", basis="?")
    out = _decisions([r, blank], [], {})
    assert out[0] | {} == {"id": "dec-r1", "topic": "R1 LED 전류 제한", "choice": "1k", "by": "changed",
                           "reason": "3mA 목표", "request": "10mA", "calc": "(5-2)/1k",
                           "source": "KiCad 심볼 Device:R (generic)", "components": ["r1"]}
    assert out[1]["by"] == "ai" and "이유를 남기지 않았습니다" in out[1]["reason"]


# --- backend --------------------------------------------------------------------------------------------

def test_report_and_review_endpoints_and_output_file(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    client.put(f"/api/projects/{pid}/requirements", json={"text": "빨간 LED 하나"})
    save_ir(client, pid)
    url = f"/api/projects/{pid}/boards/a"
    r = client.post(f"{url}/proposals", json={"title": "R1 근거", "rationale": "검토용", "ops": [
        {"op": "set_decision", "decision": {**DEC, "by": "engineer"}}]})
    client.app.state.jobs.wait(r.json()["job"]["id"], timeout=300)
    client.post(f"{url}/proposals/{r.json()['proposal']['id']}/approve",
                json={"accept_failed_checks": True, "generate": False})

    review = client.get(f"{url}/technical-review").json()
    assert review["revision"] == 2 and set(review["summary"]) == {"fail", "warn", "info", "ok"}
    assert [r["net"] for r in review["supply_nets"]] == ["VIN"]  # the LED example's input, no voltage in the name
    report = client.get(f"{url}/documents/rationale").json()
    md = report["markdown"]
    assert "> 빨간 LED 하나" in md and "### 사람(엔지니어)이 정한 것" not in md
    assert "### 엔지니어가 정한 것 (1)" in md and "#1 R1 근거" in md and "제안 근거: 검토용" in md

    job = run_export(client, pid)
    assert "rationale.md" in job["result"]["artifacts"]
    text = (tmp_path / "projects" / pid / "boards" / "a" / "outputs" / "rationale.md").read_text(encoding="utf-8")
    assert "R1 전류 제한" in text and "작성 " not in text  # no time stamp: same revision, same file
