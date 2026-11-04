"""Inventory API: upload (preview / save), match a board, apply as a new revision."""

import base64

from .backend_helpers import make_client, new_project, save_ir
from .test_inventory import KOREAN_CSV


def upload(client, text=KOREAN_CSV, *, save, name="자재.csv", encoding="utf-8"):
    body = {"filename": name, "content_base64": base64.b64encode(text.encode(encoding)).decode(), "save": save}
    return client.post("/api/inventory/import", json=body)


def test_preview_does_not_save(tmp_path):
    client = make_client(tmp_path)
    r = upload(client, save=False).json()
    assert r["ok"] and r["part_count"] == 5 and r["saved"] is False
    assert r["unknown_columns"] == ["사용처"]
    assert client.get("/api/inventory").json()["inventory"] is None


def test_save_replace_and_delete(tmp_path):
    client = make_client(tmp_path)
    r = upload(client, save=True, encoding="cp949").json()
    assert r["saved"] is True and r["source_file"] == "자재.csv"
    inv = client.get("/api/inventory").json()
    assert len(inv["inventory"]["parts"]) == 5
    assert inv["category_labels"]["resistor"] == "저항"
    assert (tmp_path / "inventory" / "source.csv").is_file()
    upload(client, "품번,값,종류\nX1,1k,저항\n", save=True)
    assert len(client.get("/api/inventory").json()["inventory"]["parts"]) == 1
    assert client.delete("/api/inventory").json() == {"deleted": True}
    assert client.get("/api/inventory").json()["inventory"] is None


def test_invalid_file_is_not_saved(tmp_path):
    client = make_client(tmp_path)
    r = upload(client, "이름,색\nA,red\n", save=True)
    assert r.status_code == 422 and r.json()["error_code"] == "INVALID_INVENTORY"
    bad = client.post("/api/inventory/import", json={"filename": "a.csv", "content_base64": "%%%", "save": False})
    assert bad.status_code == 422 and bad.json()["error_code"] == "INVALID_UPLOAD"


def test_match_and_apply(tmp_path):
    client = make_client(tmp_path)
    pid = new_project(client)
    save_ir(client, pid)
    url = f"/api/projects/{pid}/boards/a"
    assert client.get(url + "/inventory-match").json()["available"] is False
    r = client.post(url + "/inventory-apply", json={})
    assert r.status_code == 409 and r.json()["error_code"] == "NO_INVENTORY"

    upload(client, save=True)
    match = client.get(url + "/inventory-match?boards=11").json()
    assert match["summary"] == {"matched": 3, "candidate": 0, "missing": 0, "stock_short": 1}
    r1 = next(line for line in match["lines"] if line["references"] == ["R1"])
    assert r1["chosen"]["mpn"] == "RC0603FR-07330RL" and r1["category_label"] == "저항"

    applied = client.post(url + "/inventory-apply", json={}).json()
    assert applied["created"] is True
    assert applied["revision"]["source"] == "inventory"
    assert applied["revision"]["note"] == "보유 자재 적용: 3개 부품"
    assert {(c["reference"], c["after"]) for c in applied["changes"]} >= {("R1", "RC0603FR-07330RL")}
    circuit = client.get(url + "/circuit").json()["circuit"]
    assert circuit["components"][1]["manufacturer_part_number"] == "RC0603FR-07330RL"
    assert client.post(url + "/inventory-apply", json={}).json() == {
        "revision": client.get(url + "/revisions").json()[0], "created": False, "changes": []}

    alt = next(c["part"]["id"] for c in r1["candidates"] if c["part"]["mpn"] == "RC0805FR-07330RL")
    chosen = client.post(url + "/inventory-apply", json={"apply_matched": False, "choices": {"res-001": alt}}).json()
    assert [(c["field"], c["after"]) for c in chosen["changes"]] == [("manufacturer_part_number", "RC0805FR-07330RL")]
    bad = client.post(url + "/inventory-apply", json={"choices": {"res-001": "inv-99999"}})
    assert bad.status_code == 422 and bad.json()["error_code"] == "INVALID_CHOICE"
