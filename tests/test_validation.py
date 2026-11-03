from circuit_core import CircuitIR, validate_circuit


def codes(result):
    return sorted(i.error_code for i in result.issues)


def test_fixture_has_no_issues(led_circuit):
    result = validate_circuit(led_circuit)
    assert result.ok
    assert result.issues == []


def test_duplicate_reference(led_circuit_data):
    led_circuit_data["components"][2]["reference"] = "R1"
    result = validate_circuit(CircuitIR.model_validate(led_circuit_data))
    assert "DUPLICATE_REFERENCE" in codes(result)
    issue = next(i for i in result.errors if i.error_code == "DUPLICATE_REFERENCE")
    assert set(issue.affected_component_ids) == {"res-001", "led-001"}
    assert issue.recovery_hint


def test_pin_name_used_instead_of_number(led_circuit_data):
    # The plan's illustrative IR used LED pins "A"/"K"; KiCad's Device:LED numbers them 2/1.
    led_circuit_data["nets"][1]["nodes"][1]["pin"] = "A"
    result = validate_circuit(CircuitIR.model_validate(led_circuit_data))
    assert codes(result) == ["UNCONNECTED_PIN", "UNKNOWN_PIN"]


def test_unknown_component(led_circuit_data):
    led_circuit_data["nets"][0]["nodes"][0]["component_id"] = "conn-999"
    result = validate_circuit(CircuitIR.model_validate(led_circuit_data))
    assert "UNKNOWN_COMPONENT" in codes(result)


def test_pin_in_two_nets(led_circuit_data):
    led_circuit_data["nets"][2]["nodes"].append({"component_id": "res-001", "pin": "1"})
    result = validate_circuit(CircuitIR.model_validate(led_circuit_data))
    assert "PIN_IN_MULTIPLE_NETS" in codes(result)


def test_unconnected_pin_and_single_node_net_are_warnings(led_circuit_data):
    led_circuit_data["nets"][2]["nodes"] = [{"component_id": "conn-001", "pin": "2"}]
    result = validate_circuit(CircuitIR.model_validate(led_circuit_data))
    assert result.ok
    assert codes(result) == ["SINGLE_NODE_NET", "UNCONNECTED_PIN"]


def test_layout_checks(led_circuit_data):
    led_circuit_data["layout"]["led-001"]["rotation"] = 45
    led_circuit_data["layout"]["ghost"] = {"x": 0, "y": 0}
    result = validate_circuit(CircuitIR.model_validate(led_circuit_data))
    assert codes(result) == ["LAYOUT_UNKNOWN_COMPONENT", "UNSUPPORTED_ROTATION"]
