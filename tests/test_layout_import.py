"""Keeping a layout arranged in KiCad: reading positions back, the generator honouring them, the proposal flow."""

import pytest

from circuit_core import CircuitIR
from kicad_adapter import generate_schematic, run_checks, sexpr
from kicad_adapter.layout_import import read_layout
from kicad_adapter.sexpr import child, children, unquote

from .backend_helpers import make_client, new_project, run_export, save_ir
from .test_schematic import drawn_nets, ir_nets


def move_in_file(text: str, comp_id: str, x: float, y: float, rotation: int, mirror: str | None) -> str:
    """What KiCad does when someone moves (and turns / mirrors) a symbol and saves."""
    doc = sexpr.parse(text)
    for sym in children(doc, "symbol"):
        if any(unquote(p[1]) == "AICS_ID" and unquote(p[2]) == comp_id for p in children(sym, "property")):
            at = child(sym, "at")
            at[1:] = [str(x), str(y), str(rotation)]
            sym[:] = [n for n in sym if not (isinstance(n, list) and n and n[0] == "mirror")]
            if mirror:
                sym.insert(sym.index(at) + 1, ["mirror", mirror])
    return sexpr.dumps(doc)


def test_positions_are_read_by_aics_id(led_circuit, library):
    text = generate_schematic(led_circuit, library).text
    layout = read_layout([text])
    assert set(layout.placements) == {"conn-001", "res-001", "led-001"}
    assert layout.unknown == [] and layout.extra_units == []
    moved = move_in_file(text, "res-001", 101.6, 50.8, 90, "x")
    p = read_layout([moved]).placements["res-001"]
    # KiCad's "mirror x" is "mirror y" turned half way round.
    assert (p.x, p.y, p.rotation, p.mirror) == (101.6, 50.8, 270, True)


def test_fixed_mirrored_and_turned_parts_round_trip(led_circuit, library):
    data = led_circuit.model_dump(mode="json")
    data["layout"] = {"conn-001": {"x": 50.8, "y": 50.8, "rotation": 0, "mirror": True},
                      "res-001": {"x": 76.2, "y": 50.8, "rotation": 90, "mirror": True},
                      "led-001": {"x": 101.6, "y": 63.5, "rotation": 270, "mirror": False}}
    circuit = CircuitIR.model_validate(data)
    g = generate_schematic(circuit, library)
    assert read_layout([g.text]).placements == {k: v for k, v in circuit.layout.items()}
    drawn = drawn_nets(g)
    connected = {nodes: tags for nodes, tags in drawn.items() if len(nodes) > 1 or tags}
    assert set(connected) == set(ir_nets(circuit))


def test_import_from_kicad_is_a_proposal_and_regenerates(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    url = f"/api/projects/{pid}/boards/a"
    assert client.post(f"{url}/layout/import-kicad").json()["error_code"] == "NO_OUTPUTS"
    run_export(client, pid)
    sch = tmp_path / "projects" / pid / "boards" / "a" / "outputs" / "led-demo-a.kicad_sch"
    sch.write_text(move_in_file(sch.read_text(encoding="utf-8"), "res-001", 127.0, 76.2, 90, None), encoding="utf-8")
    assert client.get(f"{url}/outputs").json()["externally_modified"] == ["led-demo-a.kicad_sch"]

    r = client.post(f"{url}/layout/import-kicad")
    assert r.status_code == 201, r.text
    body = r.json()
    client.app.state.jobs.wait(body["job"]["id"], timeout=300)
    p = client.get(f"{url}/proposals/{body['proposal']['id']}").json()
    assert p["source"] == "kicad_layout" and p["status"] == "ready"
    assert p["candidate"]["layout"]["res-001"] == {"x": 127.0, "y": 76.2, "rotation": 90, "mirror": False}
    assert set(p["candidate"]["layout"]) == {"conn-001", "res-001", "led-001"}
    assert p["diff"]["moved"] == ["R1"] and p["diff"]["connections"] == []  # only what was moved in KiCad

    # Approving with generation replaces the edited file (its edits are in the circuit now).
    a = client.post(f"{url}/proposals/{p['id']}/approve", json={"accept_failed_checks": True, "generate": True}).json()
    job = client.app.state.jobs.wait(a["job"]["id"], timeout=300)
    assert job.status == "succeeded", job
    status = client.get(f"{url}/outputs").json()
    assert status["externally_modified"] == [] and status["stale"] is False
    assert read_layout([sch.read_text(encoding="utf-8")]).placements["res-001"].x == 127.0
    assert client.post(f"{url}/layout/import-kicad").json()["error_code"] == "NO_LAYOUT_CHANGES"

    # And back to automatic placement.
    r = client.post(f"{url}/layout/reset")
    assert r.status_code == 201
    client.app.state.jobs.wait(r.json()["job"]["id"], timeout=300)
    p2 = client.get(f"{url}/proposals/{r.json()['proposal']['id']}").json()
    assert p2["candidate"]["layout"] == {} and p2["diff"]["moved"] == ["D1", "J1", "R1"]


def test_parts_added_in_kicad_are_reported_not_imported(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    run_export(client, pid)
    sch = tmp_path / "projects" / pid / "boards" / "a" / "outputs" / "led-demo-a.kicad_sch"
    doc = sexpr.parse(sch.read_text(encoding="utf-8"))
    extra = next(s for s in children(doc, "symbol") if unquote(child(s, "lib_id")[1]) == "Device:R")
    copy = sexpr.parse(sexpr.dumps(extra))
    copy[:] = [n for n in copy if not (isinstance(n, list) and n and n[0] == "property" and unquote(n[1]) == "AICS_ID")]
    for prop in children(copy, "property"):
        if unquote(prop[1]) == "Reference":
            prop[2] = '"R9"'
    doc.append(copy)
    text = move_in_file(sexpr.dumps(doc), "res-001", 127.0, 76.2, 0, None)
    sch.write_text(text, encoding="utf-8")
    body = client.post(f"/api/projects/{pid}/boards/a/layout/import-kicad").json()
    assert body["unknown"] == ["R9"]
    assert "R9" in body["proposal"]["rationale"]


@pytest.mark.kicad
def test_turned_and_mirrored_fixed_parts_connect_in_kicad(led_circuit, library, kicad_cli, workdir):
    """The generator's mirror-then-rotate matches KiCad: wires end on the pins of turned, mirrored parts."""
    data = led_circuit.model_dump(mode="json")
    data["layout"] = {"conn-001": {"x": 50.8, "y": 50.8, "rotation": 180, "mirror": True},
                      "res-001": {"x": 76.2, "y": 50.8, "rotation": 90, "mirror": True},
                      "led-001": {"x": 101.6, "y": 63.5, "rotation": 270, "mirror": True}}
    circuit = CircuitIR.model_validate(data)
    g = generate_schematic(circuit, library)
    sch = g.write(workdir)
    report = run_checks(circuit, g, sch, kicad_cli)
    assert report.erc.errors == [], report.erc.summary()
    assert report.connectivity.ok, report.connectivity.describe()
