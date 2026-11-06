"""Research notebook API: notes the AI writes while reviewing a circuit, and notes people write themselves.

    GET  /api/notes                       list (filters: project, board, status, text)
    GET  /api/notes/{id}                  one note with its Markdown and history
    POST /api/notes                       a person's own note
    PUT  /api/notes/{id}                  edit (a new version; changed lines become the editor's)
    POST /api/notes/{id}/review           mark reviewed (or back to draft)
    DELETE /api/notes/{id}                drafts only
    POST /api/notes/{id}/apply/{n}/guide  turn "apply next time" item n into a design-guide candidate
    GET  /api/notes/{id}/markdown         download one note; GET /api/notes-export.md the whole notebook
    GET  /api/projects/{p}/boards/{b}/notes          notes of this board
    GET  .../notes/preview                what an AI note sends (evidence count by kind) before the user confirms
    POST .../notes/ai                     the AI writes a draft note (job)
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ai_engine import AiError
from ai_engine.design import circuit_text
from ai_engine.notes import write_note
from circuit_core import validate_circuit
from circuit_core.guides import TOPICS, GuideDraft, GuideSource
from circuit_core.notes import NoteContext, NoteDraft, ResearchNote, notebook_markdown, to_markdown
from kicad_adapter import SymbolLibrary

from .ai_access import AiAccess, api_error, run_record
from .config import Settings
from .datasheets import DatasheetLibrary
from .errors import ApiError
from .exports import read_manifest
from .guides import GuideLibrary
from .jobs import JobRunner
from .notes import NoteLibrary, collect_evidence
from .proposals import ProposalStore, datasheet_evidence
from .store import ProjectStore, now_iso

BOARD = "/api/projects/{project_id}/boards/{board_id}"


class NoteCreate(BaseModel):
    draft: NoteDraft
    by: str = Field(min_length=1, max_length=80)
    project_id: str | None = None
    board_id: str | None = None
    revision: int | None = None


class NoteEdit(BaseModel):
    draft: NoteDraft
    by: str = Field(min_length=1, max_length=80)


class NoteReview(BaseModel):
    by: str = Field(default="", max_length=80)
    reviewed: bool = True


class NoteAiRequest(BaseModel):
    proposal_id: str | None = None
    focus: str = Field(default="", max_length=1000, description="What the note should be about, in the user's words.")


class ByBody(BaseModel):
    by: str = Field(default="", max_length=80)


def summary_of(n: ResearchNote) -> dict:
    return {"id": n.id, "title": n.title, "summary": n.summary, "status": n.status, "author_kind": n.author_kind,
            "written_by": n.written_by, "reviewed_by": n.reviewed_by, "created_at": n.created_at,
            "updated_at": n.updated_at, "version": n.version, "parts": n.parts, "topics": n.topics,
            "context": n.context.model_dump(), "counts": {"checks": len(n.checks), "learnings": len(n.learnings),
                                                          "apply_next": len(n.apply_next),
                                                          "open_questions": len(n.open_questions)}}


def make_note_writer(store: ProjectStore, proposals: ProposalStore, notes: NoteLibrary, guides: GuideLibrary | None,
                     datasheets: DatasheetLibrary, settings: Settings,
                     board_checklist: Callable[..., dict] | None = None,
                     proposal_text: Callable | None = None):
    """``inputs`` gathers what a note is written from; ``write`` asks the AI and saves a draft note."""

    def datasheet_lines(circuit) -> list[str]:
        lib = SymbolLibrary(settings.symbol_dirs) if settings.symbol_dirs else None
        out = []
        for comp in circuit.components:
            entry = datasheets.find(comp, lib.datasheet_url(comp.symbol_id) if lib else None)
            facts = datasheets.facts(entry) if entry else None
            if facts is None:
                continue
            name = f"{comp.reference} {comp.manufacturer_part_number or comp.value}"
            doc = f" ({facts.document})" if facts.document else ""

            def page(p: int | None) -> str:
                return f"p.{p}" if p else "쪽 번호 없음"

            for r in [*facts.abs_max[:4], *facts.recommended[:4]]:
                rng = "~".join(f"{v:g}" for v in (r.min, r.max) if v is not None)
                out.append(f"{name} 데이터시트{doc} {r.parameter} {rng} {r.unit} ({page(r.page)})")
            for e in facts.external_parts[:6]:
                out.append(f"{name} 데이터시트{doc} {'필수' if e.requirement == 'required' else '권장'} 외부 부품: "
                           f"{e.kind_label} {e.value or ''} {' ~ '.join(e.endpoints)} {e.description or ''} "
                           f"({page(e.page)})")
            for n in facts.notes[:6]:
                out.append(f"{name} 데이터시트{doc} 메모: {n.text} ({page(n.page)})")
        return out

    def inputs(project_id: str, board_id: str, proposal_id: str | None, review: dict | None = None):
        if proposal_id:
            p = proposals.get(project_id, board_id, proposal_id)
            circuit, revision = proposals.candidate(p), p.base_revision
            kicad, ptext = [], proposal_text(p) if proposal_text else ""
            review = review or (p.ai or {}).get("review")
        else:
            circuit, revision = store.get_circuit(project_id, board_id)
            if circuit is None:
                raise ApiError(409, "NO_CIRCUIT", "이 보드에는 아직 회로가 없습니다.")
            ptext = ""
            kicad = [f"IR {i.severity.value}: {i.error_code} {i.message}" for i in validate_circuit(circuit).issues]
            manifest = read_manifest(store.board_dir(project_id, board_id) / "outputs")
            if manifest and manifest.source_revision == revision:
                ac = manifest.automated_check
                kicad.insert(0, f"KiCad 자동 검사(revision {revision}): {ac.status}, ERC 오류 {ac.erc_errors} "
                                f"경고 {ac.erc_warnings}")
                kicad += [f"ERC {v.get('severity')}: {v.get('description')}" for v in ac.violations[:10]]
            if review is None:
                path = store.board_dir(project_id, board_id) / "ai_reviews" / "latest.json"
                if path.is_file():
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    review = saved if saved.get("revision") == revision else None
        guide_items = board_checklist(project_id, board_id, proposal_id)["items"] if board_checklist else []
        evidence = collect_evidence(circuit, requirements=store.get_requirements(project_id),
                                    datasheet_rows=datasheet_evidence(circuit, datasheets, settings.symbol_dirs),
                                    datasheet_notes=datasheet_lines(circuit), guide_items=guide_items, kicad=kicad,
                                    review=review, proposal_text=ptext)
        board = store.get_board(project_id, board_id)
        context = NoteContext(project_id=project_id, project_name=store.get_project(project_id).name,
                              board_id=board_id, board_name=board.name, revision=revision, proposal_id=proposal_id)
        return circuit, evidence, context

    def write(cfg, provider, project_id: str, board_id: str, proposal_id: str | None, *, job_id: str,
              review: dict | None = None, focus: str = "") -> ResearchNote:
        circuit, evidence, context = inputs(project_id, board_id, proposal_id, review)
        record = run_record(job_id, "research_note", cfg, {"project_id": project_id, "board_id": board_id,
                                                            "proposal": proposal_id, "revision": context.revision,
                                                            "evidence": len(evidence)})
        try:
            draft, call = write_note(provider, requirements=store.get_requirements(project_id),
                                     circuit=circuit_text(circuit), evidence=evidence, focus=focus,
                                     max_output_tokens=min(cfg.max_output_tokens, 16384))
        except AiError as exc:
            record.update(status="failed", error={"code": exc.code, "message": exc.message})
            notes.save_ai_run(record)
            raise api_error(exc) from exc
        note = notes.create(draft, author_kind="ai", written_by=f"AI ({call.model or cfg.model})", context=context,
                            evidence=evidence)
        record.update(status="succeeded", usage=call.usage, output={"note": note.id})
        notes.save_ai_run(record)
        return note

    write.inputs = inputs
    return write


def build_note_router(store: ProjectStore, jobs: JobRunner, proposals: ProposalStore, notes: NoteLibrary,
                      guides: GuideLibrary, settings: Settings, writer) -> APIRouter:
    router = APIRouter()
    access = AiAccess(store, settings)

    def page(note: ResearchNote) -> dict:
        return {"note": note.model_dump(mode="json"), "markdown": to_markdown(note, topic_labels=TOPICS),
                "history": notes.history(note.id)}

    @router.get("/api/notes")
    def list_notes(project_id: str | None = None, board_id: str | None = None, status: str | None = None,
                   q: str | None = None):
        out = notes.notes()
        if project_id:
            out = [n for n in out if n.context.project_id == project_id]
        if board_id:
            out = [n for n in out if n.context.board_id == board_id]
        if status:
            out = [n for n in out if n.status == status]
        if q and q.strip():
            words = q.lower().split()
            out = [n for n in out if all(w in to_markdown(n, topic_labels=TOPICS).lower() for w in words)]
        out.sort(key=lambda n: (n.created_at, n.id), reverse=True)
        return {"notes": [summary_of(n) for n in out], "topics": TOPICS}

    @router.get("/api/notes-export.md", response_class=PlainTextResponse)
    def export_all():
        newest_first = sorted(notes.notes(), key=lambda n: (n.created_at, n.id), reverse=True)
        text = notebook_markdown(newest_first, topic_labels=TOPICS, generated_at=now_iso())
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": 'attachment; filename="research-notes.md"'})

    @router.get("/api/notes/{note_id}")
    def get_note(note_id: str):
        return page(notes.get(note_id))

    @router.get("/api/notes/{note_id}/markdown", response_class=PlainTextResponse)
    def note_markdown(note_id: str):
        note = notes.get(note_id)
        return PlainTextResponse(to_markdown(note, topic_labels=TOPICS), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{note.id}.md"'})

    @router.post("/api/notes", status_code=201)
    def create_note(body: NoteCreate):
        context = NoteContext()
        if body.project_id:
            context.project_id, context.project_name = body.project_id, store.get_project(body.project_id).name
            if body.board_id:
                b = store.get_board(body.project_id, body.board_id)
                context.board_id, context.board_name, context.revision = body.board_id, b.name, body.revision
        return page(notes.create(body.draft, author_kind="person", written_by=body.by.strip(), context=context))

    @router.put("/api/notes/{note_id}")
    def edit_note(note_id: str, body: NoteEdit):
        return page(notes.update(note_id, body.draft, by=body.by.strip()))

    @router.post("/api/notes/{note_id}/review")
    def review_note(note_id: str, body: NoteReview):
        return page(notes.review(note_id, by=body.by, reviewed=body.reviewed))

    @router.delete("/api/notes/{note_id}", status_code=204)
    def delete_note(note_id: str, by: str = ""):
        notes.delete(note_id, by=by)

    @router.post("/api/notes/{note_id}/apply/{index}/guide", status_code=201)
    def apply_to_guide(note_id: str, index: int, body: ByBody):
        """One "apply next time" item becomes a design-guide *candidate* (a person adopts it in the guide)."""
        note = notes.get(note_id)
        if not 0 <= index < len(note.apply_next):
            raise ApiError(404, "NOTE_ITEM_NOT_FOUND", f"노트 {note_id}에 {index + 1}번째 적용 항목이 없습니다.")
        item = note.apply_next[index]
        if item.guide_id:
            raise ApiError(409, "ALREADY_GUIDE", f"이미 설계 지침 {item.guide_id}로 남겼습니다.")
        title = item.text if len(item.text) <= 120 else item.text[:117] + "…"
        ctx = {k: v for k, v in note.context.model_dump().items() if v is not None and k in
               ("project_id", "board_id", "proposal_id", "revision")}
        draft = GuideDraft(title=title if len(title) >= 2 else note.title[:120], rule=item.text, why=item.why,
                           topics=[t for t in note.topics if t in TOPICS][:1] or ["general"],
                           sources=[GuideSource(kind="review", text=f"연구노트 {note.id} 「{note.title}」"[:500],
                                                ref={**ctx, "note_id": note.id})])
        entry = guides.create(draft, origin="review", by=body.by.strip() or note.written_by, adopt=False,
                              ai_text=item.by == "ai")
        notes.link_guide(note_id, index, entry.id, by=body.by.strip())
        return {"guide": entry.model_dump(mode="json"), **page(notes.get(note_id))}

    # --- per board -------------------------------------------------------------------------------

    @router.get(BOARD + "/notes")
    def board_notes(project_id: str, board_id: str):
        store.get_board(project_id, board_id)
        out = [n for n in notes.notes() if n.context.project_id == project_id and n.context.board_id == board_id]
        out.sort(key=lambda n: (n.created_at, n.id), reverse=True)
        return {"notes": [summary_of(n) for n in out], "topics": TOPICS}

    @router.get(BOARD + "/notes/preview")
    def note_preview(project_id: str, board_id: str, proposal_id: str | None = None):
        """What an AI note would send: the circuit summary, the requirements and the evidence list (counted by kind)."""
        cfg = access.settings()
        circuit, evidence, context = writer.inputs(project_id, board_id, proposal_id)
        return {"provider": cfg.provider, "model": cfg.model, "enabled": cfg.enabled, "revision": context.revision,
                "circuit_chars": len(circuit_text(circuit)), "evidence": len(evidence),
                "evidence_kinds": dict(Counter(e.kind for e in evidence))}

    @router.post(BOARD + "/notes/ai", status_code=202)
    def ai_note(project_id: str, board_id: str, body: NoteAiRequest):
        store.get_board(project_id, board_id)
        cfg = access.settings()
        provider = access.provider_for(cfg)
        writer.inputs(project_id, board_id, body.proposal_id)  # fail now (no circuit) rather than inside the job

        def run(job_id: str) -> dict:
            note = writer(cfg, provider, project_id, board_id, body.proposal_id, job_id=job_id, focus=body.focus)
            return {"note_id": note.id, "title": note.title}

        return jobs.submit(project_id, "research_note", run, board_id=board_id)

    return router
