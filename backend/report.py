"""The rationale report and the calculated technical review for a board (plan section 10, L3 pre-checks)."""

from __future__ import annotations

from circuit_core.engineering import engineering_review, review_summary
from circuit_core.rationale import rationale_markdown

from .errors import ApiError
from .proposals import ProposalStore
from .reviews import ReviewStore
from .store import ProjectStore


def _proposal_summary(p) -> dict:
    return {"id": p.id, "title": p.title, "source": p.source, "status": p.status, "rationale": p.rationale,
            "decision_note": p.decision_note, "result_revision": p.result_revision, "base_revision": p.base_revision,
            "ai": p.ai, "template": p.template, "diff": p.diff}


def technical_review(store: ProjectStore, project_id: str, board_id: str) -> dict:
    circuit, revision = store.get_circuit(project_id, board_id)
    if circuit is None:
        raise ApiError(409, "NO_CIRCUIT", "This board has no circuit yet.")
    findings = engineering_review(circuit)
    refs = {c.id: c.reference for c in circuit.components}
    return {"revision": revision, "summary": review_summary(findings), "findings": [f.to_dict(refs) for f in findings]}


def rationale_report(store: ProjectStore, project_id: str, board_id: str, *, automated_check: dict | None = None,
                     generated_at: str | None = None, decided_only: bool = False) -> dict:
    """``decided_only`` keeps the report a function of the revision (for the generated outputs): only proposals that
    were approved or rejected on or before it, and no time stamp."""
    circuit, revision = store.get_circuit(project_id, board_id)
    if circuit is None or revision is None:
        raise ApiError(409, "NO_CIRCUIT", "This board has no circuit yet.")
    proposals = sorted(ProposalStore(store).list(project_id, board_id), key=lambda p: int(p.id))
    if decided_only:
        proposals = [p for p in proposals if p.status in ("approved", "rejected")
                     and (p.result_revision or p.base_revision or 0) <= revision]
    findings = engineering_review(circuit)
    md = rationale_markdown(
        circuit, revision=revision, requirements=store.get_requirements(project_id),
        proposals=[_proposal_summary(p) for p in proposals], review=ReviewStore(store).status(project_id, board_id),
        automated_check=automated_check, findings=findings, generated_at=None if decided_only else generated_at)
    return {"revision": revision, "markdown": md, "summary": review_summary(findings)}
