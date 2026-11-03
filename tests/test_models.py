import json
import os

import pytest
from pydantic import ValidationError

from circuit_core import CircuitIR, VerificationStatus

from .conftest import ROOT

SCHEMA_FILE = ROOT.parent / "docs" / "schema" / "circuit-ir.schema.json"


def test_fixture_loads(led_circuit):
    assert [c.reference for c in led_circuit.components] == ["J1", "R1", "D1"]
    assert {n.name for n in led_circuit.nets} == {"VIN", "LED_A", "GND"}
    assert led_circuit.validation.status is VerificationStatus.UNVERIFIED


def test_unverified_is_the_default(led_circuit_data):
    led_circuit_data["components"][0].pop("pin_map_evidence")
    led_circuit_data.pop("validation")
    circuit = CircuitIR.model_validate(led_circuit_data)
    assert circuit.components[0].pin_map_evidence.status is VerificationStatus.UNVERIFIED
    assert circuit.validation.status is VerificationStatus.UNVERIFIED


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["components"][0].update(reference="j1"),          # lower-case ref
        lambda d: d["components"][0].update(symbol_id="Conn_01x02"),  # no library nickname
        lambda d: d["components"][0].update(pins=[]),                 # no pins
        lambda d: d["components"][0].update(id="Conn 1"),             # unstable id
        lambda d: d["nets"][0].update(name="/VIN"),                   # '/' reserved
        lambda d: d["components"][0].update(colour="red"),            # unknown field
    ],
)
def test_schema_rejects_bad_input(led_circuit_data, mutate):
    mutate(led_circuit_data)
    with pytest.raises(ValidationError):
        CircuitIR.model_validate(led_circuit_data)


def test_ir_is_immutable(led_circuit):
    with pytest.raises(ValidationError):
        led_circuit.components[0].reference = "J2"


def test_json_round_trip(led_circuit):
    again = CircuitIR.model_validate_json(led_circuit.model_dump_json())
    assert again == led_circuit


def test_committed_json_schema_is_current():
    """docs/schema/circuit-ir.schema.json must match the Pydantic model."""
    current = CircuitIR.model_json_schema()
    if os.environ.get("AICS_UPDATE_GOLDEN"):
        SCHEMA_FILE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    assert json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) == current, (
        "schema changed: rerun with AICS_UPDATE_GOLDEN=1 and commit the file"
    )
