"""Projects, requirements, settings, auth, and the Phase 1 restart gate."""

import json

from circuit_core import CircuitIR

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
    assert p["boards"] == ["a"]
    board = p["board_list"][0]
    assert (board["id"], board["name"], board["current_revision"]) == ("a", "보드 A", None)
    assert board["kicad_project"] == "stm32-can-controller-a"
    folder = tmp_path / "projects" / p["id"]
    for name in ("project.json", "requirements.md", "design_notes.md", "boards/a/board.json"):
        assert (folder / name).is_file()
    for name in ("boards/a/revisions", "boards/a/outputs", "boards/a/imports"):
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
    first.put("/api/settings", json={"last_project_id": pid, "last_board_id": "a", "last_tab": "schematic"})
    before = first.get(f"/api/projects/{pid}/boards/a/circuit").json()
    first.close()

    second = make_client(tmp_path)  # a new app instance on the same workspace = app restart
    assert [p["id"] for p in second.get("/api/projects").json()] == [pid]
    assert second.get("/api/settings").json() == {"last_project_id": pid, "last_board_id": "a",
                                                  "last_tab": "schematic"}
    assert second.get(f"/api/projects/{pid}/boards/a/circuit").json() == before
    assert second.get(f"/api/projects/{pid}/requirements").json()["text"] == "# R\n\n5V input\n"
    assert second.get(f"/api/projects/{pid}/boards/a").json()["current_revision"] == 1


def test_index_is_rebuilt_from_folders(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid, led_ir())
    client.close()
    (tmp_path / "index.sqlite").unlink()
    again = make_client(tmp_path)
    assert [p["id"] for p in again.get("/api/projects").json()] == [pid]
    assert again.get(f"/api/projects/{pid}/boards/a/circuit").json()["revision"] == 1


def test_system_reports_environment(tmp_path):
    info = make_client(tmp_path).get("/api/system").json()
    assert info["kicad"] == {"available": False, "version": None, "runner": None}
    assert info["workspace"] == str(tmp_path)
    assert len(info["symbol_dirs"]) == 1


def test_examples_are_offered(tmp_path):
    examples = make_client(tmp_path).get("/api/examples").json()
    led = next(e for e in examples if e["id"] == "led-resistor-connector")
    assert led["title"].startswith("LED + series resistor")
    assert CircuitIR.model_validate(led["circuit"]) == CircuitIR.model_validate(led_ir())
    sample = next(e for e in examples if e["id"] == "atmega328p-led-board")
    assert sample["title"] == "ATmega328P 상태 LED 보드"
    timer = next(e for e in examples if e["id"] == "ne555-led-board")
    assert timer["title"] == "NE555 LED 점멸 보드" and timer["circuit"]["notes"]
    assert any(e["id"] == "lm7805-24v-to-5v" for e in examples)
    assert any(e["id"] == "i2c-sensor-3v3" for e in examples)



# --- boards -------------------------------------------------------------------------

def test_boards_crud(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    r = client.post(f"/api/projects/{pid}/boards", json={"name": "Board B"})
    assert r.status_code == 201
    assert r.json()["id"] == "board-b"
    assert client.post(f"/api/projects/{pid}/boards", json={"name": "보드 C"}).json()["id"] == "c"
    assert client.post(f"/api/projects/{pid}/boards", json={"name": "전원"}).json()["id"] == "board"
    assert [b["id"] for b in client.get(f"/api/projects/{pid}/boards").json()] == ["a", "board-b", "c", "board"]
    assert client.patch(f"/api/projects/{pid}/boards/board", json={"name": "전원 보드"}).json()["name"] == "전원 보드"

    r = client.delete(f"/api/projects/{pid}/boards/board-b")
    assert r.status_code == 200 and ".trash" in r.json()["moved_to"]
    assert client.get(f"/api/projects/{pid}").json()["boards"] == ["a", "c", "board"]
    client.delete(f"/api/projects/{pid}/boards/board")
    client.delete(f"/api/projects/{pid}/boards/c")
    r = client.delete(f"/api/projects/{pid}/boards/a")
    assert r.status_code == 409 and r.json()["error_code"] == "LAST_BOARD"
    assert client.get(f"/api/projects/{pid}/boards/zzz").json()["error_code"] == "BOARD_NOT_FOUND"


def test_boards_are_independent(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    client.post(f"/api/projects/{pid}/boards", json={"name": "B"})
    save_ir(client, pid)
    assert client.get(f"/api/projects/{pid}/boards/b/circuit").json() == {"circuit": None, "revision": None}
    ir = led_ir()
    ir["components"][1]["value"] = "1k"
    r = client.put(f"/api/projects/{pid}/boards/b/circuit", json=ir)
    assert r.json()["revision"]["revision"] == 1
    assert client.get(f"/api/projects/{pid}/boards/a/circuit").json()["circuit"]["components"][1]["value"] == "330"


def test_open_remembers_the_board(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    client.post(f"/api/projects/{pid}/boards", json={"name": "B"})
    client.post(f"/api/projects/{pid}/open")
    assert client.get("/api/settings").json()["last_board_id"] == "a"
    client.put("/api/settings", json={"last_project_id": pid, "last_board_id": "b", "last_tab": "circuit"})
    client.post(f"/api/projects/{pid}/open")
    assert client.get("/api/settings").json()["last_board_id"] == "b"


def test_phase1_project_is_moved_into_board_a(tmp_path):
    """A project written by the Phase 1 app (circuit at the project root) opens as board A."""
    folder = tmp_path / "projects" / "old"
    (folder / "revisions").mkdir(parents=True)
    (folder / "outputs").mkdir()
    (folder / "datasheets").mkdir()
    (folder / "requirements.md").write_text("# R\n", encoding="utf-8")
    ir = json.dumps(led_ir() | {"project_id": "old"})
    (folder / "circuit.json").write_text(ir, encoding="utf-8")
    (folder / "revisions" / "0001.circuit.json").write_text(ir, encoding="utf-8")
    (folder / "revisions" / "0001.meta.json").write_text(json.dumps({
        "revision": 1, "created_at": "2026-11-04T10:00:00+09:00", "source": "user_edit", "checksum": "x",
        "parent": None, "restored_from": None, "note": None, "component_count": 3, "net_count": 3}),
        encoding="utf-8")
    (folder / "project.json").write_text(json.dumps({
        "schema_version": 1, "id": "old", "name": "Old", "description": "", "created_at": "2026-11-04T10:00:00+09:00",
        "updated_at": "2026-11-04T10:00:00+09:00", "current_revision": 1}), encoding="utf-8")

    client = make_client(tmp_path)
    project = client.get("/api/projects/old").json()
    assert project["schema_version"] == 2 and project["boards"] == ["a"]
    assert project["board_list"][0]["current_revision"] == 1
    assert client.get("/api/projects/old/boards/a/circuit").json()["revision"] == 1
    assert not (folder / "circuit.json").exists()
    assert (folder / "boards" / "a" / "revisions" / "0001.meta.json").is_file()
    assert client.get("/api/projects/old/requirements").json()["text"] == "# R\n"
