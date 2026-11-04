"""/api/inventory and the per-project inventory match/apply endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from circuit_core.inventory import (
    COLUMN_LABELS,
    InventoryApplyError,
    apply_inventory,
    match_inventory,
    parse_inventory,
    summarize,
)
from circuit_core.parts import CATEGORY_LABELS

from .errors import ApiError
from .inventory_store import InventoryStore
from .schemas import InventoryApplyRequest, InventoryImportRequest
from .store import ProjectStore
from .uploads import MB, decode_upload

MAX_INVENTORY_BYTES = 10 * MB


def build_inventory_router(store: ProjectStore, inventory: InventoryStore) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/inventory")
    def get_inventory():
        inv = inventory.get()
        return {
            "inventory": inv.model_dump() if inv else None,
            "column_labels": COLUMN_LABELS,
            "category_labels": CATEGORY_LABELS,
        }

    @router.post("/inventory/import")
    def import_inventory(body: InventoryImportRequest):
        data = decode_upload(body.content_base64, max_bytes=MAX_INVENTORY_BYTES, what="자재 목록 파일")
        parsed = parse_inventory(body.filename, data)
        result = parsed.to_dict()
        result["saved"] = False
        if body.save:
            if not parsed.ok:
                raise ApiError(422, "INVALID_INVENTORY", "자재 목록을 읽지 못해 저장하지 않았습니다.", details=result)
            saved = inventory.save(body.filename, data, parsed.parts)
            result.update(saved=True, source_file=saved.source_file, imported_at=saved.imported_at)
        return result

    @router.delete("/inventory")
    def delete_inventory():
        return {"deleted": inventory.delete()}

    def _require(project_id: str, board_id: str):
        store.get_board(project_id, board_id)
        circuit, revision = store.get_circuit(project_id, board_id)
        if circuit is None:
            raise ApiError(409, "NO_CIRCUIT", "This board has no saved Circuit IR yet.",
                           recovery_hint="Save a Circuit IR first.")
        inv = inventory.get()
        return circuit, revision, inv

    @router.get("/projects/{project_id}/boards/{board_id}/inventory-match")
    def inventory_match(project_id: str, board_id: str, boards: int = Query(default=1, ge=1, le=100_000)):
        circuit, revision, inv = _require(project_id, board_id)
        if inv is None:
            return {"available": False, "revision": revision, "boards": boards, "summary": None, "lines": []}
        lines = match_inventory(circuit, inv, boards=boards)
        return {
            "available": True,
            "revision": revision,
            "boards": boards,
            "inventory": {"source_file": inv.source_file, "imported_at": inv.imported_at,
                          "part_count": len(inv.parts)},
            "summary": summarize(lines),
            "lines": [line.to_dict() for line in lines],
        }

    @router.post("/projects/{project_id}/boards/{board_id}/inventory-apply")
    def inventory_apply(project_id: str, board_id: str, body: InventoryApplyRequest):
        circuit, _, inv = _require(project_id, board_id)
        if inv is None:
            raise ApiError(409, "NO_INVENTORY", "보유 자재 목록이 없습니다.",
                           recovery_hint="보유 자재 화면에서 자재 목록 파일을 먼저 올리세요.")
        try:
            updated, changes = apply_inventory(circuit, inv, apply_matched=body.apply_matched, choices=body.choices)
        except InventoryApplyError as exc:
            raise ApiError(422, "INVALID_CHOICE", str(exc)) from exc
        if not changes:
            _, revision = store.get_circuit(project_id, board_id)
            return {"revision": store.get_revision_info(project_id, board_id, revision) if revision else None,
                    "created": False, "changes": []}
        note = f"보유 자재 적용: {len({c.component_id for c in changes})}개 부품"
        info, created = store.save_circuit(project_id, board_id, updated, source="inventory", note=note)
        return {"revision": info, "created": created, "changes": [c.to_dict() for c in changes]}

    return router
