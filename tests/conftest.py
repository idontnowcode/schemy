from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from circuit_core import CircuitIR
from kicad_adapter import KiCadCli, SymbolLibrary

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
EXAMPLE_NAME = "led-resistor-connector"
EXAMPLE_DIR = ROOT.parent / "examples" / EXAMPLE_NAME
SYMBOL_DIR = FIXTURES / "kicad_symbols"
LED_CIRCUIT = EXAMPLE_DIR / "circuit.json"
SAMPLE_NAME = "atmega328p-led-board"
SAMPLE_DIR = ROOT.parent / "examples" / SAMPLE_NAME
TIMER_NAME = "ne555-led-board"
TIMER_DIR = ROOT.parent / "examples" / TIMER_NAME
REGULATOR_NAME = "lm7805-24v-to-5v"
REGULATOR_DIR = ROOT.parent / "examples" / REGULATOR_NAME
MOTOR_NAME = "dc-motor-drv8871"
MOTOR_DIR = ROOT.parent / "examples" / MOTOR_NAME
I2C_NAME = "i2c-sensor-3v3"
I2C_DIR = ROOT.parent / "examples" / I2C_NAME
PRACTICAL_NAMES = ["usbc-5v-3v3", "iso-24v-input", "relay-driver-12v", "rs485-3v3", "analog-0-10v"]
"""examples/<name>: practical boards (USB-C power, isolated input, relay, RS-485, 0-10 V input) designed through
the app's design pipeline, each with an engineer's research note."""


@pytest.fixture
def library() -> SymbolLibrary:
    return SymbolLibrary([SYMBOL_DIR])


@pytest.fixture
def sample_board() -> CircuitIR:
    """examples/atmega328p-led-board: an MCU board built with a template (multi-pin IC, crystal, reset)."""
    return CircuitIR.model_validate_json((SAMPLE_DIR / "circuit.json").read_text(encoding="utf-8"))


@pytest.fixture
def timer_board() -> CircuitIR:
    """examples/ne555-led-board: a board without an MCU (555 timer, power input block, English notes)."""
    return CircuitIR.model_validate_json((TIMER_DIR / "circuit.json").read_text(encoding="utf-8"))


@pytest.fixture
def motor_board() -> CircuitIR:
    """examples/dc-motor-drv8871: a 12 V brushed DC motor driver (DRV8871) with a protected power input."""
    return CircuitIR.model_validate_json((MOTOR_DIR / "circuit.json").read_text(encoding="utf-8"))


@pytest.fixture
def regulator_board() -> CircuitIR:
    """examples/lm7805-24v-to-5v: a 7805 with input/output capacitors, protection diodes and a power LED."""
    return CircuitIR.model_validate_json((REGULATOR_DIR / "circuit.json").read_text(encoding="utf-8"))


@pytest.fixture
def led_circuit_data() -> dict:
    return json.loads(LED_CIRCUIT.read_text(encoding="utf-8"))


@pytest.fixture
def led_circuit(led_circuit_data) -> CircuitIR:
    return CircuitIR.model_validate(led_circuit_data)


@pytest.fixture(scope="session")
def kicad_cli() -> KiCadCli:
    cli = KiCadCli.detect()
    if cli is None:
        pytest.skip(
            "kicad-cli not available (install KiCad 10, or set AICS_KICAD_CLI / "
            "AICS_KICAD_DOCKER_IMAGE) - KiCad checks are pending, not passed"
        )
    return cli


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    yield tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)
