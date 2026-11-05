"""Engineering review records: the only way the "engineering review" status changes (plan 10.1)."""

from __future__ import annotations

from fastapi import APIRouter

from circuit_core.guides import relevant

from .guides import GuideLibrary
from .reviews import ReviewCreate, ReviewStore, checklist_for
from .store import ProjectStore

BOARD = "/api/projects/{project_id}/boards/{board_id}"


def build_review_router(store: ProjectStore, reviews: ReviewStore, guides: GuideLibrary | None = None) -> APIRouter:
    router = APIRouter()

    @router.get(BOARD + "/reviews")
    def list_reviews(project_id: str, board_id: str):
        items = reviews.list(project_id, board_id)
        return {"status": reviews.status(project_id, board_id),
                "reviews": [r.model_dump(mode="json") for r in reversed(items)]}

    @router.get(BOARD + "/reviews/checklist")
    def checklist(project_id: str, board_id: str):
        circuit, revision = store.get_circuit(project_id, board_id)
        items = checklist_for(circuit)
        if guides is not None and circuit is not None:
            # The adopted design-guide entries that apply, as items the engineer can tick.
            label = {"must": "필수", "should": "권장", "consider": "검토"}
            for e in relevant(guides.entries(), circuit=circuit, text=store.get_requirements(project_id),
                              text_topics=False):
                if e.strength == "consider":
                    continue
                items.append({"source": f"guide:{e.id}", "item": f"[{label[e.strength]}] {e.title}: {e.rule}",
                              "basis": f"설계 가이드 {e.id}", "group": "설계 가이드"})
        return {"revision": revision, "items": items}

    @router.post(BOARD + "/reviews", status_code=201)
    def add_review(project_id: str, board_id: str, body: ReviewCreate):
        record = reviews.add(project_id, board_id, body)
        if guides is not None:
            issues = [c.source.split(":", 1)[1] for c in body.checks
                      if c.source.startswith("guide:") and c.result == "issue"]
            if issues:
                guides.record("issue", issues, by=body.reviewer, project_id=project_id, board_id=board_id,
                              review_id=record.id, revision=body.revision)
        return {"review": record.model_dump(mode="json"), "status": reviews.status(project_id, board_id)}

    return router
