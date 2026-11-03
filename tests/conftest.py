from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from circuit_core import CircuitIR
from kicad_adapter import KiCadCli, SymbolLibrary

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
GOLDEN = ROOT / "golden"
SYMBOL_DIR = FIXTURES / "kicad_symbols"
LED_CIRCUIT = FIXTURES / "circuits" / "led_resistor_connector.json"


@pytest.fixture
def library() -> SymbolLibrary:
    return SymbolLibrary([SYMBOL_DIR])


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
