"""Change proposals: the single approval path (plan v1.2 FR-16, Phase 2a).

    boards/<board>/proposals/<id>/
        proposal.json           what is proposed, by whom, against which revision, status, evidence
        candidate.circuit.json  the circuit as it would be after approval
        outputs/                KiCad files generated from the candidate for checking (never the board's)

Nothing here writes the board's circuit, revisions or outputs except :func:`ProposalStore.approve`,
which saves the candidate as a new revision through the normal store — and only if the board is
still at the revision the proposal was made against.
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from circuit_core import CircuitIR
from circuit_core.datasheet_checks import check_component
from circuit_core.diff import diff_circuits
from circuit_core.export import canonical_json
from circuit_core.patch import PatchError, PatchOp, apply_patch
from kicad_adapter import KiCadCli, SchematicGenerationError, SymbolLibrary, SymbolLibraryError, generate_schematic

from .errors import ApiError, issue_dict
from .schemas import AutomatedCheck, PageOutput
from .store import ProjectStore, atomic_write, kicad_project_name, now_iso

ProposalSource = Literal["user", "ai_design", "ai_revision", "ai_pdf_import", "template", "agent",
                         "kicad_layout", "kicad_edit"]
ProposalStatus = Literal["checking", "ready", "invalid", "check_failed", "approved", "rejected", "stale"]
OPEN = ("checking", "ready", "invalid", "check_failed")


class ProposalEvidence(BaseModel):
    checked_at: str
    ir_issues: list[dict] = Field(default_factory=list)
    blocking: bool = False
    """IR rule errors: such a circuit cannot be saved, so the proposal cannot be approved."""
    generation_error: dict | None = None
    automated_check: AutomatedCheck | None = None
    power_flags: list[str] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    pages: list[PageOutput] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    datasheets: list[dict] = Field(default_factory=list)


class Proposal(BaseModel):
    id: str
    project_id: str
    board_id: str
    title: str
    rationale: str | None = None
    source: ProposalSource
    base_revision: int | None
    ops: list[PatchOp]
    status: ProposalStatus
    created_at: str
    updated_at: str
    decided_at: str | None = None
    decision_note: str | None = None
    result_revision: int | None = None
    diff: dict
    evidence: ProposalEvidence | None = None
    ai: dict | None = Field(default=None, description="AI metadata: model, assumptions, questions, citations, review.")
    template: dict | None = Field(default=None, description="Template placed: ref, title, params, basis, checklist.")
    agent: dict | None = Field(default=None, description="Agent run that submitted the proposal: run id, scope.")
    parent: str | None = Field(default=None, description="The open proposal this one fixes (a follow-up).")
    diff_from_parent: dict | None = None


class ProposalStore:
    def __init__(self, store: ProjectStore):
        self.store = store
        self._lock = threading.RLock()

    def _dir(self, project_id: str, board_id: str) -> Path:
        return self.store.board_dir(project_id, board_id) / "proposals"

    def _path(self, project_id: str, board_id: str, pid: str) -> Path:
        if not pid.isdigit():
            raise ApiError(404, "NOT_FOUND", f"Proposal '{pid}' not found.")
        return self._dir(project_id, board_id) / pid

    def _save(self, p: Proposal) -> Proposal:
        p.updated_at = now_iso()
        atomic_write(self._path(p.project_id, p.board_id, p.id) / "proposal.json", p.model_dump_json(indent=2) + "\n")
        return p

    # --- create / read ----------------------------------------------------------------------

    def create(self, project_id: str, board_id: str, *, title: str, ops: list, source: ProposalSource,
               rationale: str | None = None, ai: dict | None = None, template: dict | None = None,
               agent: dict | None = None, base_revision: int | None = -1, parent: str | None = None) -> Proposal:
        """``base_revision`` (when given) must still be the board's revision, so a proposal built from an older
        read (an agent run) is refused instead of overwriting newer work."""
        self.store.get_board(project_id, board_id)
        current, revision = self.store.get_circuit(project_id, board_id)
        if base_revision != -1 and base_revision != revision:
            raise ApiError(409, "STALE_BASE", f"The board changed (revision {revision}) since revision {base_revision} "
                           "this change was built on.", recovery_hint="Run it again on the current revision.")
        try:
            candidate = apply_patch(current, ops)
        except PatchError as exc:
            raise ApiError(422, "PATCH_FAILED", f"The change cannot be applied: {exc}",
                           details={"failures": [f.model_dump() for f in exc.failures]}) from exc
        if candidate.project_id != project_id:
            candidate = candidate.model_copy(update={"project_id": project_id})
        with self._lock:
            folder = self._dir(project_id, board_id)
            folder.mkdir(parents=True, exist_ok=True)
            numbers = [int(d.name) for d in folder.iterdir() if d.is_dir() and d.name.isdigit()]
            pid = f"{max(numbers, default=0) + 1:04d}"
            ts = now_iso()
            p = Proposal(id=pid, project_id=project_id, board_id=board_id, title=title.strip() or "변경 제안",
                         rationale=rationale, source=source, base_revision=revision, ops=ops, status="checking",
                         created_at=ts, updated_at=ts, diff=diff_circuits(current, candidate), ai=ai,
                         template=template, agent=agent, parent=parent)
            if parent is not None:
                p.diff_from_parent = diff_circuits(self.candidate(self.get(project_id, board_id, parent)), candidate)
            atomic_write(self._path(project_id, board_id, pid) / "candidate.circuit.json", canonical_json(candidate))
            return self._save(p)

    def get(self, project_id: str, board_id: str, pid: str) -> Proposal:
        path = self._path(project_id, board_id, pid) / "proposal.json"
        if not path.is_file():
            raise ApiError(404, "NOT_FOUND", f"Proposal '{pid}' not found.")
        p = Proposal.model_validate_json(path.read_text(encoding="utf-8"))
        return self._refresh_stale(p)

    def list(self, project_id: str, board_id: str) -> list[Proposal]:
        self.store.get_board(project_id, board_id)
        folder = self._dir(project_id, board_id)
        if not folder.is_dir():
            return []
        out = []
        for d in sorted((d for d in folder.iterdir() if d.is_dir() and d.name.isdigit()), key=lambda d: d.name,
                        reverse=True):
            try:
                out.append(self.get(project_id, board_id, d.name))
            except (ApiError, ValueError):
                continue
        return out

    def candidate(self, p: Proposal) -> CircuitIR:
        path = self._path(p.project_id, p.board_id, p.id) / "candidate.circuit.json"
        return CircuitIR.model_validate_json(path.read_text(encoding="utf-8"))

    def outputs_dir(self, p: Proposal) -> Path:
        return self._path(p.project_id, p.board_id, p.id) / "outputs"

    def _refresh_stale(self, p: Proposal) -> Proposal:
        if p.status in OPEN:
            current = self.store.get_board(p.project_id, p.board_id).current_revision
            if current != p.base_revision:
                with self._lock:
                    p.status = "stale"
                    p.decision_note = (f"보드가 revision {p.base_revision or '없음'}에서 {current}(으)로 바뀌어 이 제안은 "
                                       "더 이상 적용할 수 없습니다.")
                    self._save(p)
        return p

    def update(self, p: Proposal, **fields) -> Proposal:
        with self._lock:
            for k, v in fields.items():
                setattr(p, k, v)
            return self._save(p)

    # --- decide -----------------------------------------------------------------------------

    def approve(self, project_id: str, board_id: str, pid: str, *, accept_failed_checks: bool = False):
        with self._lock:
            p = self.get(project_id, board_id, pid)
            if p.status == "stale":
                raise ApiError(409, "STALE_PROPOSAL", p.decision_note or "The board changed since this proposal.",
                               recovery_hint="같은 변경을 현재 회로에 대해 다시 제안하세요.")
            if p.status in ("approved", "rejected"):
                raise ApiError(409, "ALREADY_DECIDED", f"This proposal was already {p.status}.")
            if p.status == "checking":
                raise ApiError(409, "STILL_CHECKING", "The proposal is still being checked.")
            if p.status == "invalid":
                raise ApiError(409, "INVALID_PROPOSAL", "The proposed circuit breaks IR rules and cannot be saved.",
                               recovery_hint="Reject it, or create a corrected proposal.")
            check = p.evidence.automated_check if p.evidence else None
            passed = check is not None and check.status == "passed"
            if not passed and not accept_failed_checks:
                raise ApiError(409, "CHECKS_NOT_PASSED",
                               "Automated checks did not pass (or did not run) for this proposal.",
                               recovery_hint="Review the evidence; approve again with accept_failed_checks to "
                                             "save it anyway.")
            info, _ = self.store.save_circuit(project_id, board_id, self.candidate(p), source="proposal",
                                              note=f"제안 #{int(p.id)}: {p.title}"[:200])
            return self.update(p, status="approved", decided_at=now_iso(), result_revision=info.revision,
                               decision_note=None if passed else "자동 검사를 통과하지 못한 상태에서 승인"), info

    def reject(self, project_id: str, board_id: str, pid: str, note: str | None = None) -> Proposal:
        with self._lock:
            p = self.get(project_id, board_id, pid)
            if p.status in ("approved", "rejected"):
                raise ApiError(409, "ALREADY_DECIDED", f"This proposal was already {p.status}.")
            return self.update(p, status="rejected", decided_at=now_iso(), decision_note=note)


# --- checking ------------------------------------------------------------------------------------

def check_proposal(proposals: ProposalStore, p: Proposal, *, checker, symbol_dirs: list[Path],
                   kicad_factory: Callable[[], KiCadCli | None], datasheets, run_kicad_checks) -> Proposal:
    """IR rules + library pins, then KiCad generation, ERC and netlist comparison in the proposal's
    own outputs/ folder, then datasheet checks. Records the evidence; never touches the board."""
    candidate = proposals.candidate(p)
    _, report = checker.check(candidate.model_dump(mode="json"))
    ir_issues = report["issues"]
    blocking = any(i["severity"] == "error" and i.get("level") == "ir" for i in ir_issues)
    evidence = ProposalEvidence(checked_at=now_iso(), ir_issues=ir_issues, blocking=blocking)
    out = proposals.outputs_dir(p)
    shutil.rmtree(out, ignore_errors=True)
    if not blocking:
        if not symbol_dirs:
            evidence.generation_error = {"error_code": "NO_SYMBOL_LIBRARY",
                                         "message": "No KiCad symbol library folder is configured."}
        else:
            kproj = kicad_project_name(p.project_id, p.board_id)
            lib = SymbolLibrary(symbol_dirs)
            try:
                generated = generate_schematic(candidate, lib, project_name=kproj)
            except SchematicGenerationError as exc:
                evidence.generation_error = {"error_code": "GENERATION_FAILED",
                                             "message": "The proposed circuit cannot be turned into a schematic.",
                                             "issues": [issue_dict(i) for i in exc.issues]}
            else:
                out.mkdir(parents=True, exist_ok=True)
                sch = generated.write(out)
                evidence.automated_check = run_kicad_checks(candidate, generated, sch, kicad_factory)
                evidence.power_flags = generated.power_flags
                evidence.warnings = [issue_dict(w) for w in generated.warnings]
                for leftover in out.glob("*.kicad_prl"):
                    leftover.unlink()
                for page in generated.pages:
                    svg = f"{kproj}.svg" if page.number == 1 else f"{kproj}-{page.name}.svg"
                    evidence.pages.append(PageOutput(
                        number=page.number, sheet_id=page.sheet_id, name=page.name, schematic=page.filename,
                        svg=svg if (out / svg).is_file() else None, paper=page.paper,
                        bbox_mm=list(page.bbox) if page.bbox else None))
                evidence.files = sorted(f.name for f in out.iterdir() if f.is_file())
        evidence.datasheets = datasheet_evidence(candidate, datasheets, symbol_dirs)
    status = "invalid" if blocking else "ready"
    return proposals.update(p, status=status, evidence=evidence)


def datasheet_evidence(circuit: CircuitIR, datasheets, symbol_dirs: list[Path]) -> list[dict]:
    lib = SymbolLibrary(symbol_dirs) if symbol_dirs else None
    rows = []
    for comp in circuit.components:
        url = lib.datasheet_url(comp.symbol_id) if lib else None
        entry = datasheets.find(comp, url)
        facts = datasheets.facts(entry) if entry else None
        if facts is None:
            continue
        pins = None
        if lib:
            try:
                pins = [(x.number, x.name) for x in lib.get(comp.symbol_id).pins]
            except SymbolLibraryError:
                pins = None
        r = check_component(circuit, comp, facts, pins, [])
        rows.append({"reference": comp.reference, "datasheet": entry.id,
                     "reviewed": bool(entry.summary and entry.summary.reviewed),
                     "pin_map": r["pin_map"]["status"], "ratings": r["ratings"]["status"],
                     "external": r["external"]["status"],
                     "external_missing": [f"{x['kind_label']} {x['value'] or ''} ({' ~ '.join(x['endpoints'])})".strip()
                                          for x in r["external"]["rows"] if x["status"] in ("missing", "short",
                                                                                            "value_differs",
                                                                                            "spec_differs")],
                     "ratings_problems": [f"{x['name']} {x['net']} {x['voltage']} V: {x['status']}"
                                          for x in r["ratings"]["rows"] if x["status"] in ("over_abs_max",
                                                                                           "outside_recommended")]})
    return rows


def proposal_dict(p: Proposal) -> dict:
    return json.loads(p.model_dump_json())
