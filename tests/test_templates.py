"""Reviewed circuit templates: placing them, their evidence, and (with KiCad) a clean ERC and netlist."""

import pytest

from circuit_core import CircuitIR, validate_circuit
from circuit_core.templates import TEMPLATES, TemplateError, as_circuit, instantiate
from kicad_adapter import generate_schematic
from kicad_adapter.netlist import compare_with_ir, parse_kicad_netlist


@pytest.fixture
def pins_of(library):
    def lookup(symbol_id):
        try:
            return [(p.number, p.name) for p in library.get(symbol_id).pins]
        except Exception:  # noqa: BLE001
            return None
    return lookup


def harness(template_id: str, pins_of) -> CircuitIR:
    """The template plus connectors on its ports, so ERC sees where power and signals come from."""
    t = TEMPLATES[template_id]
    inst = instantiate(t, None, None, pins_of)
    circuit = as_circuit(inst, "tpl-" + template_id)
    data = circuit.model_dump(mode="json")
    params = inst.params
    supply = params.get("vin") or params["vcc"]
    others = [params[p.name] for p in t.params if p.kind == "net" and p.name not in ("vin", "vcc", "gnd")]
    groups = [[supply, params["gnd"]], others if len(others) > 1 else [*others, params["gnd"]]]
    for n, group in enumerate(groups, start=1):
        cid = f"j-test-{n}"
        data["components"].append({"id": cid, "reference": f"J{90 + n}", "value": "TEST",
                                   "symbol_id": f"Connector_Generic:Conn_01x0{len(group)}",
                                   "pins": [{"number": str(i)} for i in range(1, len(group) + 1)]})
        for i, net_name in enumerate(group, start=1):
            net = next(x for x in data["nets"] if x["name"] == net_name)
            net["nodes"].append({"component_id": cid, "pin": str(i)})
    return CircuitIR.model_validate(data)


def test_every_template_has_evidence_and_a_checklist():
    assert set(TEMPLATES) == {"ldo-ams1117-3v3", "buck-lm2596-5v", "can-sn65hvd230", "mcu-atmega328p-min"}
    for t in TEMPLATES.values():
        info = t.to_dict()
        assert info["basis"] and info["checklist"] and info["parts"]
        assert all(b["section"] and b["document"] for b in info["basis"])
        assert all(b["page"] is None for b in info["basis"]), "pages are recorded by the reviewer, never guessed"
        assert all(c["text"] and c["basis"] for c in info["checklist"])


@pytest.mark.parametrize("template_id", sorted(TEMPLATES))
def test_template_alone_is_a_valid_circuit_that_generates(template_id, library, pins_of):
    circuit = harness(template_id, pins_of)
    report = validate_circuit(circuit)
    assert report.errors == [], report.errors
    assert not [w for w in report.warnings if w.error_code == "UNCONNECTED_PIN"]
    origins = {c.origin for c in circuit.components if not c.id.startswith("j-test")}
    assert origins == {TEMPLATES[template_id].origin}
    generate_schematic(circuit, library)


def test_placing_a_template_next_to_existing_parts(pins_of, led_circuit):
    inst = instantiate(TEMPLATES["can-sn65hvd230"], {"vcc": "VIN", "termination": "no"}, led_circuit, pins_of)
    refs = {c.reference for c in inst.components}
    assert refs.isdisjoint({c.reference for c in led_circuit.components})  # R1 exists: none clash
    assert "R2" not in refs and len(inst.components) == 2  # no termination resistor
    assert "VIN" in inst.nets and "GND" in inst.nets  # ports join the board's nets by name
    # An internal net whose name is taken on the board is renamed.
    buck = instantiate(TEMPLATES["buck-lm2596-5v"], None, as_circuit(
        instantiate(TEMPLATES["buck-lm2596-5v"], None, None, pins_of), "x"), pins_of)
    assert "SW_2" in buck.nets and "SW" not in buck.nets
    assert {c.id for c in buck.components} == {"buck-u-2", "buck-cin-2", "buck-d-2", "buck-l-2", "buck-cout-2"}


def test_template_parameters_are_checked(pins_of):
    with pytest.raises(TemplateError):
        instantiate(TEMPLATES["ldo-ams1117-3v3"], {"vin": "bad name"}, None, pins_of)
    with pytest.raises(TemplateError):
        instantiate(TEMPLATES["ldo-ams1117-3v3"], {"vin": "+5V", "vout": "+5V"}, None, pins_of)
    with pytest.raises(TemplateError):
        instantiate(TEMPLATES["ldo-ams1117-3v3"], {"nope": "1"}, None, pins_of)
    with pytest.raises(TemplateError):
        instantiate(TEMPLATES["can-sn65hvd230"], {"termination": "maybe"}, None, pins_of)


@pytest.mark.kicad
@pytest.mark.parametrize("template_id", sorted(TEMPLATES))
def test_templates_pass_kicad_erc_and_netlist(template_id, library, pins_of, kicad_cli, workdir):
    circuit = harness(template_id, pins_of)
    generated = generate_schematic(circuit, library)
    sch = generated.write(workdir / template_id)
    erc = kicad_cli.erc(sch, workdir / template_id / "erc.json")
    assert erc.violations == (), erc.summary()
    diff = compare_with_ir(circuit, parse_kicad_netlist(kicad_cli.export_netlist(sch, workdir / template_id / "x.net")))
    assert diff.ok, diff.describe()


# --- backend: place a template as a proposal ---------------------------------------------------------

def test_template_api_places_a_template_as_a_proposal(tmp_path):
    from .backend_helpers import make_client, new_project, save_ir

    client = make_client(tmp_path)
    listed = client.get("/api/templates").json()["templates"]
    assert {t["id"] for t in listed} == set(TEMPLATES)
    pid = new_project(client)

    # Empty board: the template becomes the first circuit.
    r = client.post(f"/api/projects/{pid}/boards/a/templates/ldo-ams1117-3v3", json={"params": {"vin": "+5V"}})
    assert r.status_code == 201, r.text
    p = r.json()["proposal"]
    assert p["source"] == "template" and p["template"]["ref"] == "ldo-ams1117-3v3@1"
    assert p["diff"]["is_new_circuit"] is True
    assert {c["origin"] for c in p["diff"]["components_added"]} == {"template:ldo-ams1117-3v3@1"}
    assert client.get(f"/api/projects/{pid}/boards/a/circuit").json()["circuit"] is None  # nothing applied yet

    # Board with parts: the CAN template joins VIN and GND by name, refs do not clash.
    save_ir(client, pid)
    r = client.post(f"/api/projects/{pid}/boards/a/templates/can-sn65hvd230",
                    json={"params": {"vcc": "VIN", "termination": "no"}})
    p = r.json()["proposal"]
    ops = [o["op"] for o in p["ops"]]
    assert ops.count("add_component") == 2 and "replace_circuit" not in ops
    assert {c["reference"] for c in p["diff"]["components_added"]} == {"U1", "C1"}
    joined = {c["after"] for c in p["diff"]["connections"]}
    assert {"VIN", "GND", "TXD", "RXD", "CANH", "CANL"} <= joined

    bad = client.post(f"/api/projects/{pid}/boards/a/templates/can-sn65hvd230", json={"params": {"vcc": "a b"}})
    assert bad.status_code == 422 and bad.json()["error_code"] == "TEMPLATE_INVALID"
    assert client.post(f"/api/projects/{pid}/boards/a/templates/nope", json={}).status_code == 404
