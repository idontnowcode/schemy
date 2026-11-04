"""Shared helpers for backend API tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend import Settings, create_app

from .conftest import LED_CIRCUIT, SYMBOL_DIR

TOKEN = "test-token"


def make_client(workspace: Path, *, kicad_factory=lambda: None, symbol_dirs=None, token=TOKEN) -> TestClient:
    settings = Settings(
        workspace=workspace,
        token=token,
        symbol_dirs=[SYMBOL_DIR] if symbol_dirs is None else symbol_dirs,
        kicad_factory=kicad_factory,
    )
    client = TestClient(create_app(settings))
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return client


def led_ir() -> dict:
    return json.loads(LED_CIRCUIT.read_text(encoding="utf-8"))


def new_project(client: TestClient, name: str = "LED Demo") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def save_ir(client: TestClient, project_id: str, ir: dict | None = None) -> dict:
    r = client.put(f"/api/projects/{project_id}/boards/a/circuit", json=ir or led_ir())
    assert r.status_code == 200, r.text
    return r.json()


def run_export(client: TestClient, project_id: str, force: bool = False) -> dict:
    r = client.post(f"/api/projects/{project_id}/boards/a/exports", json={"force": force})
    assert r.status_code == 202, r.text
    job = client.app.state.jobs.wait(r.json()["id"], timeout=300)
    return job.model_dump()
