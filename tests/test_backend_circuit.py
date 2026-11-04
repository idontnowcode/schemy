"""Circuit IR validation, saving, revisions, recovery and derived documents."""

from .backend_helpers import led_ir, make_client, new_project, save_ir


def test_validate_reports_each_level(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    url = f"/api/projects/{pid}/circuit/validate"

    ok = client.post(url, json=led_ir()).json()
    assert ok == {"valid": True, "schema_errors": [], "issues": [], "library_checked": True}

    bad_schema = led_ir()
    del bad_schema["components"][0]["symbol_id"]
    r = client.post(url, json=bad_schema).json()
    assert r["valid"] is False
    assert r["schema_errors"][0]["loc"] == ["components", "0", "symbol_id"]

    swapped = led_ir()
    swapped["components"][2]["pins"] = [{"number": "1", "name": "A"}, {"number": "2", "name": "K"}]
    r = client.post(url, json=swapped).json()
    assert r["valid"] is False
    assert {(i["error_code"], i["level"]) for i in r["issues"]} == {("PIN_NAME_MISMATCH", "library")}


def test_save_creates_revisions_only_on_change(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    first = save_ir(client, pid)
    assert first["created"] is True
    assert first["revision"]["revision"] == 1
    assert first["revision"]["source"] == "user_edit"

    assert save_ir(client, pid)["created"] is False  # identical content

    ir = led_ir()
    ir["components"][1]["value"] = "1k"
    second = save_ir(client, pid, ir)
    assert second["revision"]["revision"] == 2
    assert second["revision"]["parent"] == 1
    assert client.get(f"/api/projects/{pid}").json()["current_revision"] == 2
    revs = client.get(f"/api/projects/{pid}/revisions").json()
    assert [r["revision"] for r in revs] == [2, 1]


def test_save_aligns_project_id(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client, "My LED")
    r = save_ir(client, pid)  # fixture says project_id "led-resistor-connector"
    assert r["warnings"] == ["project_id 'led-resistor-connector' was changed to 'my-led' to match this project."]
    assert client.get(f"/api/projects/{pid}/circuit").json()["circuit"]["project_id"] == "my-led"


def test_rule_errors_block_saving_library_errors_do_not(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)

    dup = led_ir()
    dup["components"][2]["reference"] = "R1"
    r = client.put(f"/api/projects/{pid}/circuit", json=dup)
    assert r.status_code == 422
    body = r.json()
    assert body["error_code"] == "INVALID_CIRCUIT"
    assert set(body["affected_component_ids"]) == {"res-001", "led-001"}
    assert client.get(f"/api/projects/{pid}/circuit").json() == {"circuit": None, "revision": None}

    swapped = led_ir()
    swapped["components"][2]["pins"] = [{"number": "1", "name": "A"}, {"number": "2", "name": "K"}]
    r = client.put(f"/api/projects/{pid}/circuit", json=swapped)
    assert r.status_code == 200
    assert r.json()["validation"]["valid"] is False  # shown to the user, enforced at generation


def test_restore_revision(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    ir = led_ir()
    ir["components"][1]["value"] = "1k"
    save_ir(client, pid, ir)

    r = client.post(f"/api/projects/{pid}/revisions/1/restore").json()
    assert r["created"] is True
    assert r["revision"]["revision"] == 3
    assert r["revision"]["restored_from"] == 1
    assert r["revision"]["source"] == "restore"
    assert client.get(f"/api/projects/{pid}/circuit").json()["circuit"]["components"][1]["value"] == "330"
    # Restoring what is already current changes nothing.
    assert client.post(f"/api/projects/{pid}/revisions/3/restore").json()["created"] is False
    assert client.get(f"/api/projects/{pid}/revisions/9").status_code == 404


def test_damaged_working_copy_is_recovered_from_snapshot(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    working = tmp_path / "projects" / pid / "circuit.json"
    working.write_text("{ truncated", encoding="utf-8")
    r = client.get(f"/api/projects/{pid}/circuit").json()
    assert r["revision"] == 1
    assert r["circuit"]["components"][0]["reference"] == "J1"
    assert working.read_text(encoding="utf-8").startswith("{\n")


def test_documents_are_deterministic(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    assert client.get(f"/api/projects/{pid}/documents/markdown").json()["error_code"] == "NO_CIRCUIT"
    save_ir(client, pid)
    md1 = client.get(f"/api/projects/{pid}/documents/markdown").json()
    md2 = client.get(f"/api/projects/{pid}/documents/markdown").json()
    assert md1 == md2
    assert "| LED_A | D1.2, R1.2 |" in md1["markdown"]
    bom = client.get(f"/api/projects/{pid}/documents/bom").json()
    assert [row["References"] for row in bom["rows"]] == ["D1", "J1", "R1"]
    assert bom["columns"][:4] == ["References", "Quantity", "Value", "Symbol"]
