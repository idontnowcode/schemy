"""Programmatic test circuits."""

from circuit_core import CircuitIR

ROTATIONS = (0, 90, 180, 270)


def rotation_ring() -> CircuitIR:
    """R and LED at every rotation, chained pin 2 -> next pin 1 in a closed ring.

    If the generator put any pin in the wrong place, KiCad's netlist would show
    it unconnected or on the wrong net.
    """
    parts = [("R", "Device:R", "10k", {}) for _ in ROTATIONS] + [
        ("D", "Device:LED", "LED", {"1": "K", "2": "A"}) for _ in ROTATIONS
    ]
    components, layout = [], {}
    counters: dict[str, int] = {}
    for i, (prefix, symbol, value, names) in enumerate(parts):
        counters[prefix] = counters.get(prefix, 0) + 1
        comp_id = f"c{i}"
        components.append({
            "id": comp_id,
            "reference": f"{prefix}{counters[prefix]}",
            "value": value,
            "symbol_id": symbol,
            "pins": [{"number": n, "name": names.get(n)} for n in ("1", "2")],
        })
        layout[comp_id] = {
            "x": 50.8 + (i % 4) * 38.1,
            "y": 63.5 + (i // 4) * 38.1,
            "rotation": ROTATIONS[i % 4],
        }
    nets = []
    for i in range(len(parts)):
        nxt = (i + 1) % len(parts)
        nets.append({
            "id": f"n{i}",
            "name": f"N{i + 1}",
            "nodes": [{"component_id": f"c{i}", "pin": "2"}, {"component_id": f"c{nxt}", "pin": "1"}],
        })
    return CircuitIR.model_validate({
        "project_id": "rotation-ring",
        "title": "Rotation coverage",
        "components": components,
        "nets": nets,
        "layout": layout,
    })


def two_page_led(sheet_names=("전원 입력", "LED 출력")) -> CircuitIR:
    """The LED example split over two pages; LED_A is local to page 2, GND crosses pages."""
    import json

    from .conftest import LED_CIRCUIT

    data = json.loads(LED_CIRCUIT.read_text(encoding="utf-8"))
    data["layout"] = {}
    data["sheets"] = [
        {"id": "power", "name": sheet_names[0], "components": ["conn-001"]},
        {"id": "led", "name": sheet_names[1], "components": ["res-001", "led-001"]},
    ]
    return CircuitIR.model_validate(data)


def many_resistors(count: int) -> CircuitIR:
    """``count`` resistors in a chain, no layout: exercises automatic placement and paper choice."""
    components = [{"id": f"r{i}", "reference": f"R{i}", "value": "10k", "symbol_id": "Device:R",
                   "pins": [{"number": "1"}, {"number": "2"}]} for i in range(1, count + 1)]
    nets = [{"id": f"n{i}", "name": f"N{i}", "nodes": [{"component_id": f"r{i}", "pin": "2"},
                                                         {"component_id": f"r{i + 1}", "pin": "1"}]}
            for i in range(1, count)]
    return CircuitIR.model_validate({"project_id": "many", "components": components, "nets": nets})


def can_node(supply: str = "+3V3", decoupling: str | None = "100nF", termination: str | None = "120") -> CircuitIR:
    """SN65HVD230 CAN transceiver with a decoupling capacitor and a bus termination resistor.

    ``supply`` names the supply net (the voltage is read from the name); ``None`` leaves a part out.
    Rs (pin 8) goes straight to GND and Vref (pin 5) is marked no-connect.
    """
    lib_pins = [("1", "D"), ("2", "GND"), ("3", "VCC"), ("4", "R"), ("5", "Vref"), ("6", "CANL"), ("7", "CANH"),
                ("8", "Rs")]
    conn = [{"number": "1", "name": "Pin_1"}, {"number": "2", "name": "Pin_2"}]
    two = [{"number": "1", "name": None}, {"number": "2", "name": None}]
    components = [
        {"id": "u1", "reference": "U1", "value": "SN65HVD230", "symbol_id": "Interface_CAN_LIN:SN65HVD230",
         "footprint_id": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "manufacturer": "Texas Instruments",
         "manufacturer_part_number": "SN65HVD230DR",
         "pins": [{"number": n, "name": nm, "no_connect": n == "5"} for n, nm in lib_pins]},
        {"id": "j1", "reference": "J1", "value": "CAN", "symbol_id": "Connector_Generic:Conn_01x02", "pins": conn},
        {"id": "j2", "reference": "J2", "value": "UART", "symbol_id": "Connector_Generic:Conn_01x02", "pins": conn},
        {"id": "j3", "reference": "J3", "value": "PWR", "symbol_id": "Connector_Generic:Conn_01x02", "pins": conn},
    ]
    nets = {supply: [("j3", "1"), ("u1", "3")], "GND": [("j3", "2"), ("u1", "2"), ("u1", "8")],
            "CANH": [("u1", "7"), ("j1", "1")], "CANL": [("u1", "6"), ("j1", "2")],
            "TXD": [("j2", "1"), ("u1", "1")], "RXD": [("j2", "2"), ("u1", "4")]}
    if decoupling:
        components.append({"id": "c1", "reference": "C1", "value": decoupling, "symbol_id": "Device:C",
                           "footprint_id": "Capacitor_SMD:C_0603_1608Metric", "pins": two})
        nets[supply].append(("c1", "1"))
        nets["GND"].append(("c1", "2"))
    if termination:
        components.append({"id": "r1", "reference": "R1", "value": termination, "symbol_id": "Device:R",
                           "footprint_id": "Resistor_SMD:R_0603_1608Metric", "pins": two})
        nets["CANH"].append(("r1", "1"))
        nets["CANL"].append(("r1", "2"))
    return CircuitIR.model_validate({
        "project_id": "can-node",
        "title": "CAN node (SN65HVD230)",
        "components": components,
        "nets": [{"id": f"net-{i}", "name": name, "nodes": [{"component_id": c, "pin": p} for c, p in nodes]}
                 for i, (name, nodes) in enumerate(nets.items())],
    })


def ldo_supply() -> CircuitIR:
    """5 V in -> AMS1117-3.3 (a derived KiCad symbol) -> 3.3 V out, with input and output capacitors."""
    two = [{"number": "1", "name": None}, {"number": "2", "name": None}]
    conn = [{"number": "1", "name": "Pin_1"}, {"number": "2", "name": "Pin_2"}]
    components = [
        {"id": "j1", "reference": "J1", "value": "5V_IN", "symbol_id": "Connector_Generic:Conn_01x02", "pins": conn},
        {"id": "j2", "reference": "J2", "value": "3V3_OUT", "symbol_id": "Connector_Generic:Conn_01x02", "pins": conn},
        {"id": "u1", "reference": "U1", "value": "AMS1117-3.3", "symbol_id": "Regulator_Linear:AMS1117-3.3",
         "footprint_id": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
         "pins": [{"number": "1", "name": "GND"}, {"number": "2", "name": "VO"}, {"number": "3", "name": "VI"}]},
        {"id": "c1", "reference": "C1", "value": "10uF", "symbol_id": "Device:C", "pins": two},
        {"id": "c2", "reference": "C2", "value": "22uF", "symbol_id": "Device:C", "pins": two},
    ]
    nets = {"+5V": [("j1", "1"), ("u1", "3"), ("c1", "1")],
            "+3V3": [("u1", "2"), ("c2", "1"), ("j2", "1")],
            "GND": [("j1", "2"), ("u1", "1"), ("c1", "2"), ("c2", "2"), ("j2", "2")]}
    return CircuitIR.model_validate({
        "project_id": "ldo-supply", "title": "5 V to 3.3 V LDO", "components": components,
        "nets": [{"id": f"net-{i}", "name": n, "nodes": [{"component_id": c, "pin": p} for c, p in nodes]}
                 for i, (n, nodes) in enumerate(nets.items())],
    })
