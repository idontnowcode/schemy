"""Research notes (연구노트): what was checked, learned and should be applied next time, written so a colleague can read
and reuse it - like an engineer's lab notebook kept during a circuit review.

A note is tied to a board revision (or a proposal) but lives in the workspace notebook, so it is found again on the
next project. The AI can write a draft during a review; a person reads it, edits it and marks it reviewed.

Grounding: before the AI writes, the app lists the evidence it has as numbered items (E1, E2, ...): calculated
checks, datasheet checks and notes with their page numbers, guide checklist rows, design decisions, KiCad results,
the AI review. Every statement in a note cites those numbers; the app keeps only numbers that exist and shows
each cited item next to the statement. A statement with no evidence is shown as the AI's general knowledge, to be
checked. Page numbers can only come from a datasheet summary, never from the AI.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

NoteStatus = Literal["draft", "reviewed"]
AuthorKind = Literal["ai", "person"]
CheckStatus = Literal["ok", "issue", "unknown"]

CHECK_LABEL = {"ok": "문제 없음", "issue": "확인 필요", "unknown": "판단 못 함"}
STATUS_LABEL = {"draft": "초안", "reviewed": "검토함"}
EVIDENCE_KIND_LABEL = {"calc": "계산", "datasheet": "데이터시트", "guide": "설계 지침", "decision": "설계 결정",
                       "kicad": "KiCad 검사", "ai_review": "AI 검토", "requirement": "요구사항", "check": "자동 검사"}


class Evidence(BaseModel):
    """One thing the app knows about the circuit, shown to the AI as ``[E3]`` and kept with the note."""

    id: str = Field(pattern=r"^E\d{1,3}$")
    kind: str
    text: str = Field(max_length=600)


class NoteLine(BaseModel):
    """A statement in the note with the evidence it rests on. ``by`` is who wrote this line."""

    text: str = Field(min_length=1, max_length=1000)
    evidence: list[str] = Field(default_factory=list, description="Evidence ids (E1, E2, ...) of this note.")
    by: AuthorKind = "ai"


class NoteCheck(BaseModel):
    what: str = Field(min_length=1, max_length=300)
    how: str = Field(default="", max_length=600, description="How it was checked: the calculation, the comparison.")
    result: str = Field(default="", max_length=600)
    status: CheckStatus = "unknown"
    evidence: list[str] = Field(default_factory=list)
    by: AuthorKind = "ai"


class NoteApply(BaseModel):
    """Something to do on the next design. It can be turned into a design-guide candidate (``guide_id``)."""

    text: str = Field(min_length=1, max_length=600)
    why: str = Field(default="", max_length=600)
    evidence: list[str] = Field(default_factory=list)
    by: AuthorKind = "ai"
    guide_id: str | None = None


class NoteDraft(BaseModel):
    """The written part of a note (what a person edits or the AI writes)."""

    title: str = Field(min_length=2, max_length=150)
    summary: str = Field(default="", max_length=1500)
    background: str = Field(default="", max_length=3000)
    parts: list[str] = Field(default_factory=list, max_length=30)
    topics: list[str] = Field(default_factory=list, max_length=10)
    checks: list[NoteCheck] = Field(default_factory=list, max_length=40)
    learnings: list[NoteLine] = Field(default_factory=list, max_length=30)
    apply_next: list[NoteApply] = Field(default_factory=list, max_length=30)
    open_questions: list[NoteLine] = Field(default_factory=list, max_length=30)


class NoteContext(BaseModel):
    project_id: str | None = None
    project_name: str | None = None
    board_id: str | None = None
    board_name: str | None = None
    revision: int | None = None
    proposal_id: str | None = None


class ResearchNote(NoteDraft):
    id: str = Field(pattern=r"^N-\d{4}$")
    status: NoteStatus = "draft"
    author_kind: AuthorKind
    written_by: str = Field(description="The person's name, or 'AI (model)'.")
    created_at: str
    updated_at: str
    version: int = 1
    edited_by: list[str] = Field(default_factory=list)
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    context: NoteContext = Field(default_factory=NoteContext)
    evidence: list[Evidence] = Field(default_factory=list)
    """Everything the AI was shown, so each cited number can be read later even after the circuit changed."""


def keep_known(ids, known: set[str]) -> list[str]:
    """Evidence ids that exist in this note, in order, once each (anything else the AI wrote is dropped)."""
    out: list[str] = []
    for raw in ids or []:
        i = str(raw).strip().upper().strip("[]")
        if i in known and i not in out:
            out.append(i)
    return out


def _refs(ids: list[str], evidence: dict[str, Evidence]) -> str:
    if not ids:
        return ""
    return " (근거: " + ", ".join(f"{i} {EVIDENCE_KIND_LABEL.get(evidence[i].kind, evidence[i].kind)}"
                                   for i in ids if i in evidence) + ")"


def _basis(line: NoteLine, evidence: dict[str, Evidence]) -> str:
    """The evidence a lesson rests on; a lesson the AI wrote without any is marked as general knowledge."""
    refs = _refs(line.evidence, evidence)
    if not refs and line.by == "ai":
        return " (근거 없음: AI의 일반 지식, 확인 필요)"
    return refs


def _who(by: AuthorKind, author: AuthorKind) -> str:
    return " — 사람이 고침" if by == "person" and author == "ai" else ""


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ") or "—"


def to_markdown(note: ResearchNote, *, topic_labels: dict[str, str] | None = None) -> str:
    """The note as a Markdown page anyone can read: header, summary, background, checks, lessons, what to apply,
    open questions and the evidence it cites."""
    ev = {e.id: e for e in note.evidence}
    ctx = note.context
    board = ctx.board_name or ctx.board_id
    board = (board if board.startswith("보드") else f"보드 {board}") if board else None
    where = " / ".join(x for x in (ctx.project_name, board,
                                   f"revision {ctx.revision}" if ctx.revision is not None else None,
                                   f"제안 #{int(ctx.proposal_id)}" if ctx.proposal_id and ctx.proposal_id.isdigit()
                                   else (f"제안 {ctx.proposal_id}" if ctx.proposal_id else None)) if x)
    author = note.written_by  # "AI (model)" or the person's name; the status line says whether it is a draft
    status = STATUS_LABEL[note.status] + (f" · {note.reviewed_by} ({note.reviewed_at[:10]})" if note.reviewed_by and
                                          note.reviewed_at else "")
    labels = topic_labels or {}
    lines = [f"# {note.id} {note.title}", "",
             f"- 대상: {where or '—'}",
             f"- 작성: {author} · {note.created_at[:10]}"
             + (f" · 고친 사람: {', '.join(note.edited_by)}" if note.edited_by else ""),
             f"- 상태: {status}"]
    if note.parts:
        lines.append(f"- 부품: {', '.join(note.parts)}")
    if note.topics:
        lines.append(f"- 분야: {', '.join(labels.get(t, t) for t in note.topics)}")
    if note.author_kind == "ai" and note.status == "draft":
        lines += ["", "> AI가 쓴 초안입니다. 근거 번호가 없는 문장은 AI의 일반 지식이므로 확인한 뒤 쓰세요."]
    if note.summary:
        lines += ["", "## 요약", "", note.summary]
    if note.background:
        lines += ["", "## 배경", "", note.background]
    if note.checks:
        lines += ["", "## 확인한 것", "", "| 항목 | 어떻게 확인했나 | 결과 | 판단 | 근거 |", "|---|---|---|---|---|"]
        for c in note.checks:
            lines.append(f"| {_cell(c.what)} | {_cell(c.how)} | {_cell(c.result)} | {CHECK_LABEL[c.status]} | "
                         f"{', '.join(c.evidence) or '없음'}{_who(c.by, note.author_kind)} |")
    if note.learnings:
        lines += ["", "## 배운 점·깨달은 점", ""]
        lines += [f"- {x.text}{_basis(x, ev)}{_who(x.by, note.author_kind)}" for x in note.learnings]
    if note.apply_next:
        lines += ["", "## 다음 설계에 적용할 것", ""]
        for x in note.apply_next:
            why = f" — {x.why}" if x.why else ""
            guide = f" → 설계 지침 {x.guide_id}" if x.guide_id else ""
            lines.append(f"- {x.text}{why}{_refs(x.evidence, ev)}{guide}{_who(x.by, note.author_kind)}")
    if note.open_questions:
        lines += ["", "## 열린 질문 (확인할 것)", ""]
        lines += [f"- {x.text}{_refs(x.evidence, ev)}{_who(x.by, note.author_kind)}" for x in note.open_questions]
    cited = [i for i in _cited(note) if i in ev]
    if cited:
        lines += ["", "## 근거", ""]
        lines += [f"- {i} [{EVIDENCE_KIND_LABEL.get(ev[i].kind, ev[i].kind)}] {ev[i].text}" for i in cited]
    return "\n".join(lines) + "\n"


def _cited(note: NoteDraft) -> list[str]:
    out: list[str] = []
    for group in (note.checks, note.learnings, note.apply_next, note.open_questions):
        for x in group:
            out += [i for i in x.evidence if i not in out]
    return sorted(out, key=lambda i: int(i[1:]))


def notebook_markdown(notes: list[ResearchNote], *, topic_labels: dict[str, str] | None = None,
                      generated_at: str = "") -> str:
    """All notes, newest first, as one document to share."""
    head = ["# 연구노트", "", f"- 노트 {len(notes)}개" + (f" · 내보낸 때 {generated_at[:19]}" if generated_at else ""),
            "- 「초안」은 AI가 쓰고 아직 사람이 확인하지 않은 노트입니다.", ""]
    body = ["#" + to_markdown(n, topic_labels=topic_labels) for n in notes]  # each note one level down
    return "\n".join(head) + "\n---\n\n".join(body)
