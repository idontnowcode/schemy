"""Workspace research notebook (``<workspace>/notes/``) and the evidence list a note is written from.

    notes/
      notes.json      the notes
      history.jsonl   one line per event: created, edited (before/after), reviewed, unreviewed, deleted, guide
      ai_runs/        one record per AI call that wrote a note (no keys)

Like the design guide, the notebook belongs to the workspace: a note written on one board is found again on the
next project. A note the AI wrote stays a *draft* until a person marks it reviewed; a line a person changed is
marked as theirs. Drafts can be deleted; reviewed notes are kept (a notebook is not rewritten after the fact -
edits are versions with history).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path

from circuit_core.engineering import engineering_review
from circuit_core.models import CircuitIR
from circuit_core.notes import Evidence, NoteContext, NoteDraft, ResearchNote, keep_known

from .errors import ApiError
from .store import atomic_write, now_iso

MAX_EVIDENCE = 80


class NoteLibrary:
    def __init__(self, workspace: Path):
        self.dir = Path(workspace) / "notes"
        self.path = self.dir / "notes.json"
        self.history_path = self.dir / "history.jsonl"
        self._lock = threading.RLock()

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("notes", [])
        except ValueError:
            return []

    def _save(self, notes: list[dict]) -> None:
        atomic_write(self.path, json.dumps({"schema_version": 1, "notes": notes}, ensure_ascii=False, indent=2) + "\n")

    def _log(self, event: str, note_id: str, by: str = "", **data) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now_iso(), "event": event, "id": note_id, "by": by, **data},
                                ensure_ascii=False) + "\n")

    def notes(self) -> list[ResearchNote]:
        with self._lock:
            return [ResearchNote.model_validate(n) for n in self._load()]

    def get(self, note_id: str) -> ResearchNote:
        for n in self.notes():
            if n.id == note_id:
                return n
        raise ApiError(404, "NOTE_NOT_FOUND", f"연구노트 '{note_id}'가 없습니다.")

    def _put(self, note: ResearchNote) -> None:
        raw = self._load()
        raw = [note.model_dump(mode="json") if n["id"] == note.id else n for n in raw]
        self._save(raw)

    def create(self, draft: NoteDraft, *, author_kind: str, written_by: str, context: NoteContext,
               evidence: list[Evidence] = ()) -> ResearchNote:
        """A note the AI wrote is a draft; a person's own note counts as reviewed by them."""
        with self._lock:
            raw = self._load()
            nums = [int(m.group(1)) for n in raw if (m := re.fullmatch(r"N-(\d{4})", n["id"]))]
            now = now_iso()
            person = author_kind == "person"
            known = {e.id for e in evidence}
            data = draft.model_dump()
            for group in ("checks", "learnings", "apply_next", "open_questions"):
                for item in data[group]:
                    item["by"] = author_kind
                    item["evidence"] = keep_known(item.get("evidence"), known)
                    if group == "apply_next":
                        item["guide_id"] = None
            note = ResearchNote(**data, id=f"N-{(max(nums) + 1) if nums else 1:04d}",
                                status="reviewed" if person else "draft", author_kind=author_kind,
                                written_by=written_by, created_at=now, updated_at=now,
                                reviewed_by=written_by if person else None, reviewed_at=now if person else None,
                                context=context, evidence=list(evidence))
            raw.append(note.model_dump(mode="json"))
            self._save(raw)
            self._log("created", note.id, written_by, author_kind=author_kind, title=note.title,
                      context=context.model_dump(), evidence=len(evidence))
            return note

    def update(self, note_id: str, draft: NoteDraft, *, by: str) -> ResearchNote:
        """A new version. Lines left exactly as they were keep their author; changed or new lines are the person's."""
        with self._lock:
            old = self.get(note_id)
            known = {e.id for e in old.evidence}
            data = draft.model_dump()
            before = old.model_dump(mode="json")
            for group in ("checks", "learnings", "apply_next", "open_questions"):
                kept = {json.dumps(_content(x), sort_keys=True, ensure_ascii=False): x
                        for x in before[group]}
                for item in data[group]:
                    same = kept.get(json.dumps(_content(item), sort_keys=True, ensure_ascii=False))
                    item["by"] = same["by"] if same else "person"
                    item["evidence"] = same["evidence"] if same else keep_known(item.get("evidence"), known)
                    if group == "apply_next":
                        item["guide_id"] = same.get("guide_id") if same else None
            editors = old.edited_by + ([by] if by and by not in old.edited_by else [])
            note = ResearchNote.model_validate({**before, **NoteDraft.model_validate(data).model_dump(mode="json"),
                                                "version": old.version + 1, "updated_at": now_iso(),
                                                "edited_by": editors})
            self._put(note)
            self._log("edited", note_id, by, version=note.version, before=_content_of(before),
                      after=_content_of(note.model_dump(mode="json")))
            return note

    def review(self, note_id: str, *, by: str, reviewed: bool = True) -> ResearchNote:
        if reviewed and not by.strip():
            raise ApiError(422, "NAME_REQUIRED", "확인한 사람의 이름을 적으세요.")
        with self._lock:
            note = self.get(note_id)
            note = note.model_copy(update={"status": "reviewed" if reviewed else "draft",
                                           "reviewed_by": by.strip() if reviewed else None,
                                           "reviewed_at": now_iso() if reviewed else None, "updated_at": now_iso()})
            self._put(note)
            self._log("reviewed" if reviewed else "unreviewed", note_id, by)
            return note

    def delete(self, note_id: str, *, by: str) -> None:
        with self._lock:
            note = self.get(note_id)
            if note.status == "reviewed":
                raise ApiError(409, "NOTE_REVIEWED", "확인을 마친 노트는 지우지 않습니다. 고쳐서 새 버전으로 남기세요.")
            self._save([n for n in self._load() if n["id"] != note_id])
            self._log("deleted", note_id, by, title=note.title)

    def link_guide(self, note_id: str, index: int, guide_id: str, *, by: str) -> ResearchNote:
        with self._lock:
            note = self.get(note_id)
            if not 0 <= index < len(note.apply_next):
                raise ApiError(404, "NOTE_ITEM_NOT_FOUND", f"노트 {note_id}에 {index + 1}번째 적용 항목이 없습니다.")
            items = [x.model_copy(update={"guide_id": guide_id}) if i == index else x
                     for i, x in enumerate(note.apply_next)]
            note = note.model_copy(update={"apply_next": items, "updated_at": now_iso()})
            self._put(note)
            self._log("guide", note_id, by, index=index, guide_id=guide_id)
            return note

    def history(self, note_id: str | None = None, limit: int = 300) -> list[dict]:
        if not self.history_path.is_file():
            return []
        out = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if note_id is None or event.get("id") == note_id:
                out.append(event)
        return out[-limit:]

    def save_ai_run(self, record: dict) -> Path:
        path = self.dir / "ai_runs" / f"{record['id']}.json"
        atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return path


def _content(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("by", "evidence", "guide_id")}


def _content_of(note: dict) -> dict:
    keys = ("title", "summary", "background", "parts", "topics", "checks", "learnings", "apply_next", "open_questions")
    return {k: note.get(k) for k in keys}


def run_id() -> str:
    return f"{now_iso()[:19].replace(':', '')}-{uuid.uuid4().hex[:6]}"


# --- evidence -------------------------------------------------------------------------------------

class EvidenceList:
    """Numbered evidence items (E1, E2, ...), at most ``MAX_EVIDENCE``, each one short line."""

    def __init__(self) -> None:
        self.items: list[Evidence] = []

    def add(self, kind: str, text: str) -> None:
        text = " ".join(str(text).split())
        if text and len(self.items) < MAX_EVIDENCE and all(e.text != text[:600] for e in self.items):
            self.items.append(Evidence(id=f"E{len(self.items) + 1}", kind=kind, text=text[:600]))


def collect_evidence(circuit: CircuitIR, *, requirements: str, datasheet_rows: list[dict], datasheet_notes: list[str],
                     guide_items: list[dict], kicad: list[str], review: dict | None,
                     proposal_text: str = "") -> list[Evidence]:
    """What the app itself knows about this circuit, most useful first: calculated checks, datasheet checks and
    notes (with their pages), guide checklist rows, design decisions, KiCad results, then the AI review."""
    ev = EvidenceList()
    refs = {c.id: c.reference for c in circuit.components}
    for f in engineering_review(circuit):
        parts = ", ".join(refs.get(c, c) for c in f.components)
        ev.add("calc", f"{f.severity} · {f.title}" + (f" ({parts})" if parts else "") + f": {f.detail}"
               + (f" 계산: {f.calc}" if f.calc else "") + f" [규칙 {f.rule}]")
    for d in datasheet_rows:
        ev.add("datasheet", f"{d['reference']} 데이터시트 대조: 핀맵 {d['pin_map']}, 전압 {d['ratings']}, "
                            f"외부 부품 {d['external']}"
               + (f", 빠지거나 다른 외부 부품: {'; '.join(d['external_missing'])}" if d.get("external_missing") else "")
               + (", 원문 확인함" if d.get("reviewed") else ", 요약을 원문과 대조하지 않음"))
    for line in datasheet_notes:
        ev.add("datasheet", line)
    for g in guide_items:
        if g.get("status") in ("fail", "warn", "exception"):
            ev.add("guide", f"설계 지침 {g['id']} 「{g['title']}」: {g['status']}"
                   + (f" — {' / '.join(g.get('notes') or [])}" if g.get("notes") else ""))
    for d in circuit.decisions:
        ev.add("decision", f"설계 결정 {d.id} 「{d.topic}」: {d.choice} — {d.reason}"
               + (f" (따르지 않은 지침 {', '.join(d.waives)})" if d.waives else ""))
    for line in kicad:
        ev.add("kicad", line)
    if proposal_text.strip():
        for line in proposal_text.strip().splitlines()[:20]:
            ev.add("check", line)
    if review:
        if review.get("summary"):
            ev.add("ai_review", f"AI 검토 요약: {review['summary']}")
        for f in review.get("findings") or []:
            ev.add("ai_review", f"AI 검토 {f.get('severity')} · {f.get('title')}: {f.get('detail', '')}"
                   + (f" (부품 {', '.join(f.get('references') or [])})" if f.get("references") else ""))
    if requirements.strip():
        ev.add("requirement", "요구사항: " + requirements.strip()[:560])
    return ev.items
