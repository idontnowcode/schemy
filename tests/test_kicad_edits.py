"""Changes made in KiCad (connections, values, parts) read back as patch operations - external edits, step 2."""

import pytest

from circuit_core.diff import diff_circuits
from circuit_core.patch import apply_patch
from kicad_adapter.edit_import import edits_from_kicad

from .backend_helpers import make_client, new_project, run_export, save_ir

PARTS = {"J1": ("Conn_01x02", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", "Connector_Generic",
                "Conn_01x02", "conn-001"),
         "R1": ("330", "Resistor_SMD:R_0603_1608Metric", "Device", "R", "res-001"),
         "D1": ("LED", "LED_SMD:LED_0603_1608Metric", "Device", "LED", "led-001")}
NETS = {"/VIN": [("J1", "1"), ("R1", "1")], "/LED_A": [("R1", "2"), ("D1", "2")], "GND": [("D1", "1"), ("J1", "2")]}


def netlist(parts=None, nets=None) -> str:
    """A KiCad netlist (kicadsexpr) in the shape kicad-cli writes, with power symbols among the nodes."""
    parts = PARTS if parts is None else parts
    nets = NETS if nets is None else nets
    comps = []
    for ref, (value, fp, lib, part, aics) in parts.items():
        fields = f'(field (name "AICS_ID") "{aics}")' if aics else ""
        comps.append(f'(comp (ref "{ref}") (value "{value}") (footprint "{fp}") (fields {fields} '
                     f'(field (name "Footprint") "{fp}")) (libsource (lib "{lib}") (part "{part}")))')
    comps.append('(comp (ref "#FLG01") (value "PWR_FLAG") (libsource (lib "power") (part "PWR_FLAG")))')
    body = []
    for i, (name, nodes) in enumerate(nets.items(), start=1):
        ns = " ".join(f'(node (ref "{r}") (pin "{p}"))' for r, p in nodes)
        body.append(f'(net (code "{i}") (name "{name}") {ns})')
    return f'(export (version "E") (components {" ".join(comps)}) (nets {" ".join(body)}))'


def ops_of(edits):
    return [o.model_dump(exclude_none=True) for o in edits.ops]


def test_unchanged_schematic_gives_nothing(led_circuit, library):
    assert edits_from_kicad(led_circuit, netlist(), library).ops == []


def test_values_footprints_and_removed_parts(led_circuit, library):
    parts = {**PARTS, "R1": ("1k", "Resistor_SMD:R_0805_2012Metric", "Device", "R", "res-001")}
    del parts["D1"]
    nets = {"/VIN": NETS["/VIN"], "/LED_A": [("R1", "2")], "GND": [("J1", "2")]}
    edits = edits_from_kicad(led_circuit, netlist(parts, nets), library)
    after = apply_patch(led_circuit, edits.ops)
    assert {c.reference for c in after.components} == {"J1", "R1"}
    r1 = next(c for c in after.components if c.id == "res-001")
    assert (r1.value, r1.footprint_id) == ("1k", "Resistor_SMD:R_0805_2012Metric")
    assert edits.counts["removed"] == 1 and edits.counts["properties"] == 2
    assert "KiCad에서 지운 부품: D1." in edits.notes


def test_rewired_pins_new_nets_and_renames(led_circuit, library):
    """D1's cathode moves from GND to a new unnamed net with an added resistor; VIN's label is renamed +VIN_RAW
    (same pins) - a rename, not a new net."""
    parts = {**PARTS, "R2": ("100", "Resistor_SMD:R_0603_1608Metric", "Device", "R", None)}
    nets = {"/VIN_RAW": NETS["/VIN"], "/LED_A": NETS["/LED_A"], "GND": [("J1", "2"), ("R2", "2")],
            "Net-(D1-K)": [("D1", "1"), ("R2", "1")]}
    edits = edits_from_kicad(led_circuit, netlist(parts, nets), library)
    kinds = [o["op"] for o in ops_of(edits)]
    assert kinds[:2] == ["add_component", "rename_net"] and "disconnect" in kinds and "connect" in kinds
    after = apply_patch(led_circuit, edits.ops)
    by = {n.name: {(c, p) for c, p in ((x.component_id, x.pin) for x in n.nodes)} for n in after.nets}
    assert by["VIN_RAW"] == {("conn-001", "1"), ("res-001", "1")} and "VIN" not in by
    assert by["GND"] == {("conn-001", "2"), ("kicad-r2", "2")}
    new = next(name for name, nodes in by.items() if ("led-001", "1") in nodes)
    assert new not in ("GND", "VIN_RAW", "LED_A") and by[new] == {("led-001", "1"), ("kicad-r2", "1")}
    r2 = next(c for c in after.components if c.id == "kicad-r2")
    assert r2.symbol_id == "Device:R" and [p.number for p in r2.pins] == ["1", "2"]
    d = diff_circuits(led_circuit, after)
    assert d["summary"]["components_added"] == 1 and d["summary"]["connections_changed"] >= 3


def test_auto_named_net_keeps_the_ir_name_it_overlaps(led_circuit, library):
    """The LED_A label was deleted in KiCad but the wire stays: KiCad calls it Net-(R1-Pad2); nothing changes."""
    nets = {**{k: v for k, v in NETS.items() if k != "/LED_A"}, "Net-(R1-Pad2)": NETS["/LED_A"]}
    assert edits_from_kicad(led_circuit, netlist(nets=nets), library).ops == []


def test_parts_that_cannot_be_imported_are_reported(led_circuit, library):
    parts = {**PARTS, "U?": ("X", "", "Device", "R", None), "Q1": ("BC547", "", "Nope", "Q", None),
             "R7": ("1k", "", "Device", "R", "res-001")}
    edits = edits_from_kicad(led_circuit, netlist(parts), library)
    text = " ".join(edits.skipped)
    assert "U?" in text and "Q1" in text and "R1 → R7" in text
    assert all(o.op != "add_component" for o in edits.ops)


# --- backend --------------------------------------------------------------------------------------------

class FakeCli:
    """Stands in for kicad-cli: returns a prepared netlist for the edited schematic."""

    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def export_netlist(self, schematic, output):
        self.calls.append(schematic.name)
        output.write_text(self.text, encoding="utf-8")
        return self.text

    def describe(self):
        return "fake"

    def __getattr__(self, name):  # ERC, SVG, version: as if KiCad failed (the proposal check copes with that)
        from kicad_adapter.cli import KiCadCliError

        def fail(*args, **kwargs):
            raise KiCadCliError(f"fake kicad-cli has no {name}")
        return fail


def test_import_endpoint_makes_a_proposal(tmp_path):
    edited = netlist({**PARTS, "R1": ("1k", PARTS["R1"][1], "Device", "R", "res-001")})
    fake = {"cli": None}
    client = make_client(tmp_path, kicad_factory=lambda: fake["cli"])
    pid = new_project(client)
    save_ir(client, pid)
    url = f"/api/projects/{pid}/boards/a/kicad/import-edits"
    assert client.post(url).json()["error_code"] == "NO_OUTPUTS"
    run_export(client, pid)
    assert client.post(url).json()["error_code"] == "KICAD_UNAVAILABLE"
    fake["cli"] = FakeCli(netlist())
    assert client.post(url).json()["error_code"] == "NO_KICAD_CHANGES"
    fake["cli"] = FakeCli(edited)
    r = client.post(url)
    assert r.status_code == 201, r.text
    body = r.json()
    assert fake["cli"].calls == ["led-demo-a.kicad_sch"]
    p = body["proposal"]
    assert p["source"] == "kicad_edit" and "R1 값 330 → 1k" in p["rationale"]
    client.app.state.jobs.wait(body["job"]["id"], timeout=300)
    p = client.get(f"/api/projects/{pid}/boards/a/proposals/{p['id']}").json()
    assert p["diff"]["properties_changed"][0] | {} == {"id": "res-001", "reference": "R1", "field": "value",
                                                       "before": "330", "after": "1k"}


@pytest.mark.kicad
def test_value_change_and_deleted_part_in_a_real_kicad_file(tmp_path, kicad_cli):
    """Edit the generated schematic as KiCad would save it (R1's value, D1 deleted) and read it back with
    kicad-cli."""
    from kicad_adapter import sexpr
    from kicad_adapter.sexpr import child, children, unquote

    client = make_client(tmp_path, kicad_factory=lambda: kicad_cli)
    pid = new_project(client)
    save_ir(client, pid)
    run_export(client, pid)
    sch = tmp_path / "projects" / pid / "boards" / "a" / "outputs" / "led-demo-a.kicad_sch"
    doc = sexpr.parse(sch.read_text(encoding="utf-8"))

    def aics(s):
        return next((unquote(p[2]) for p in children(s, "property") if unquote(p[1]) == "AICS_ID"), None)

    for s in children(doc, "symbol"):
        if aics(s) == "res-001":
            for prop in children(s, "property"):
                if unquote(prop[1]) == "Value":
                    prop[2] = '"1k"'
    doc[:] = [n for n in doc if not (isinstance(n, list) and n and n[0] == "symbol" and child(n, "lib_id") is not None
                                     and aics(n) == "led-001")]
    sch.write_text(sexpr.dumps(doc), encoding="utf-8")
    r = client.post(f"/api/projects/{pid}/boards/a/kicad/import-edits")
    assert r.status_code == 201, r.text
    body = r.json()
    client.app.state.jobs.wait(body["job"]["id"], timeout=300)
    p = client.get(f"/api/projects/{pid}/boards/a/proposals/{body['proposal']['id']}").json()
    assert [c["reference"] for c in p["diff"]["components_removed"]] == ["D1"]
    assert any(c["field"] == "value" and c["after"] == "1k" for c in p["diff"]["properties_changed"])
