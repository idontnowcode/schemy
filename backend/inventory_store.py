"""Workspace-level available-parts list (<workspace>/inventory/).

One list per workspace: it describes what the team has on the shelf, not one design.
The original upload is kept next to the normalised JSON for traceability.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path, PurePath

from circuit_core.inventory import Inventory, InventoryPart

from .store import atomic_write, now_iso


class InventoryStore:
    def __init__(self, workspace: Path):
        self.dir = Path(workspace) / "inventory"
        self.json_path = self.dir / "inventory.json"

    def get(self) -> Inventory | None:
        if not self.json_path.is_file():
            return None
        try:
            return Inventory.model_validate_json(self.json_path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def save(self, filename: str, data: bytes, parts: list[InventoryPart]) -> Inventory:
        self.dir.mkdir(parents=True, exist_ok=True)
        suffix = PurePath(filename).suffix.lower() or ".bin"
        for old in self.dir.glob("source.*"):
            old.unlink()
        tmp = self.dir / f".source.{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, self.dir / f"source{suffix}")
        inventory = Inventory(source_file=PurePath(filename).name, imported_at=now_iso(), parts=parts)
        atomic_write(self.json_path, inventory.model_dump_json(indent=2) + "\n")
        return inventory

    def delete(self) -> bool:
        existed = self.json_path.is_file()
        if existed:
            self.json_path.unlink()
        for old in self.dir.glob("source.*"):
            old.unlink()
        return existed
