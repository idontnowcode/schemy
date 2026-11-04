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
