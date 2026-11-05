"""Engineering review records: the only way the "engineering review" status changes (plan 10.1)."""

from __future__ import annotations

from fastapi import APIRouter

from .reviews import ReviewCreate, ReviewStore, checklist_for
from .store import ProjectStore

BOARD = "/api/projects/{project_id}/boards/{board_id}"


def build_review_router(store: ProjectStore, reviews: ReviewStore) -> APIRouter:
    router = APIRouter()

    @router.get(BOARD + "/reviews")
    def list_reviews(project_id: str, board_id: str):
        items = reviews.list(project_id, board_id)
        return {"status": reviews.status(project_id, board_id),
                "reviews": [r.model_dump(mode="json") for r in reversed(items)]}

    @router.get(BOARD + "/reviews/checklist")
    def checklist(project_id: str, board_id: str):
        circuit, revision = store.get_circuit(project_id, board_id)
        return {"revision": revision, "items": checklist_for(circuit)}

    @router.post(BOARD + "/reviews", status_code=201)
    def add_review(project_id: str, board_id: str, body: ReviewCreate):
        record = reviews.add(project_id, board_id, body)
        return {"review": record.model_dump(mode="json"), "status": reviews.status(project_id, board_id)}

    return router
