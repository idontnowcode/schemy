import json
import math
import os

import pytest

from circuit_core import CircuitIR
from kicad_adapter import SCHEMATIC_FILE_VERSION, SchematicGenerationError, generate_schematic, sexpr
from kicad_adapter.schematic import project_file
from kicad_adapter.sexpr import child, children, head, unquote

from .circuits import rotation_ring
from .conftest import (
    EXAMPLE_DIR,
    EXAMPLE_NAME,
    I2C_DIR,
    I2C_NAME,
    MOTOR_DIR,
    MOTOR_NAME,
    PRACTICAL_NAMES,
    REGULATOR_DIR,
    REGULATOR_NAME,
    SAMPLE_DIR,
    SAMPLE_NAME,
    TIMER_DIR,
    TIMER_NAME,
)

KICAD_10_NEW_PROJECT_TEMPLATE = """{
  "board": {
    "design_settings": {
      "defaults": {},
      "diff_pair_dimensions": [],
      "drc_exclusions": [],
      "rules": {},
      "track_widths": [],
      "via_dimensions": []
    }
  },
  "boards": [],
  "libraries": {
    "pinned_footprint_libs": [],
    "pinned_symbol_libs": []
  },
  "meta": {
    "filename": "kicad.kicad_pro",
    "version": 1
  },
  "net_settings": {
    "classes": [],
    "meta": {
      "version": 0
    }
  },
  "pcbnew": {
    "page_layout_descr_file": ""
  },
  "sheets": [],
  "text_variables": {}
}
"""


def error_codes(exc_info):
    return sorted(i.error_code for i in exc_info.value.issues)


def check_golden(circuit, library, workdir, name, folder):
    """Every page file (the root and, on a multi-page board, each sub-sheet) is compared byte for byte."""
    generate_schematic(circuit, library, project_name=name).write(workdir)
    pages = sorted(f.name for f in workdir.glob(f"{name}*.kicad_sch"))
    pro = f"{name}.kicad_pro"
    if os.environ.get("AICS_UPDATE_GOLDEN"):
        for file in (*pages, pro):
            (folder / file).write_text((workdir / file).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    assert pages == sorted(f.name for f in folder.glob(f"{name}*.kicad_sch")), "page files differ"
    for sch in pages:
        assert (workdir / sch).read_text(encoding="utf-8") == (folder / sch).read_text(encoding="utf-8"), (
            f"{sch} differs from the generator output. If it was saved from KiCad, restore it with "
            "`git checkout -- examples/`; if the generator changed on purpose, rerun with AICS_UPDATE_GOLDEN=1."
        )
    meta = json.loads((folder / pro).read_text(encoding="utf-8"))["meta"]
    assert meta["filename"] == pro


def test_example_project_matches_generator(led_circuit, library, workdir):
    """examples/led-resistor-connector is the golden output and a KiCad project users can open.

    The schematic is compared byte for byte. The .kicad_pro is only checked for its
    file name, because KiCad rewrites that file whenever someone opens and closes the
    project; its content is covered by test_project_file_is_kicads_new_project_template.
    """
    check_golden(led_circuit, library, workdir, EXAMPLE_NAME, EXAMPLE_DIR)


def test_sample_board_matches_generator(sample_board, library, workdir):
    """examples/atmega328p-led-board: the sample MCU board is a second golden, with a template, a crystal
    block and a 32-pin IC, so layout changes show up here too."""
    check_golden(sample_board, library, workdir, SAMPLE_NAME, SAMPLE_DIR)


def test_regulator_board_matches_generator(regulator_board, library, workdir):
    """examples/lm7805-24v-to-5v: a regulator block drawn with wired input and output lines - a fourth golden."""
    check_golden(regulator_board, library, workdir, REGULATOR_NAME, REGULATOR_DIR)


def test_motor_board_matches_generator(motor_board, library, workdir):
    """examples/dc-motor-drv8871 is a golden output too (a power input through a fuse and a diode, a motor driver)."""
    check_golden(motor_board, library, workdir, MOTOR_NAME, MOTOR_DIR)


def test_i2c_sensor_board_matches_generator(library, workdir):
    """examples/i2c-sensor-3v3: a 5 V -> 3.3 V LDO board with I2C pull-ups, designed and checked through the app
    (KiCad ERC 0/0, real LM1117 datasheet) - a golden too."""
    board = CircuitIR.model_validate_json((I2C_DIR / "circuit.json").read_text(encoding="utf-8"))
    check_golden(board, library, workdir, I2C_NAME, I2C_DIR)


@pytest.mark.parametrize("name", PRACTICAL_NAMES)
def test_practical_boards_match_generator(name, library, workdir):
    """The practical example boards are goldens too: each has a block the older examples lack (a USB-C receptacle,
    optocouplers, a relay, an RS-485 transceiver, an op-amp input), so their layout changes show up here."""
    folder = EXAMPLE_DIR.parent / name
    board = CircuitIR.model_validate_json((folder / "circuit.json").read_text(encoding="utf-8"))
    check_golden(board, library, workdir, name, folder)


@pytest.mark.parametrize("name", ["atmega328p-led-board", "rs485-3v3", *PRACTICAL_NAMES])
def test_notes_stay_on_the_page(name, library):
    """A note is never placed left of or above the page edge, where KiCad cuts it off (the ATmega board's
    power note used to sit at x = -22.86 mm)."""
    from kicad_adapter.drawing import PAGE_EDGE, note_rect

    board = CircuitIR.model_validate_json((EXAMPLE_DIR.parent / name / "circuit.json").read_text(encoding="utf-8"))
    doc = sexpr.parse(generate_schematic(board, library).text)
    for t in children(doc, "text"):
        at = (float(child(t, "at")[1]), float(child(t, "at")[2]))
        rect = note_rect(at, unquote(t[1]))
        assert rect[0] >= PAGE_EDGE and rect[1] >= PAGE_EDGE, unquote(t[1])


def test_internal_regulator_pins_and_sheet_boxes(library):
    """examples/scu-main-board: LAN8742A's VDDCR is its own regulator's output though the library types it as a power
    input, so its net gets a PWR_FLAG; the sub-sheet boxes on page 1 keep their "File: ..." text clear of each other."""
    from kicad_adapter.drawing import CHAR

    folder = EXAMPLE_DIR.parent / "scu-main-board"
    board = CircuitIR.model_validate_json((folder / "circuit.json").read_text(encoding="utf-8"))
    g = generate_schematic(board, library, project_name="scu-main-board")
    assert "PHY_VDDCR" in g.power_flags
    boxes = []
    for node in children(sexpr.parse(g.text), "sheet"):
        x, y = (float(v) for v in child(node, "at")[1:3])
        w = float(child(node, "size")[1])
        file = next(unquote(p[2]) for p in children(node, "property") if unquote(p[1]) == "Sheetfile")
        boxes.append((x, y, x + max(w, len(f"File: {file}") * CHAR)))
    assert len(boxes) == 6
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[1] != b[1] or a[2] <= b[0] or b[2] <= a[0], (a, b)


def test_mixed_host_connector_is_the_power_input(library):
    """J1 brings 5V, GND, SCL and SDA in. It is still where the power enters: drawn at the left with the
    I2C lines, while the fuse stays on the regulator's input line; notes sit by their own parts."""
    from kicad_adapter.drawing import note_rect

    board = CircuitIR.model_validate_json((I2C_DIR / "circuit.json").read_text(encoding="utf-8"))
    g = generate_schematic(board, library)
    pins: dict[str, list[tuple[float, float]]] = {}
    for a in g.anchors:
        pins.setdefault(a.reference, []).append((a.x, a.y))
    left = {ref: min(x for x, _ in pts) for ref, pts in pins.items()}
    assert left["J1"] == min(left.values())
    vi = next((a.x, a.y) for a in g.anchors if a.reference == "U1" and a.pin == "3")
    assert {y for _, y in pins["F1"]} == {vi[1]} and max(x for x, _ in pins["F1"]) < vi[0]  # F1 -> C1 -> U1 VI

    doc = sexpr.parse(g.text)
    at = {unquote(t[1]): (float(child(t, "at")[1]), float(child(t, "at")[2])) for t in children(doc, "text")}
    refs = {c.id: c.reference for c in board.components}
    for note in board.notes:
        rect = note_rect(at[note.text], note.text)
        near = min(math.dist(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), p)
                   for cid in note.components for p in pins[refs[cid]])
        assert near <= 25.4, note.text  # not left at the top of the page


def test_sideways_power_text_runs_away_from_the_arrow(library):
    """KiCad turns a field with its symbol, which mirrors the justification on a symbol turned 90°: the file
    says the opposite so the text still runs away from the arrow (it used to run back over the part)."""
    from kicad_adapter.drawing import PinView, _port, orient, upright

    cases = [("power:PWR_FLAG", (-1, 0), 90, "left"), ("power:PWR_FLAG", (1, 0), 270, "left"),
             ("power:GND", (-1, 0), 270, "right"), ("power:GND", (1, 0), 90, "right")]
    for lib_id, d, rotation, written in cases:
        sym = library.get(lib_id)
        port = _port(PinView("1", (0.0, 0.0), d, "X"), sym, 0.0, orient(upright(sym), d) or 0)
        assert (port.rotation, port.value_at[2]) == (rotation, written), (lib_id, d)


def test_power_input_through_a_series_chain_is_one_block(motor_board, library):
    """J1 -> fuse -> reverse-polarity diode -> +12V is one straight line, and the parts on +12V/GND only (TVS, bulk
    capacitor, power LED) gather by it, while the small capacitor stays wired at the driver's VM pin."""
    g = generate_schematic(motor_board, library)
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    j1, f1a, f1b = pins[("J1", "1")], pins[("F1", "1")], pins[("F1", "2")]
    d1a, d1k = pins[("D1", "2")], pins[("D1", "1")]
    assert j1[1] == f1a[1] == f1b[1] == d1a[1] == d1k[1] and j1[0] < f1a[0] < f1b[0] < d1a[0] < d1k[0]
    for ref in ("D2", "C1", "R2", "D3"):
        x, y = pins[(ref, "1")]
        assert abs(x - j1[0]) <= 101.6 and abs(y - j1[1]) <= 50.8, ref
        assert abs(x - pins[("U1", "5")][0]) > abs(x - d1k[0]), ref  # nearer the input line than the driver
    labels = [unquote(lbl[1]) for lbl in children(sexpr.parse(g.text), "label")]
    assert labels.count("PWR_IN") == 1 and labels.count("VIN_F") == 1  # each line named once, not split
    assert _wired(g.text, pins[("C2", "1")], pins[("U1", "5")])


def test_regulator_block_is_drawn_like_the_textbook(regulator_board, library):
    """The 7805's input and output rails are wires: J1 - D1 - input caps - U1 - output caps - LED - J2 on one
    straight line, capacitors down to ground, the protection diode over the regulator, and one power symbol per
    wired piece of a rail (no labels on rails)."""
    g = generate_schematic(regulator_board, library)
    drawn = drawn_nets(g)
    connected = {nodes: tags for nodes, tags in drawn.items() if len(nodes) > 1 or tags}
    assert set(connected) == set(ir_nets(regulator_board))
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    line = pins[("U1", "1")][1]
    on_line = [("J1", "1"), ("D1", "1"), ("D1", "2"), ("C1", "1"), ("C2", "1"), ("U1", "3"), ("C3", "1"),
               ("C4", "1"), ("R1", "1"), ("J2", "1")]
    assert all(pins[k][1] == line for k in on_line), {k: pins[k] for k in on_line}
    xs = [pins[k][0] for k in (("J1", "1"), ("D1", "2"), ("C2", "1"), ("U1", "1"), ("U1", "3"), ("J2", "1"))]
    assert xs == sorted(xs)  # left to right: input, regulator, output
    for cap in ("C1", "C2", "C3", "C4"):
        assert pins[(cap, "2")][1] > line, cap  # down to ground
    assert pins[("D2", "1")][1] < line and pins[("D2", "2")][1] < line  # over the regulator
    doc = sexpr.parse(g.text)
    labels = {unquote(lbl[1]) for lbl in children(doc, "label")}
    assert not labels & {"+24V", "+5V"}
    values = [next(unquote(p[2]) for p in children(s, "property") if unquote(p[1]) == "Value")
              for s in children(doc, "symbol") if unquote(child(s, "lib_id")[1]) in ("power:+24V", "power:+5V")]
    assert sorted(values) == ["+24V", "+5V"]


def test_timer_board_matches_generator(timer_board, library, workdir):
    """examples/ne555-led-board: a board without an MCU, with English notes - a third golden."""
    check_golden(timer_board, library, workdir, TIMER_NAME, TIMER_DIR)


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
    # Rails are drawn with KiCad power symbols (VIN has no symbol of its own, so VCC is renamed), and
    # power entering through J1 gets a PWR_FLAG.
    assert embedded == ["Connector_Generic:Conn_01x02", "Device:LED", "Device:R", "power:GND", "power:PWR_FLAG",
                        "power:VCC"]
    placed = {
        unquote(child(s, "lib_id")[1]): s for s in children(doc, "symbol")
    }
    led = placed["Device:LED"]
    props = {unquote(p[1]): unquote(p[2]) for p in children(led, "property")}
    assert props["Reference"] == "D1"
    assert props["Footprint"] == "LED_SMD:LED_0603_1608Metric"
    assert props["AICS_ID"] == "led-001"
    # R1 and D1 are joined by a wire; the net keeps its name with one label on it.
    labels = sorted(unquote(lbl[1]) for lbl in children(doc, "label"))
    assert labels == ["LED_A"]
    power_values = sorted(
        next(unquote(p[2]) for p in children(s, "property") if unquote(p[1]) == "Value")
        for s in children(doc, "symbol") if unquote(child(s, "lib_id")[1]) in ("power:GND", "power:VCC"))
    assert set(power_values) == {"GND", "VIN"}


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
    assert set(sources) == {"Connector_Generic:Conn_01x02", "Device:LED", "Device:R", "power:GND",
                            "power:PWR_FLAG", "power:VCC"}
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
    # Symbol instances are keyed by project name, so it must match the .kicad_pro file name.
    assert '(project "demo"' in sch.read_text(encoding="utf-8")
    pro = json.loads((workdir / "demo.kicad_pro").read_text(encoding="utf-8"))
    assert pro["meta"] == {"filename": "demo.kicad_pro", "version": 1}


def test_project_file_is_kicads_new_project_template():
    """Same bytes as KiCad 10.0.8 share/kicad/template/kicad.kicad_pro when named 'kicad'."""
    assert project_file("kicad") == KICAD_10_NEW_PROJECT_TEMPLATE


@pytest.mark.parametrize("name", ["../evil", "a/b", "", "with space"])
def test_rejects_unsafe_project_name(led_circuit, library, name):
    with pytest.raises(ValueError, match="project name"):
        generate_schematic(led_circuit, library, project_name=name)


def test_bbox_covers_every_pin_and_label(led_circuit, library):
    generated = generate_schematic(led_circuit, library)
    x, y, w, h = generated.bbox
    for a in generated.anchors:
        assert x < a.x < x + w and y < a.y < y + h
    # Much smaller than the A4 sheet, so the preview can zoom in.
    assert w < 80 and h < 80


# --- pages -------------------------------------------------------------------------------

def test_two_pages_structure(library):
    from .circuits import two_page_led

    g = generate_schematic(two_page_led(), library)
    assert [p.filename for p in g.pages] == ["schematic.kicad_sch", "schematic-p2-led.kicad_sch"]
    root, sub = (sexpr.parse(p.text) for p in g.pages)

    def parts(doc):
        return [unquote(child(s, "lib_id")[1]) for s in children(doc, "symbol")
                if not unquote(child(s, "lib_id")[1]).startswith("power:")]

    # Root holds J1 and one sheet symbol pointing at page 2.
    assert parts(root) == ["Connector_Generic:Conn_01x02"]
    sheet = child(root, "sheet")
    props = {unquote(p[1]): unquote(p[2]) for p in children(sheet, "property")}
    assert props == {"Sheetname": "LED 출력", "Sheetfile": "schematic-p2-led.kicad_sch"}
    assert child(child(child(child(sheet, "instances"), "project"), "path"), "page")[1] == '"2"'
    # The rails crossing pages are power symbols (global by nature); LED_A stays a local label on page 2.
    assert list(children(root, "global_label")) == [] and list(children(sub, "global_label")) == []
    assert sorted(unquote(lbl[1]) for lbl in children(sub, "label")) == ["LED_A"]
    # Sub-page symbols are instanced under the sheet symbol, and only the root has sheet_instances.
    path = child(child(child(next(children(sub, "symbol")), "instances"), "project"), "path")[1]
    assert unquote(path) == f"/{g.root_uuid}/{unquote(child(sheet, 'uuid')[1])}"
    assert child(sub, "sheet_instances") is None
    assert {a.sheet for a in g.anchors if a.reference == "D1"} == {"led"}


def test_single_page_list_is_the_same_as_no_pages(led_circuit_data, library):
    plain = generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    led_circuit_data["sheets"] = [{"id": "p1", "name": "Page 1", "components": []}]
    one = generate_schematic(CircuitIR.model_validate(led_circuit_data), library)
    assert one.text == plain.text


def test_page_rules(led_circuit_data):
    from circuit_core import validate_circuit

    led_circuit_data["sheets"] = [
        {"id": "a", "name": "A", "components": ["res-001", "ghost"]},
        {"id": "b", "name": "A", "components": ["res-001"]},
    ]
    codes = sorted(i.error_code for i in validate_circuit(CircuitIR.model_validate(led_circuit_data)).errors)
    assert codes == ["COMPONENT_ON_TWO_SHEETS", "DUPLICATE_SHEET_NAME", "SHEET_UNKNOWN_COMPONENT"]


def test_auto_layout_picks_a_bigger_sheet_when_needed(library):
    from .circuits import many_resistors

    small = generate_schematic(many_resistors(6), library)
    assert small.pages[0].paper == "A4"
    big = generate_schematic(many_resistors(200), library)
    assert big.pages[0].paper in ("A3", "A2", "A1")
    assert not [w for w in big.warnings if w.error_code == "PAGE_OVERFLOW"]
    # No two symbols share an origin and every pin is inside the sheet.
    width, height = {"A3": (420, 297), "A2": (594, 420), "A1": (841, 594)}[big.pages[0].paper]
    assert all(0 < a.x < width and 0 < a.y < height for a in big.anchors)


# --- no-connect markers, automatic PWR_FLAG, derived symbols ------------------------------------

def test_no_connect_pins_get_a_marker(library):
    from .circuits import can_node

    generated = generate_schematic(can_node(), library)
    text = generated.text
    assert text.count("(no_connect") == 1  # U1 pin 5 (Vref)
    from kicad_adapter import sexpr

    vref = next(a for a in generated.anchors if a.reference == "U1" and a.pin == "5")
    marker = sexpr.child(sexpr.parse(text), "no_connect")
    assert (float(marker[1][1]), float(marker[1][2])) == (vref.x, vref.y) and vref.net is None


def test_power_flags_only_where_power_enters_through_a_connector(library):
    from .circuits import can_node, ldo_supply

    can = generate_schematic(can_node(), library)
    assert can.power_flags == ["+3V3", "GND"]
    assert can.text.count('(lib_id "power:PWR_FLAG")') == 2 and '"#FLG01"' in can.text
    assert "AICS_ID" not in can.text.split('(lib_id "power:PWR_FLAG")', 1)[1].split("(instances", 1)[0]
    # The regulator output drives +3V3 itself: only the input and GND get a flag.
    assert generate_schematic(ldo_supply(), library).power_flags == ["+5V", "GND"]
    # Without a connector nothing marks the entry point, so no flag hides the problem.
    data = can_node().model_dump(mode="json")
    data["components"] = [c for c in data["components"] if c["id"] != "j3"]
    for net in data["nets"]:
        net["nodes"] = [n for n in net["nodes"] if n["component_id"] != "j3"]
    assert generate_schematic(CircuitIR.model_validate(data), library).power_flags == []


def test_missing_power_library_is_a_warning(tmp_path):
    import shutil

    from kicad_adapter import SymbolLibrary

    from .circuits import can_node
    from .conftest import SYMBOL_DIR

    for f in SYMBOL_DIR.glob("*.kicad_sym"):
        if f.name != "power.kicad_sym":
            shutil.copy(f, tmp_path / f.name)
    generated = generate_schematic(can_node(), SymbolLibrary([tmp_path]))
    assert generated.power_flags == [] and "PWR_FLAG_UNAVAILABLE" in {w.error_code for w in generated.warnings}


def test_derived_symbol_is_flattened(library):
    from .circuits import ldo_supply

    sym = library.get("Regulator_Linear:AMS1117-3.3")
    assert [(p.number, p.name, p.electrical_type) for p in sym.pins] == [
        ("1", "GND", "power_in"), ("2", "VO", "power_out"), ("3", "VI", "power_in")]
    assert sym.field("Value").value == "AMS1117-3.3" and "SOT-223" in sym.field("Footprint").value
    subs = [n[1] for n in sym.node[2:] if isinstance(n, list) and n[0] == "symbol"]
    assert subs == ['"AMS1117-3.3_0_1"', '"AMS1117-3.3_1_1"'] and sexpr_child(sym.node, "extends") is None
    assert '(symbol "Regulator_Linear:AMS1117-3.3"' in generate_schematic(ldo_supply(), library).text


def sexpr_child(node, name):
    from kicad_adapter import sexpr

    return sexpr.child(node, name)


# --- drawing: power symbols, wires, labels ------------------------------------------------------

def drawn_nets(generated) -> dict[frozenset, set[str]]:
    """Connectivity read back from the drawing alone (wires, labels, power symbols), as KiCad would: groups of
    (reference, pin), each with the names that label it. Independent of the generator's own bookkeeping."""
    parent: dict = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def join(a, b):
        parent[find(a)] = find(b)

    def pt(node):
        return round(float(node[1]), 3), round(float(node[2]), 3)

    names: dict = {}
    for page in generated.pages:
        doc = sexpr.parse(page.text)
        sheet = page.sheet_id
        segments = []
        for w in children(doc, "wire"):
            a, b = (pt(xy) for xy in children(child(w, "pts"), "xy"))
            segments.append((a, b))
            join(("pt", sheet, a), ("pt", sheet, b))

        def on_wire(p, sheet=sheet, segments=segments):
            for a, b in segments:
                if min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]):
                    join(("pt", sheet, p), ("pt", sheet, a))

        for kind in ("label", "global_label"):
            for lbl in children(doc, kind):
                p = pt(child(lbl, "at"))
                on_wire(p)
                scope = ("label", sheet) if kind == "label" else ("global",)
                join(("pt", sheet, p), ("name", *scope, unquote(lbl[1])))
                names.setdefault(find(("pt", sheet, p)), set()).add(unquote(lbl[1]))
        for sym in children(doc, "symbol"):
            lib_id = unquote(child(sym, "lib_id")[1])
            if lib_id.startswith("power:") and lib_id != "power:PWR_FLAG":
                value = next(unquote(p[2]) for p in children(sym, "property") if unquote(p[1]) == "Value")
                p = pt(child(sym, "at"))  # power symbols have their pin at the origin
                on_wire(p)
                join(("pt", sheet, p), ("name", "global", value))
    for a in generated.anchors:
        p = (round(a.x, 3), round(a.y, 3))
        join(("pin", a.reference, a.pin), ("pt", a.sheet, p))
    groups: dict = {}
    for a in generated.anchors:
        groups.setdefault(find(("pin", a.reference, a.pin)), set()).add((a.reference, a.pin))
    labelled: dict = {}
    for root, nodes in groups.items():
        tags = {n for r, ns in names.items() if find(r) == root for n in ns}
        tags |= {k[-1] for k in parent if k[0] == "name" and find(k) == root}
        labelled[frozenset(nodes)] = tags
    return labelled


def ir_nets(circuit) -> dict[frozenset, str]:
    refs = {c.id: c.reference for c in circuit.components}
    return {frozenset((refs[n.component_id], n.pin) for n in net.nodes): net.name for net in circuit.nets}


@pytest.mark.parametrize("make", ["can_node", "ldo_supply", "two_page_led", "rotation_ring"])
def test_drawing_reproduces_exactly_the_ir_nets(library, make):
    from . import circuits

    circuit = getattr(circuits, make)()
    drawn = drawn_nets(generate_schematic(circuit, library))
    want = ir_nets(circuit)
    connected = {nodes: tags for nodes, tags in drawn.items() if len(nodes) > 1 or tags}
    assert set(connected) == set(want)
    for nodes, name in want.items():
        assert name in connected[nodes], f"net {name} lost its name"


def test_drawing_with_many_parts_keeps_nets_apart(library):
    from .circuits import many_resistors

    circuit = many_resistors(40)
    drawn = drawn_nets(generate_schematic(circuit, library))
    assert set(nodes for nodes in drawn if len(nodes) > 1) == set(ir_nets(circuit))


def test_rails_are_power_symbols_and_nearby_pins_are_wired(library):
    from .circuits import can_node

    g = generate_schematic(can_node(), library)
    doc = sexpr.parse(g.text)
    values = {next(unquote(p[2]) for p in children(s, "property") if unquote(p[1]) == "Value")
              for s in children(doc, "symbol") if unquote(child(s, "lib_id")[1]) in ("power:GND", "power:+3V3")}
    assert values == {"GND", "+3V3"}
    labels = [unquote(lbl[1]) for lbl in children(doc, "label")]
    assert "GND" not in labels and "+3V3" not in labels
    # Every signal net keeps exactly one name label per wired piece, and some pieces join several pins.
    assert sorted(set(labels)) == ["CANH", "CANL", "RXD", "TXD"]
    assert len(list(children(doc, "wire"))) > len(labels)


def test_connector_facing_right_is_mirrored_to_keep_pin_1_on_top(library):
    from .circuits import can_node

    g = generate_schematic(can_node(), library)
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    u1_d = pins[("U1", "1")]
    # J2 (UART) sits left of the transceiver with its pins facing it, pin 1 above pin 2.
    j2 = [s for s in children(sexpr.parse(g.text), "symbol")
          if any(unquote(p[1]) == "Reference" and unquote(p[2]) == "J2" for p in children(s, "property"))][0]
    assert child(j2, "mirror") == ["mirror", "y"]
    assert pins[("J2", "1")][0] < u1_d[0] and pins[("J2", "1")][1] < pins[("J2", "2")][1]


def test_signal_net_crossing_pages_uses_global_labels(library):
    from .circuits import two_page_led

    data = two_page_led().model_dump(mode="json")
    for net in data["nets"]:
        if net["name"] == "VIN":
            net["name"] = "LED_SUPPLY"  # not a rail name: drawn as a signal
    g = generate_schematic(CircuitIR.model_validate(data), library)
    root, sub = (sexpr.parse(p.text) for p in g.pages)
    assert [unquote(x[1]) for x in children(root, "global_label")] == ["LED_SUPPLY"]
    assert [unquote(x[1]) for x in children(sub, "global_label")] == ["LED_SUPPLY"]


def test_power_flag_where_a_series_part_feeds_a_rail(library):
    """+5V reaches the regulator only through R1 from the connector: nothing on the board drives it."""
    two = [{"number": "1"}, {"number": "2"}]

    def node(c, p):
        return {"component_id": c, "pin": p}

    circuit = CircuitIR.model_validate({
        "project_id": "fed", "components": [
            {"id": "j1", "reference": "J1", "value": "IN", "symbol_id": "Connector_Generic:Conn_01x02", "pins": two},
            {"id": "r1", "reference": "R1", "value": "0R", "symbol_id": "Device:R", "pins": two},
            {"id": "u1", "reference": "U1", "value": "AMS1117-3.3", "symbol_id": "Regulator_Linear:AMS1117-3.3",
             "pins": [{"number": "1"}, {"number": "2"}, {"number": "3"}]},
            {"id": "c1", "reference": "C1", "value": "10uF", "symbol_id": "Device:C", "pins": two}],
        "nets": [
            {"id": "a", "name": "+12V", "nodes": [node("j1", "1"), node("r1", "1")]},
            {"id": "b", "name": "+5V", "nodes": [node("r1", "2"), node("u1", "3")]},
            {"id": "c", "name": "+3V3", "nodes": [node("u1", "2"), node("c1", "1")]},
            {"id": "d", "name": "GND", "nodes": [node("j1", "2"), node("u1", "1"), node("c1", "2")]}]})
    g = generate_schematic(circuit, library)
    # +12V: power symbol only (a power input) fed by the connector; +5V: fed through R1; +3V3 is driven by U1.
    assert sorted(g.power_flags) == ["+12V", "+5V", "GND"]


def test_termination_resistor_bridges_the_lines_and_the_connector_lines_up(library):
    """CAN block: U1 CANH/CANL run straight on to the bus connector J1, and the 120 ohm R1 stands across
    both lines between them, so the whole block is wired with one name label per net."""
    from .circuits import can_node

    g = generate_schematic(can_node(), library)
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    canh, canl = pins[("U1", "7")], pins[("U1", "6")]
    assert pins[("J1", "1")][1] == canh[1] and pins[("J1", "2")][1] == canl[1]
    r_top, r_bottom = sorted((pins[("R1", "1")], pins[("R1", "2")]), key=lambda p: p[1])
    assert r_top[0] == r_bottom[0] and canh[0] < r_top[0] < pins[("J1", "1")][0]
    assert r_top[1] < min(canh[1], canl[1]) and r_bottom[1] > max(canh[1], canl[1])
    labels = [unquote(lbl[1]) for lbl in children(sexpr.parse(g.text), "label")]
    assert labels.count("CANH") == 1 and labels.count("CANL") == 1


def test_sample_board_draws_the_ir_nets_and_a_textbook_crystal(sample_board, library):
    """The crystal lies along the XTAL1/XTAL2 lines with a load capacitor hanging straight down from each end
    (one small block, not parts dropped one by one), and every wired piece has one name label."""
    g = generate_schematic(sample_board, library)
    drawn = drawn_nets(g)
    want = ir_nets(sample_board)
    connected = {nodes: tags for nodes, tags in drawn.items() if len(nodes) > 1 or tags}
    assert set(connected) == set(want)
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    y1, y2 = pins[("Y1", "1")], pins[("Y1", "2")]
    assert y1[1] == y2[1]  # lying flat
    for cap, end in (("C4", y1), ("C5", y2)):  # XTAL1's cap under the XTAL1 end, XTAL2's under the XTAL2 end
        top = pins[(cap, "1")]
        assert top[0] == end[0] and 0 < top[1] - end[1] <= 10.16, cap
    labels = [unquote(lbl[1]) for lbl in children(sexpr.parse(g.text), "label")]
    for net in ("XTAL1", "XTAL2", "STATUS", "STATUS_LED", "RESET", "AREF"):
        assert labels.count(net) == 1, net


def test_timer_board_keeps_blocks_together(timer_board, library):
    """No MCU: a 555 with its timing chain, a power input block and a status LED. The timing parts form the
    usual vertical chain (+5V - R2 - DISCH - R3 - TRIG/THR - C2 - GND), the CONT capacitor stands beside its pin
    with ground below it, and the power input J1 keeps its bulk capacitor and power LED next to it."""
    g = generate_schematic(timer_board, library)
    drawn = drawn_nets(g)
    connected = {nodes: tags for nodes, tags in drawn.items() if len(nodes) > 1 or tags}
    assert set(connected) == set(ir_nets(timer_board))
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    r2, r3, c2 = (pins[(r, "1")] for r in ("R2", "R3", "C2"))
    assert abs(r2[0] - r3[0]) <= 2.54 + 1e-6 and r3[0] == c2[0] and r2[1] < r3[1] < c2[1]
    assert pins[("C2", "1")][1] - pins[("R3", "2")][1] <= 5.08  # straight under R3
    assert pins[("C3", "2")][1] > pins[("C3", "1")][1]  # ground below the capacitor, not above it
    j1 = pins[("J1", "1")]
    for ref in ("C1", "R1", "D1"):
        x, y = pins[(ref, "1")]
        assert abs(x - j1[0]) <= 30.48 + 1e-6 and abs(y - j1[1]) <= 30.48 + 1e-6, ref
    labels = [unquote(lbl[1]) for lbl in children(sexpr.parse(g.text), "label")]
    for net in ("DISCH", "TRIG_THR", "CONT", "STATUS", "STATUS_LED", "PWR_LED"):
        assert labels.count(net) == 1, net


def test_timer_board_notes_sit_by_their_blocks(timer_board, library):
    """Each English note lands next to what it describes: block notes within a few grid steps of their parts,
    the STATUS note next to the STATUS label."""
    from kicad_adapter.drawing import note_rect

    g = generate_schematic(timer_board, library)
    doc = sexpr.parse(g.text)
    at = {unquote(t[1]): (float(child(t, "at")[1]), float(child(t, "at")[2])) for t in children(doc, "text")}
    assert set(at) == {n.text for n in timer_board.notes}
    pins = {}
    for a in g.anchors:
        pins.setdefault(a.reference, []).append((a.x, a.y))
    refs = {c.id: c.reference for c in timer_board.components}

    def gap(rect, points):
        xs, ys = [x for x, _ in points], [y for _, y in points]
        dx = max(min(xs) - rect[2], rect[0] - max(xs), 0)
        dy = max(min(ys) - rect[3], rect[1] - max(ys), 0)
        return max(dx, dy)

    for note in timer_board.notes:
        rect = note_rect(at[note.text], note.text)
        if note.components:
            points = [p for cid in note.components for p in pins[refs[cid]]]
        else:
            points = [(float(child(lbl, "at")[1]), float(child(lbl, "at")[2])) for lbl in children(doc, "label")
                      if unquote(lbl[1]) == note.net]
        assert gap(rect, points) <= 15.24, note.text


def test_long_value_slides_off_the_symbol_drawing(library):
    """An LED's value longer than the library's "LED" would run into the light arrows: it slides along the
    text, away from them. A resistor's value sits inside its box on purpose and stays put."""
    from kicad_adapter.drawing import _graphics, field_anchor, field_rect, overlaps

    led, res = library.get("Device:LED"), library.get("Device:R")
    value = next(f for f in led.fields if f.name == "Value")
    at = field_anchor(led, value, "GREEN")
    assert at[1] == -value.y and at[0] > value.x
    arrows = [g for g in _graphics(led) if g[2] < -1.27]  # the two light arrows, left of the diode bar
    assert arrows and not any(overlaps(field_rect(value, "GREEN", 0, at=at), g) for g in arrows)
    assert field_anchor(led, value, "RED") == (value.x, -value.y)
    r_value = next(f for f in res.fields if f.name == "Value")
    assert field_anchor(res, r_value, "10k") == (r_value.x, -r_value.y)


# --- multi-unit symbols and stacked pins (Phase 3) -----------------------------------------------

def test_multi_unit_symbol_is_drawn_unit_by_unit(library):
    from .circuits import opamp_mcu

    g = generate_schematic(opamp_mcu(), library)
    doc = sexpr.parse(g.text)
    units = sorted(int(child(s, "unit")[1]) for s in children(doc, "symbol")
                   if any(unquote(p[1]) == "Reference" and unquote(p[2]) == "U1" for p in children(s, "property")))
    assert units == [1, 2, 3]
    # Every unit instance lists all eight pins, with uuids unique in the file.
    lm358 = [s for s in children(doc, "symbol") if unquote(child(s, "lib_id")[1]) == "Amplifier_Operational:LM358"]
    assert all(len(list(children(s, "pin"))) == 8 for s in lm358)
    uuids = [unquote(child(p, "uuid")[1]) for s in lm358 for p in children(s, "pin")]
    assert len(uuids) == len(set(uuids)) == 24
    assert {a.pin for a in g.anchors if a.reference == "U1"} == {str(n) for n in range(1, 9)}


def test_stacked_pins_are_drawn_once_and_must_share_a_net(library):
    from .circuits import opamp_mcu

    circuit = opamp_mcu()
    g = generate_schematic(circuit, library)
    at = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    assert at[("U2", "3")] == at[("U2", "5")] == at[("U2", "21")] and at[("U2", "4")] == at[("U2", "6")]
    drawn = drawn_nets(g)
    assert set(nodes for nodes in drawn if len(nodes) > 1) == set(ir_nets(circuit))

    data = circuit.model_dump(mode="json")
    for net in data["nets"]:
        net["nodes"] = [n for n in net["nodes"] if (n["component_id"], n["pin"]) != ("u2", "21")]
    data["nets"].append({"id": "x", "name": "OTHER", "nodes": [{"component_id": "u2", "pin": "21"},
                                                              {"component_id": "r2", "pin": "2"}]})
    for net in data["nets"]:
        if net["name"] == "GND":
            net["nodes"] = [n for n in net["nodes"] if (n["component_id"], n["pin"]) != ("r2", "2")]
    with pytest.raises(SchematicGenerationError) as err:
        generate_schematic(CircuitIR.model_validate(data), library)
    assert "STACKED_PINS_DIFFER" in str(err.value)


def _wire_components(text: str) -> list[list[tuple[tuple[float, float], tuple[float, float]]]]:
    """Wire segments of a page grouped by what touches (end to end or end on segment)."""
    doc = sexpr.parse(text)
    segs = [tuple((round(float(xy[1]), 3), round(float(xy[2]), 3)) for xy in children(child(w, "pts"), "xy"))
            for w in children(doc, "wire")]

    def on(p, s):
        (ax, ay), (bx, by) = s
        return min(ax, bx) <= p[0] <= max(ax, bx) and min(ay, by) <= p[1] <= max(ay, by) and (
            ax == bx == p[0] or ay == by == p[1])

    groups: list[list] = []
    for s in segs:
        hit = [g for g in groups if any(on(e, t) or on(f, s) for t in g for e in s for f in t)]
        merged = [s] + [t for g in hit for t in g]
        groups = [g for g in groups if g not in hit] + [merged]
    return groups


def _wired(text: str, a: tuple[float, float], b: tuple[float, float]) -> bool:
    def on(p, s):
        (ax, ay), (bx, by) = s
        return min(ax, bx) <= p[0] <= max(ax, bx) and min(ay, by) <= p[1] <= max(ay, by)

    return any(any(on(a, s) for s in g) and any(on(b, s) for s in g) for g in _wire_components(text))


def test_pwr_flags_sit_on_the_power_symbols_of_the_input(timer_board, regulator_board, library):
    """A PWR_FLAG shares the connection point of its rail's power symbol (pins on one point join), next to the
    connector or series part bringing the power in - not on its own somewhere on the page."""
    for circuit, feeders in ((timer_board, {"J1"}), (regulator_board, {"J1", "D1"})):
        g = generate_schematic(circuit, library)
        doc = sexpr.parse(g.text)
        at = {}
        for s in children(doc, "symbol"):
            if child(s, "lib_id") is None:
                continue
            lib = unquote(child(s, "lib_id")[1])
            at.setdefault(lib, []).append((float(child(s, "at")[1]), float(child(s, "at")[2])))
        flags = at.pop("power:PWR_FLAG")
        power_points = {p for lib, pts in at.items() if lib.startswith("power:") for p in pts}
        anchors = [(a.x, a.y) for a in g.anchors if a.reference in feeders]
        assert flags and set(g.power_flags)
        for p in flags:
            assert p in power_points, p
            assert min(abs(p[0] - x) + abs(p[1] - y) for x, y in anchors) <= 20.32, (feeders, p)


def test_decoupling_capacitors_are_wired_to_the_supply_pins(sample_board, library):
    """The MCU's 100 nF capacitors stand beside its VCC / AVCC pins and are wired to them (one power symbol for
    the pin and its capacitor) instead of floating elsewhere with power symbols of their own."""
    g = generate_schematic(sample_board, library)
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    supply = [pins[("U1", "4")], pins[("U1", "18")]]
    for cap in ("C1", "C2"):
        assert any(_wired(g.text, pins[(cap, "1")], p) for p in supply), cap
        assert min(abs(pins[(cap, "1")][0] - x) + abs(pins[(cap, "1")][1] - y) for x, y in supply) <= 30.48


def test_long_notes_wrap_and_stay_by_their_block(timer_board, library):
    data = timer_board.model_dump(mode="json")
    long = "555 astable timer: about 1 Hz with R2 = 10k, R3 = 68k and C2 = 10uF, roughly 53% duty cycle"
    note = next(n for n in data["notes"] if n["components"] and "u-tmr" in n["components"])
    note["text"] = long
    g = generate_schematic(CircuitIR.model_validate(data), library)
    texts = {unquote(t[1]): (float(child(t, "at")[1]), float(child(t, "at")[2]))
             for t in children(sexpr.parse(g.text), "text")}
    wrapped = next(t for t in texts if t.replace("\n", " ") == long)
    assert "\n" in wrapped and max(len(line) for line in wrapped.split("\n")) <= 48
    x, y = texts[wrapped]
    refs = {c.id: c.reference for c in timer_board.components}
    near = [(a.x, a.y) for a in g.anchors if a.reference in {refs[c] for c in note["components"]}]
    assert min(abs(x - px) + abs(y - py) for px, py in near) <= 40.0  # beside the block, not above the page
    assert y > min(a.y for a in g.anchors) - 20.0


def test_connector_facing_up_keeps_pin_1_on_the_left():
    """A connector hanging under pins that point down faces up; mirrored if needed so pin 1 stays on the left."""
    from kicad_adapter.layout import PinGeom, plan_groups

    pins = {"u": [PinGeom("1", -2.54, 5.08, (0, 1), "output"), PinGeom("2", 2.54, 5.08, (0, 1), "output"),
                  PinGeom("3", 0.0, -5.08, (0, -1), "power_in")],
            "j": [PinGeom("1", -5.08, -1.27, (-1, 0), "passive"), PinGeom("2", -5.08, 1.27, (-1, 0), "passive")]}
    net_of = {("u", "1"): "A", ("u", "2"): "B", ("u", "3"): "+5V", ("j", "1"): "A", ("j", "2"): "B"}
    net_nodes = {"A": [("u", "1"), ("j", "1")], "B": [("u", "2"), ("j", "2")], "+5V": [("u", "3")]}

    def extent(cid, rotation, quiet, mirror=False, parts=False):
        return (-6.35, -6.35, 6.35, 6.35) if not parts else [(-6.35, -6.35, 6.35, 6.35)]

    groups = plan_groups(["u", "j"], pins, extent, net_of, net_nodes, {}, {}, {"+5V": (0, -1)},
                         {"u": "ic", "j": "connector"})
    g = next(g for g in groups if "j" in g.offsets)
    _, _, rotation = g.offsets["j"]
    from kicad_adapter.drawing import transform, transform_dir

    mirror = "j" in g.mirrored
    (x1, _), (x2, _) = (transform(p.x, p.y, rotation, mirror) for p in pins["j"])
    assert transform_dir((-1, 0), rotation, mirror) == (0, -1)  # facing up, toward the pins above
    assert x1 < x2


def test_supply_lies_along_its_pin_instead_of_crossing_the_ground_wire(led_circuit, library):
    """The LED example's connector is turned (pin 2 = GND above pin 1 = VIN): an upright VIN arrow would cut
    through the GND wire, so VIN lies along its pin while ground keeps pointing down."""
    g = generate_schematic(led_circuit, library)
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    j1 = pins[("J1", "1")]
    vin = [s for s in children(sexpr.parse(g.text), "symbol") if child(s, "lib_id") is not None
           and unquote(child(s, "lib_id")[1]).startswith("power:")
           and any(unquote(p[1]) == "Value" and unquote(p[2]) == "VIN" for p in children(s, "property"))]
    at = [(float(child(s, "at")[1]), float(child(s, "at")[2]), int(float(child(s, "at")[3]))) for s in vin]
    near = min(at, key=lambda a: abs(a[0] - j1[0]) + abs(a[1] - j1[1]))
    assert near[1] == j1[1] and near[2] in (90, 270), near
