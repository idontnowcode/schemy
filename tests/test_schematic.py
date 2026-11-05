import json
import os

import pytest

from circuit_core import CircuitIR
from kicad_adapter import SCHEMATIC_FILE_VERSION, SchematicGenerationError, generate_schematic, sexpr
from kicad_adapter.schematic import project_file
from kicad_adapter.sexpr import child, children, head, unquote

from .circuits import rotation_ring
from .conftest import EXAMPLE_DIR, EXAMPLE_NAME, SAMPLE_DIR, SAMPLE_NAME

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
    generate_schematic(circuit, library, project_name=name).write(workdir)
    sch, pro = (f"{name}{suffix}" for suffix in (".kicad_sch", ".kicad_pro"))
    if os.environ.get("AICS_UPDATE_GOLDEN"):
        for file in (sch, pro):
            (folder / file).write_text((workdir / file).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
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
    """The crystal stands across XTAL1/XTAL2 with C5 straight below it (a capacitor to ground hangs under the
    end of a bridge that points to ground), and short wires are drawn even where another pin's label space
    was kept free at first (R2 to D2): every wired piece has one name label."""
    g = generate_schematic(sample_board, library)
    drawn = drawn_nets(g)
    want = ir_nets(sample_board)
    connected = {nodes: tags for nodes, tags in drawn.items() if len(nodes) > 1 or tags}
    assert set(connected) == set(want)
    pins = {(a.reference, a.pin): (a.x, a.y) for a in g.anchors}
    y2, c5 = pins[("Y1", "2")], pins[("C5", "1")]
    assert c5[0] == y2[0] and c5[1] > y2[1]
    labels = [unquote(lbl[1]) for lbl in children(sexpr.parse(g.text), "label")]
    for net in ("XTAL1", "XTAL2", "STATUS", "STATUS_LED", "RESET", "AREF"):
        assert labels.count(net) == 1, net


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
