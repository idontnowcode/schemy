"""/api/guides (the workspace design guide) and the per-board guide checklist.

Nothing here changes a circuit. Entries written by a person may be adopted at once; text the AI wrote
(a review finding, a drafted guide) is saved as a candidate and a person adopts it.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ai_engine import AiError
from ai_engine.design import circuit_text, draft_guide
from circuit_core import CircuitIR
from circuit_core.engineering import engineering_review
from circuit_core.guides import (
    CHECKS,
    STRENGTH_LABEL,
    TOPICS,
    checklist,
    parse_guide_csv,
    parse_guide_markdown,
    relevant,
    to_markdown,
)
from circuit_core.quick_fixes import floating_pins

from .ai_access import AiAccess, api_error, run_record
from .config import Settings
from .errors import ApiError
from .guides import GuideLibrary
from .jobs import JobRunner
from .proposals import ProposalStore
from .routes_proposals import pin_types_for
from .schemas import GuideCreate, GuideDecision, GuideDraftRequest, GuideImport, GuideUpdate
from .store import ProjectStore, now_iso

BOARD = "/api/projects/{project_id}/boards/{board_id}"


def circuit_checklist(store: ProjectStore, guides: GuideLibrary, settings: Settings, project_id: str,
                      circuit: CircuitIR) -> list[dict]:
    """The adopted guides that apply to ``circuit`` with what the app's own checks tell (fail / warn / ok / manual /
    exception) — for a saved board, a proposal, or a design that is not even a proposal yet."""
    text = store.get_requirements(project_id)
    applicable = relevant(guides.entries(), circuit=circuit, text=text, text_topics=False)
    refs = {c.id: c.reference for c in circuit.components}
    findings = [f.to_dict(refs) for f in engineering_review(circuit)]
    floating = [f.to_dict() for f in floating_pins(circuit, pin_types_for(circuit, settings.symbol_dirs))]
    waivers = {g: {"id": d.id, "topic": d.topic, "choice": d.choice, "reason": d.reason}
               for d in circuit.decisions for g in d.waives}
    return checklist(applicable, findings, floating, waivers)


def board_checklist(store: ProjectStore, proposals: ProposalStore, guides: GuideLibrary, settings: Settings,
                    project_id: str, board_id: str, proposal_id: str | None = None) -> dict:
    """The adopted guides that apply to the saved circuit (or a proposal's), with what the app can tell itself."""
    store.get_board(project_id, board_id)
    ai_applied: list[str] = []
    if proposal_id:
        p = proposals.get(project_id, board_id, proposal_id)
        circuit, revision = proposals.candidate(p), p.base_revision
        ai_applied = ((p.ai or {}).get("guides") or {}).get("applied") or []
    else:
        circuit, revision = store.get_circuit(project_id, board_id)
    if circuit is None:
        return {"revision": None, "items": [], "candidates": 0, "ai_applied": []}
    text = store.get_requirements(project_id)
    entries = guides.entries()
    items = circuit_checklist(store, guides, settings, project_id, circuit)
    for item in items:
        item["ai_applied"] = item["id"] in ai_applied
    pending = relevant(entries, circuit=circuit, text=text, statuses=("candidate",), text_topics=False)
    return {"revision": revision, "proposal_id": proposal_id, "items": items, "candidates": len(pending),
            "ai_applied": ai_applied}


def build_guide_router(store: ProjectStore, jobs: JobRunner, proposals: ProposalStore, guides: GuideLibrary,
                       settings: Settings) -> APIRouter:
    router = APIRouter()
    access = AiAccess(store, settings)

    def listing() -> dict:
        stats = guides.stats()
        return {"entries": [{**e.model_dump(mode="json"), "stats": stats.get(e.id, {})} for e in guides.entries()],
                "topics": TOPICS, "checks": CHECKS, "strengths": STRENGTH_LABEL}

    @router.get("/api/guides")
    def list_guides():
        return listing()

    @router.post("/api/guides", status_code=201)
    def create_guide(body: GuideCreate):
        unknown = [t for t in body.draft.topics if t not in TOPICS]
        if unknown or (body.draft.check and body.draft.check not in CHECKS):
            what = ", ".join(unknown or [str(body.draft.check)])
            raise ApiError(422, "INVALID_GUIDE", f"모르는 분야나 자동 검사입니다: {what}")
        entry = guides.create(body.draft, origin=body.origin, by=body.by.strip() or "사용자", adopt=body.adopt,
                              ai_text=body.ai_text)
        return entry.model_dump(mode="json")

    @router.put("/api/guides/{entry_id}")
    def update_guide(entry_id: str, body: GuideUpdate):
        if any(t not in TOPICS for t in body.draft.topics) or (body.draft.check and body.draft.check not in CHECKS):
            raise ApiError(422, "INVALID_GUIDE", "모르는 분야나 자동 검사입니다.")
        return guides.update(entry_id, body.draft, by=body.by.strip() or "사용자").model_dump(mode="json")

    @router.post("/api/guides/{entry_id}/decision")
    def decide_guide(entry_id: str, body: GuideDecision):
        return guides.decide(entry_id, body.status, by=body.by.strip(), note=body.note).model_dump(mode="json")

    @router.delete("/api/guides/{entry_id}", status_code=204)
    def delete_guide(entry_id: str):
        guides.delete(entry_id, by="사용자")

    @router.get("/api/guides/{entry_id}/history")
    def guide_history(entry_id: str):
        guides.get(entry_id)
        return {"events": guides.history(entry_id)}

    @router.get("/api/guides-history")
    def all_history(limit: int = 200):
        return {"events": list(reversed(guides.history(limit=max(1, min(limit, 2000)))))}

    @router.post("/api/guides-import")
    def import_guides(body: GuideImport):
        """A guide document people wrote (Markdown like the export, a bullet list, or CSV)."""
        is_csv = body.filename.lower().endswith((".csv", ".tsv"))
        drafts, warnings = (parse_guide_csv if is_csv else parse_guide_markdown)(body.text)
        if not drafts:
            raise ApiError(422, "NO_GUIDES_FOUND", "가져올 지침을 찾지 못했습니다.",
                           recovery_hint="「## 분야」 아래 「- [필수] 제목: 할 일」 목록, 앱에서 내보낸 Markdown, 또는 "
                                         "제목·지침·이유·강도·분야 열이 있는 CSV를 쓰세요.")
        result = guides.import_drafts(drafts, by=body.by.strip() or "사용자", adopt=body.adopt,
                                      source_name=body.filename)
        return {**result, "warnings": warnings, "found": len(drafts)}

    @router.get("/api/guides-export.md", response_class=PlainTextResponse)
    def export_markdown():
        text = to_markdown(guides.entries(), generated_at=now_iso())
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": 'attachment; filename="design-guide.md"'})

    # --- per board -------------------------------------------------------------------------------

    @router.get(BOARD + "/guides")
    def board_guides(project_id: str, board_id: str, proposal_id: str | None = None):
        return board_checklist(store, proposals, guides, settings, project_id, board_id, proposal_id)

    @router.post(BOARD + "/guides/draft", status_code=202)
    def draft(project_id: str, board_id: str, body: GuideDraftRequest):
        """AI turns a person's feedback into a guide draft. Nothing is saved: the person edits it, then saves it
        as a candidate (text drafted by the AI is never adopted without a separate decision)."""
        store.get_board(project_id, board_id)
        cfg = access.settings()
        provider = access.provider_for(cfg)
        circuit = None
        if body.include_circuit:
            if body.proposal_id:
                circuit = proposals.candidate(proposals.get(project_id, board_id, body.proposal_id))
            else:
                circuit, _ = store.get_circuit(project_id, board_id)
        context = circuit_text(circuit) if circuit is not None else ""
        existing = "\n".join(f"- {e.id} {e.title}" for e in guides.entries() if e.status != "retired")

        def run(job_id: str) -> dict:
            record = run_record(job_id, "guide_draft", cfg, {"feedback_chars": len(body.feedback),
                                                             "circuit": circuit is not None,
                                                             "proposal": body.proposal_id})
            try:
                result, call = draft_guide(provider, feedback=body.feedback, context=context, existing=existing)
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message})
                guides.save_ai_run(record)
                raise api_error(exc) from exc
            record.update(status="succeeded", usage=call.usage, output={"title": result["title"]})
            guides.save_ai_run(record)
            return {"draft": result, "model": call.model, "feedback": body.feedback,
                    "source": {"kind": "feedback", "text": f"사용자 피드백: {body.feedback.strip()[:300]}",
                               "ref": {"project_id": project_id, "board_id": board_id,
                                       **({"proposal_id": body.proposal_id} if body.proposal_id else {})}}}

        return jobs.submit(project_id, "guide_draft", run, board_id=board_id)

    @router.get(BOARD + "/guides/draft/preview")
    def draft_preview(project_id: str, board_id: str, proposal_id: str | None = None):
        """What a draft request sends (feedback text, circuit summary size, guide titles), so the user can decide."""
        store.get_board(project_id, board_id)
        cfg = access.settings()
        circuit = proposals.candidate(proposals.get(project_id, board_id, proposal_id)) if proposal_id \
            else store.get_circuit(project_id, board_id)[0]
        return {"provider": cfg.provider, "model": cfg.model, "enabled": cfg.enabled,
                "circuit_chars": len(circuit_text(circuit)) if circuit is not None else 0,
                "guide_titles": len([e for e in guides.entries() if e.status != "retired"])}

    return router

