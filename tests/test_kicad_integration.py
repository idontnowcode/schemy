"""End-to-end checks with a real kicad-cli. Skipped (not passed) when KiCad is absent."""

import json
import os
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


def test_sample_board_end_to_end(sample_board, library, kicad_cli, workdir):
    """examples/atmega328p-led-board: ERC clean and KiCad's netlist equals the IR; KiCad re-saves it unchanged."""
    generated = generate_schematic(sample_board, library)
    sch = generated.write(workdir)
    report = run_checks(sample_board, generated, sch, kicad_cli)
    assert report.erc.errors == [], report.erc.summary()
    assert report.erc.warnings == [], report.erc.summary()
    assert report.connectivity.ok, report.connectivity.describe()
    kicad_cli.upgrade(sch)
    resaved = sch.read_text(encoding="utf-8").splitlines()
    differing = [(a, b) for a, b in zip(generated.text.splitlines(), resaved, strict=True) if a != b]
    assert len(differing) == 2  # the generator name and version lines


def test_timer_board_end_to_end(timer_board, library, kicad_cli, workdir):
    """examples/ne555-led-board with its English notes: ERC clean, netlist = IR, KiCad re-saves it unchanged."""
    generated = generate_schematic(timer_board, library)
    sch = generated.write(workdir)
    report = run_checks(timer_board, generated, sch, kicad_cli)
    assert report.erc.errors == [], report.erc.summary()
    assert report.erc.warnings == [], report.erc.summary()
    assert report.connectivity.ok, report.connectivity.describe()
    kicad_cli.upgrade(sch)
    resaved = sch.read_text(encoding="utf-8").splitlines()
    differing = [(a, b) for a, b in zip(generated.text.splitlines(), resaved, strict=True) if a != b]
    assert len(differing) == 2  # the generator name and version lines


def test_regulator_board_end_to_end(regulator_board, library, kicad_cli, workdir):
    """examples/lm7805-24v-to-5v: wired rails with one power symbol per piece pass ERC and match the IR."""
    generated = generate_schematic(regulator_board, library)
    sch = generated.write(workdir)
    report = run_checks(regulator_board, generated, sch, kicad_cli)
    assert report.erc.errors == [], report.erc.summary()
    assert report.erc.warnings == [], report.erc.summary()
    assert report.connectivity.ok, report.connectivity.describe()
    kicad_cli.upgrade(sch)
    resaved = sch.read_text(encoding="utf-8").splitlines()
    differing = [(a, b) for a, b in zip(generated.text.splitlines(), resaved, strict=True) if a != b]
    assert len(differing) == 2


def test_motor_board_end_to_end(motor_board, library, kicad_cli, workdir):
    """examples/dc-motor-drv8871: the DRV8871 driver with its protected power input passes ERC and matches the IR."""
    generated = generate_schematic(motor_board, library)
    sch = generated.write(workdir)
    report = run_checks(motor_board, generated, sch, kicad_cli)
    assert report.erc.errors == [], report.erc.summary()
    assert report.erc.warnings == [], report.erc.summary()
    assert report.connectivity.ok, report.connectivity.describe()


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


def test_two_pages_with_kicad(library, kicad_cli, workdir):
    """Page split: clean ERC, the IR's netlist (incl. a local net on page 2), lossless round trip, one SVG per page."""
    from .circuits import two_page_led

    circuit = two_page_led(("전원 입력", "LED 출력 (부하)"))
    generated = generate_schematic(circuit, library)
    sch = generated.write(workdir)
    erc = kicad_cli.erc(sch, workdir / "erc.json")
    assert erc.violations == (), erc.summary()
    assert [s["path"] for s in erc.raw["sheets"]] == ["/", "/LED 출력 (부하)/"]
    netlist = parse_kicad_netlist(kicad_cli.export_netlist(sch, workdir / "two.net"))
    diff = compare_with_ir(circuit, netlist)
    assert diff.ok, diff.describe()

    for page in generated.pages:  # each file survives KiCad's own load/save
        kicad_cli.upgrade(workdir / page.filename)
        resaved = (workdir / page.filename).read_text(encoding="utf-8").splitlines()
        ours = page.text.splitlines()
        assert len(resaved) == len(ours)
        assert [(a, b) for a, b in zip(ours, resaved, strict=True) if a != b] == [
            ('\t(generator "ai_circuit_studio")', '\t(generator "eeschema")'),
            ('\t(generator_version "0.1")', '\t(generator_version "10.0")'),
        ]

    kicad_cli.export_svg(sch, workdir)
    assert sorted(p.name for p in workdir.glob("*.svg")) == ["schematic-LED 출력 (부하).svg", "schematic.svg"]


def test_ic_circuits_pass_erc_with_no_connect_and_power_flags(library, kicad_cli, workdir):
    """A transceiver with an unused pin, a regulator built from a derived symbol, and a dual op-amp
    (multi-unit) next to an MCU with stacked GND/VCC pins: clean ERC, netlist = IR (PWR_FLAGs are not
    parts), lossless KiCad round trip."""
    from .circuits import can_node, ldo_supply, opamp_mcu

    for circuit in (can_node(), ldo_supply(), opamp_mcu()):
        out = workdir / circuit.project_id
        generated = generate_schematic(circuit, library)
        sch = generated.write(out)
        erc = kicad_cli.erc(sch, out / "erc.json")
        assert erc.violations == (), erc.summary()
        diff = compare_with_ir(circuit, parse_kicad_netlist(kicad_cli.export_netlist(sch, out / "x.net")))
        assert diff.ok, diff.describe()
        ours = generated.text.splitlines()
        kicad_cli.upgrade(sch)
        resaved = sch.read_text(encoding="utf-8").splitlines()
        assert [(a, b) for a, b in zip(ours, resaved, strict=True) if a != b] == [
            ('\t(generator "ai_circuit_studio")', '\t(generator "eeschema")'),
            ('\t(generator_version "0.1")', '\t(generator_version "10.0")'),
        ]


@pytest.mark.skipif(not os.environ.get("AICS_KICAD_DOCKER_IMAGE"),
                    reason="needs the KiCad Docker image (AICS_KICAD_DOCKER_IMAGE)")
def test_library_tables_silence_an_unconfigured_kicad(tmp_path, timer_board):
    """kicad-cli on a computer whose KiCad never set up its global library tables (an empty HOME here) warns about
    every symbol and footprint; with the project's own tables it does not."""
    import shutil
    import subprocess

    from kicad_adapter import SymbolLibrary

    from .conftest import SYMBOL_DIR

    kicad_symbols = tmp_path / "share" / "kicad" / "symbols"
    shutil.copytree(SYMBOL_DIR, kicad_symbols)
    project, home = tmp_path / "project", tmp_path / "home"
    home.mkdir()
    generate_schematic(timer_board, SymbolLibrary([kicad_symbols]), project_name="timer").write(project)

    def setup_warnings() -> int:
        docker = ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                  "--env", "HOME=/home/empty", "-v", f"{project}:/work", "-v", f"{home}:/home/empty", "-w", "/work"]
        erc = ["kicad-cli", "sch", "erc", "--format", "json", "--severity-all", "-o", "/work/erc.json",
               "/work/timer.kicad_sch"]
        subprocess.run([*docker, os.environ["AICS_KICAD_DOCKER_IMAGE"], *erc], check=True, capture_output=True)
        report = json.loads((project / "erc.json").read_text(encoding="utf-8"))
        return sum(v["type"] in ("lib_symbol_issues", "footprint_link_issues")
                   for sheet in report["sheets"] for v in sheet["violations"])

    assert setup_warnings() == 0
    for table in ("sym-lib-table", "fp-lib-table"):
        (project / table).rename(project / f"{table}.off")
    assert setup_warnings() > 10  # the situation the tables fix: one warning per symbol and footprint
