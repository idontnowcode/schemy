"""End-to-end checks with a real kicad-cli. Skipped (not passed) when KiCad is absent."""

import shutil

import pytest

from circuit_core import CircuitIR
from kicad_adapter import compare_with_ir, generate_schematic, parse_kicad_netlist, run_checks

from .circuits import rotation_ring

pytestmark = pytest.mark.kicad


def test_kicad_version_is_10(kicad_cli):
    assert kicad_cli.version().startswith("10.")


def test_led_circuit_end_to_end(led_circuit, library, kicad_cli, workdir):
    generated = generate_schematic(led_circuit, library)
    sch = generated.write(workdir)
    report = run_checks(led_circuit, generated, sch, kicad_cli)

    assert report.erc.errors == [], report.erc.summary()
    assert report.erc.warnings == [], report.erc.summary()
    assert report.connectivity.ok, report.connectivity.describe()
    assert report.automated_checks_passed
    assert report.status_label() == "Automated checks passed"

    svg = report.svg.read_text(encoding="utf-8")
    assert svg.lstrip().startswith("<?xml") and "<svg" in svg
    assert (workdir / "check_report.json").is_file()


def test_kicad_round_trip_preserves_file(led_circuit, library, kicad_cli, workdir):
    """KiCad loads the file and re-saves it: only the generator lines may change."""
    generated = generate_schematic(led_circuit, library)
    sch = generated.write(workdir)
    kicad_cli.upgrade(sch)
    resaved = sch.read_text(encoding="utf-8").splitlines()
    ours = generated.text.splitlines()
    assert len(ours) == len(resaved)
    differing = [(a, b) for a, b in zip(ours, resaved, strict=True) if a != b]
    assert differing == [
        ('\t(generator "ai_circuit_studio")', '\t(generator "eeschema")'),
        ('\t(generator_version "0.1")', '\t(generator_version "10.0")'),
    ]


def test_every_rotation_connects(library, kicad_cli, workdir):
    circuit = rotation_ring()
    sch = generate_schematic(circuit, library).write(workdir)
    netlist = parse_kicad_netlist(kicad_cli.export_netlist(sch, workdir / "ring.net"))
    diff = compare_with_ir(circuit, netlist)
    assert diff.ok, diff.describe()
    assert netlist.unconnected == frozenset()
    erc = kicad_cli.erc(sch, workdir / "erc.json")
    assert erc.errors == [], erc.summary()


def test_erc_reports_real_problems(led_circuit_data, library, kicad_cli, workdir):
    """Guard against a vacuous pipeline: an open LED cathode must fail ERC."""
    led_circuit_data["nets"][2]["nodes"] = [
        {"component_id": "conn-001", "pin": "2"},
        {"component_id": "res-001", "pin": "2"},
    ]
    led_circuit_data["nets"][1]["nodes"] = [{"component_id": "led-001", "pin": "2"}]
    circuit = CircuitIR.model_validate(led_circuit_data)
    generated = generate_schematic(circuit, library)
    report = run_checks(circuit, generated, generated.write(workdir), kicad_cli)
    assert not report.automated_checks_passed
    types = {v.type for v in report.erc.violations}
    assert "pin_not_connected" in types, report.erc.summary()



def test_example_project_opens_cleanly(led_circuit, kicad_cli, workdir):
    """The committed example, opened with its .kicad_pro, has a clean ERC and the IR's netlist."""
    from .conftest import EXAMPLE_DIR, EXAMPLE_NAME

    for f in EXAMPLE_DIR.iterdir():
        if f.suffix in (".kicad_sch", ".kicad_pro"):
            shutil.copy(f, workdir / f.name)
    sch = workdir / f"{EXAMPLE_NAME}.kicad_sch"
    erc = kicad_cli.erc(sch, workdir / "erc.json")
    assert erc.violations == (), erc.summary()
    netlist = parse_kicad_netlist(kicad_cli.export_netlist(sch, workdir / "example.net"))
    diff = compare_with_ir(led_circuit, netlist)
    assert diff.ok, diff.describe()
    assert erc.raw["source"] == sch.name
