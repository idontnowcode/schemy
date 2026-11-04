"""Projects, requirements, settings, auth, and the Phase 1 restart gate."""

import json

from .backend_helpers import TOKEN, led_ir, make_client, new_project, save_ir


def test_requires_token(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/projects").status_code == 200
    client.headers.pop("Authorization")
    r = client.get("/api/projects")
    assert r.status_code == 401
    assert r.json()["error_code"] == "UNAUTHORIZED"
    client.headers["Authorization"] = "Bearer wrong"
    assert client.get("/api/projects").status_code == 401
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    assert client.get("/api/system").status_code == 200


def test_create_project_lays_out_folder(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/api/projects", json={"name": "STM32 CAN Controller", "description": "demo"})
    assert r.status_code == 201
    p = r.json()
    assert p["id"] == "stm32-can-controller"
    assert p["current_revision"] is None
    folder = tmp_path / "projects" / p["id"]
    for name in ("project.json", "requirements.md", "design_notes.md"):
        assert (folder / name).is_file()
    for name in ("datasheets", "revisions", "outputs"):
        assert (folder / name).is_dir()
    meta = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    assert meta["name"] == "STM32 CAN Controller"
    assert "+" in meta["created_at"] or "-" in meta["created_at"][19:]  # ISO 8601 with UTC offset


def test_project_ids_are_unique_and_ascii(tmp_path):
    client = make_client(tmp_path)
    assert new_project(client, "LED Demo") == "led-demo"
    assert new_project(client, "LED demo!") == "led-demo-2"
    assert new_project(client, "한글 프로젝트") == "project"


def test_update_list_and_delete(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    other = new_project(client, "Other")
    r = client.patch(f"/api/projects/{pid}", json={"name": "Renamed", "description": "x"})
    assert r.json()["name"] == "Renamed"
    client.post(f"/api/projects/{pid}/open")
    ids = [p["id"] for p in client.get("/api/projects").json()]
    assert ids[0] == pid and other in ids  # most recently opened first

    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200
    assert ".trash" in r.json()["moved_to"]
    assert [p["id"] for p in client.get("/api/projects").json()] == [other]
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get("/api/settings").json()["last_project_id"] is None
    # The trash folder carries a timestamp, so the id can be used again.
    assert new_project(client, "LED Demo") == "led-demo"


def test_unknown_and_unsafe_ids_are_404(tmp_path):
    client = make_client(tmp_path)
    for pid in ("nope", "..", "a%2F..%2Fb", "UPPER"):
        r = client.get(f"/api/projects/{pid}")
        assert r.status_code == 404, pid
        assert r.json()["error_code"] in ("PROJECT_NOT_FOUND", "NOT_FOUND"), pid


def test_requirements_round_trip(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    assert "# 요구사항" in client.get(f"/api/projects/{pid}/requirements").json()["text"]
    text = "# 요구사항\n\n- 입력 5V\n- LED 1개\n"
    assert client.put(f"/api/projects/{pid}/requirements", json={"text": text}).status_code == 200
    assert client.get(f"/api/projects/{pid}/requirements").json()["text"] == text
    assert (tmp_path / "projects" / pid / "requirements.md").read_text(encoding="utf-8") == text


def test_invalid_request_uses_error_contract(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/api/projects", json={"name": ""})
    assert r.status_code == 422
    body = r.json()
    assert body["error_code"] == "INVALID_REQUEST"
    assert set(body) >= {"message", "affected_component_ids", "recovery_hint"}


# --- Phase 1 gate: restart restores the project ------------------------------------

def test_restart_restores_projects_settings_and_circuit(tmp_path):
    first = make_client(tmp_path)
    pid = new_project(first)
    save_ir(first, pid)
    first.put(f"/api/projects/{pid}/requirements", json={"text": "# R\n\n5V input\n"})
    first.post(f"/api/projects/{pid}/open")
    first.put("/api/settings", json={"last_project_id": pid, "last_tab": "schematic"})
    before = first.get(f"/api/projects/{pid}/circuit").json()
    first.close()

    second = make_client(tmp_path)  # a new app instance on the same workspace = app restart
    assert [p["id"] for p in second.get("/api/projects").json()] == [pid]
    assert second.get("/api/settings").json() == {"last_project_id": pid, "last_tab": "schematic"}
    assert second.get(f"/api/projects/{pid}/circuit").json() == before
    assert second.get(f"/api/projects/{pid}/requirements").json()["text"] == "# R\n\n5V input\n"
    assert second.get(f"/api/projects/{pid}").json()["current_revision"] == 1


def test_index_is_rebuilt_from_folders(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid, led_ir())
    client.close()
    (tmp_path / "index.sqlite").unlink()
    again = make_client(tmp_path)
    assert [p["id"] for p in again.get("/api/projects").json()] == [pid]
    assert again.get(f"/api/projects/{pid}/circuit").json()["revision"] == 1


def test_system_reports_environment(tmp_path):
    info = make_client(tmp_path).get("/api/system").json()
    assert info["kicad"] == {"available": False, "version": None, "runner": None}
    assert info["workspace"] == str(tmp_path)
    assert len(info["symbol_dirs"]) == 1
