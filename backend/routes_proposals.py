"""Board proposals: create from patch operations, check in isolation, approve or reject (Phase 2a)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import TypeAdapter, ValidationError

from circuit_core.models import CircuitIR
from circuit_core.patch import PatchOp, apply_patch
from circuit_core.quick_fixes import fix_ops, floating_pins
from kicad_adapter import SymbolLibrary, SymbolLibraryError

from .config import Settings
from .datasheets import DatasheetLibrary
from .errors import ApiError
from .exports import _run_checks, run_export
from .jobs import JobRunner
from .proposals import ProposalStore, check_proposal, datasheet_evidence, proposal_dict
from .schemas import ProposalApprove, ProposalCreate, ProposalReject, QuickFixApply
from .store import ProjectStore

BOARD = "/api/projects/{project_id}/boards/{board_id}"
OPS = TypeAdapter(list[PatchOp])


def parse_ops(raw: list) -> list:
    try:
        return OPS.validate_python(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise ApiError(422, "INVALID_PATCH", f"Invalid patch operation at {'.'.join(str(x) for x in err['loc'])}: "
                       f"{err['msg']}") from exc


def pin_types_for(circuit: CircuitIR, symbol_dirs) -> dict[tuple[str, str], str]:
    """Electrical type of every pin, from the KiCad library (empty when the library is not there)."""
    lib = SymbolLibrary(symbol_dirs) if symbol_dirs else None
    out: dict[tuple[str, str], str] = {}
    for comp in circuit.components:
        try:
            sym = lib.get(comp.symbol_id) if lib else None
        except SymbolLibraryError:
            sym = None
        for p in comp.pins:
            lp = sym.pin(p.number) if sym else None
            if lp is not None:
                out[(comp.id, p.number)] = lp.electrical_type
    return out


class ProposalService:
    """Creating a proposal always starts its isolated check; the AI routes use this too."""

    def __init__(self, store: ProjectStore, jobs: JobRunner, proposals: ProposalStore, datasheets: DatasheetLibrary,
                 settings: Settings, checker):
        self.store, self.jobs, self.proposals = store, jobs, proposals
        self.datasheets, self.settings, self.checker = datasheets, settings, checker

    def check_now(self, p):
        """Run the check in the caller's thread (used inside other jobs)."""
        try:
            return check_proposal(self.proposals, self.proposals.get(p.project_id, p.board_id, p.id),
                                  checker=self.checker, symbol_dirs=self.settings.symbol_dirs,
                                  kicad_factory=self.settings.kicad_factory, datasheets=self.datasheets,
                                  run_kicad_checks=_run_checks)
        except Exception:
            self.proposals.update(self.proposals.get(p.project_id, p.board_id, p.id), status="check_failed")
            raise

    def submit_check(self, p):
        def run(job_id: str) -> dict:
            done = self.check_now(p)
            return {"proposal": done.id, "status": done.status}
        return self.jobs.submit(p.project_id, "proposal_check", run, board_id=p.board_id)

    def create_and_check(self, project_id: str, board_id: str, **kwargs):
        p = self.proposals.create(project_id, board_id, **kwargs)
        return p, self.submit_check(p)


def build_proposal_router(service: ProposalService) -> APIRouter:
    router = APIRouter()
    store, jobs, proposals, settings = service.store, service.jobs, service.proposals, service.settings
    create_and_check, submit_check = service.create_and_check, service.submit_check

    @router.get(BOARD + "/proposals")
    def list_proposals(project_id: str, board_id: str):
        return [proposal_dict(p) for p in proposals.list(project_id, board_id)]

    @router.post(BOARD + "/proposals/{pid}/recheck-datasheets")
    def recheck_datasheets(project_id: str, board_id: str, pid: str):
        """Compare the proposal's circuit with the datasheet summaries again (after new datasheets or summaries).
        Only the datasheet part of the evidence changes; KiCad checks are not re-run."""
        p = proposals.get(project_id, board_id, pid)
        if p.evidence is None:
            raise ApiError(409, "NOT_CHECKED", "아직 검사 중인 제안입니다. 검사가 끝난 뒤 다시 시도하세요.")
        rows = datasheet_evidence(proposals.candidate(p), service.datasheets, settings.symbol_dirs)
        p = proposals.update(p, evidence=p.evidence.model_copy(update={"datasheets": rows}))
        return proposal_dict(p)

    @router.post(BOARD + "/proposals", status_code=201)
    def create(project_id: str, board_id: str, body: ProposalCreate):
        p, job = create_and_check(project_id, board_id, title=body.title, ops=parse_ops(body.ops), source="user",
                                  rationale=body.rationale)
        return {"proposal": proposal_dict(p), "job": job.model_dump()}

    # --- fixes that need no AI ----------------------------------------------------------------

    def pin_types(circuit: CircuitIR) -> dict[tuple[str, str], str]:
        return pin_types_for(circuit, settings.symbol_dirs)

    def fix_target(project_id: str, board_id: str, proposal_id: str | None):
        if proposal_id is None:
            circuit, _ = store.get_circuit(project_id, board_id)
            if circuit is None:
                raise ApiError(409, "NO_CIRCUIT", "This board has no circuit yet.")
            return circuit, None
        p = proposals.get(project_id, board_id, proposal_id)
        return proposals.candidate(p), p

    @router.get(BOARD + "/quick-fixes")
    def quick_fixes(project_id: str, board_id: str, proposal_id: str | None = None):
        circuit, _ = fix_target(project_id, board_id, proposal_id)
        return {"fixes": [f.to_dict() for f in floating_pins(circuit, pin_types(circuit))],
                "nets": sorted(n.name for n in circuit.nets)}

    @router.post(BOARD + "/quick-fixes", status_code=201)
    def apply_quick_fixes(project_id: str, board_id: str, body: QuickFixApply):
        circuit, parent = fix_target(project_id, board_id, body.proposal_id)
        if parent is not None and parent.status not in ("ready", "invalid", "check_failed"):
            raise ApiError(409, "NOT_FIXABLE", f"A proposal that is {parent.status} cannot be fixed.",
                           recovery_hint="Fix the saved circuit, or an open proposal.")
        fixes = {f.id: f for f in floating_pins(circuit, pin_types(circuit))}
        nets = {n.name for n in circuit.nets}
        ops, texts, taken = [], [], set()
        work = circuit
        for choice in body.choices:
            fix = fixes.get(choice.fix_id)
            if fix is None:
                raise ApiError(409, "FIX_NOT_FOUND", f"'{choice.fix_id}' is not a problem of this circuit any more.",
                               recovery_hint="Refresh: the circuit changed.")
            if choice.net is not None and choice.net not in nets and choice.net not in {o.net for o in fix.options}:
                raise ApiError(422, "UNKNOWN_NET", f"Net '{choice.net}' is not in this circuit.")
            try:
                step, text = fix_ops(work, fix, choice.kind, choice.net, choice.value, taken)
            except ValueError as exc:
                raise ApiError(422, "INVALID_FIX", str(exc)) from exc
            work = apply_patch(work, step)
            ops += step
            texts.append(text)
        title = ", ".join(texts) if len(texts) <= 2 else f"{texts[0]} 외 {len(texts) - 1}건"
        if parent is not None:
            title = f"{title} (제안 #{int(parent.id)} 수정)"
            p, job = create_and_check(project_id, board_id, title=title[:200], ops=[*parent.ops, *ops], source="user",
                                      rationale="검사에서 떠 있는 입력 핀을 고친 수정안입니다: " + "; ".join(texts),
                                      base_revision=parent.base_revision, parent=parent.id)
        else:
            p, job = create_and_check(project_id, board_id, title=title[:200], ops=ops, source="user",
                                      rationale="검사에서 떠 있는 입력 핀을 고칩니다: " + "; ".join(texts))
        return {"proposal": proposal_dict(p), "job": job.model_dump()}

    @router.get(BOARD + "/proposals/{pid}")
    def get(project_id: str, board_id: str, pid: str):
        p = proposals.get(project_id, board_id, pid)
        return {**proposal_dict(p), "candidate": proposals.candidate(p).model_dump(mode="json")}

    @router.post(BOARD + "/proposals/{pid}/check", status_code=202)
    def recheck(project_id: str, board_id: str, pid: str):
        p = proposals.get(project_id, board_id, pid)
        if p.status not in ("ready", "invalid", "check_failed"):
            raise ApiError(409, "NOT_CHECKABLE", f"A proposal that is {p.status} cannot be checked again.")
        proposals.update(p, status="checking")
        return submit_check(p)

    @router.post(BOARD + "/proposals/{pid}/approve")
    def approve(project_id: str, board_id: str, pid: str, body: ProposalApprove):
        p, info = proposals.approve(project_id, board_id, pid, accept_failed_checks=body.accept_failed_checks)
        job = None
        if body.generate:
            # A layout taken from the KiCad file replaces that file: its edits are in the circuit now.
            force = p.source in ("kicad_layout", "kicad_edit")
            job = jobs.submit(project_id, "export", lambda job_id: run_export(
                store, project_id, board_id, symbol_dirs=settings.symbol_dirs, kicad_factory=settings.kicad_factory,
                force=force, run_id=job_id), board_id=board_id)
        return {"proposal": proposal_dict(p), "revision": info.model_dump(), "job": job.model_dump() if job else None}

    @router.post(BOARD + "/proposals/{pid}/reject")
    def reject(project_id: str, board_id: str, pid: str, body: ProposalReject):
        return proposal_dict(proposals.reject(project_id, board_id, pid, body.note))

    @router.get(BOARD + "/proposals/{pid}/files/{name}")
    def proposal_file(project_id: str, board_id: str, pid: str, name: str):
        p = proposals.get(project_id, board_id, pid)
        if not p.evidence or name not in p.evidence.files:
            raise ApiError(404, "NOT_FOUND", f"'{name}' is not a file of this proposal.")
        media = "image/svg+xml" if name.endswith(".svg") else "text/plain; charset=utf-8"
        return FileResponse(proposals.outputs_dir(p) / name, media_type=media)

    return router
