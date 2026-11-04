"""Requirements -> AI design -> proposal, and AI review (Phase 2b).

Every AI result here ends as a proposal (or as review findings that change nothing). The proposal is
checked in its own folder (IR rules, KiCad ERC and netlist, datasheets) before a person approves it.
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import Field

from ai_engine import AiError
from ai_engine.design import DesignInput, circuit_text, design_circuit, review_circuit, structure_requirements
from circuit_core import CircuitIR, validate_circuit
from circuit_core.patch import ReplaceCircuit
from kicad_adapter import SymbolLibrary

from .ai_access import AiAccess, api_error, run_record
from .config import Settings
from .datasheets import DatasheetLibrary
from .design_resolver import PartResolver, facts_notes, symbol_index
from .errors import ApiError
from .exports import read_manifest
from .inventory_store import InventoryStore
from .jobs import JobRunner
from .proposals import Proposal, datasheet_evidence
from .routes_proposals import ProposalService
from .schemas import _Model
from .store import ProjectStore, atomic_write, now_iso

BOARD = "/api/projects/{project_id}/boards/{board_id}"
MAX_INVENTORY_ROWS = 150


class SpecBody(_Model):
    spec: dict | None


class DesignBody(_Model):
    instructions: str | None = Field(default=None, max_length=10_000)
    use_inventory: bool = True
    review: bool = True


class ReviewBody(_Model):
    proposal_id: str | None = None


class ReviseBody(_Model):
    proposal_id: str | None = None
    instructions: str | None = Field(default=None, max_length=10_000)
    review: bool = True


def proposal_evidence_text(p: Proposal) -> str:
    ev = p.evidence
    if ev is None:
        return "(검사 전)"
    lines = []
    ac = ev.automated_check
    if ac:
        lines.append(f"KiCad 자동 검사: {ac.status}" + (f", ERC 오류 {ac.erc_errors} 경고 {ac.erc_warnings}"
                                                   if ac.erc_errors is not None else "")
                     + (f", Netlist {'일치' if ac.netlist_matches_ir else '불일치'}" if ac.netlist_matches_ir is not None else "")
                     + (f" ({ac.reason})" if ac.reason else ""))
        for v in ac.violations[:20]:
            lines.append(f"- ERC {v.get('severity')}: {v.get('description')} {', '.join(v.get('items') or [])}")
    if ev.generation_error:
        lines.append(f"회로도 생성 실패: {ev.generation_error.get('message')}")
    for i in ev.ir_issues[:30]:
        lines.append(f"- IR {i['severity']}: {i['error_code']} {i['message']}")
    if ev.power_flags:
        lines.append(f"PWR_FLAG 자동 추가(커넥터로 전원 입력): {', '.join(ev.power_flags)}")
    for d in ev.datasheets:
        problems = d["ratings_problems"] + [f"외부 부품 확인: {x}" for x in d["external_missing"]]
        lines.append(f"- 데이터시트 {d['reference']}: 핀맵 {d['pin_map']}, 전압 {d['ratings']}, 외부 부품 {d['external']}"
                     + (f" ({'; '.join(problems)})" if problems else ""))
    return "\n".join(lines) or "(문제 없음)"


def build_design_router(store: ProjectStore, jobs: JobRunner, service: ProposalService, inventory: InventoryStore,
                        datasheets: DatasheetLibrary, settings: Settings) -> APIRouter:
    router = APIRouter()
    access = AiAccess(store, settings)
    proposals = service.proposals

    def library() -> SymbolLibrary:
        if not settings.symbol_dirs:
            raise ApiError(409, "NO_SYMBOL_LIBRARY", "KiCad 심볼 라이브러리를 찾지 못했습니다.",
                           recovery_hint="KiCad 10을 설치하거나 AICS_SYMBOL_DIRS를 설정하세요.")
        return SymbolLibrary(settings.symbol_dirs)

    def inventory_rows() -> list[dict]:
        inv = inventory.get()
        if inv is None:
            return []
        rows = sorted(inv.parts, key=lambda p: (p.priority, -(p.stock or 0)))[:MAX_INVENTORY_ROWS]
        return [{"id": p.id, "category": p.category, "value": p.value, "package": p.package, "mpn": p.mpn,
                 "manufacturer": p.manufacturer, "stock": p.stock} for p in rows]

    def datasheet_text(circuit: CircuitIR) -> str:
        lib = SymbolLibrary(settings.symbol_dirs) if settings.symbol_dirs else None
        lines = []
        for comp in circuit.components:
            entry = datasheets.find(comp, lib.datasheet_url(comp.symbol_id) if lib else None)
            notes = facts_notes(datasheets.facts(entry)) if entry else None
            if notes:
                lines.append(f"{comp.reference}: " + notes.replace("\n", " / "))
        return "\n".join(lines)

    def save_run(project_id, board_id, record):
        store.save_ai_run(project_id, board_id, record)

    def run_review(cfg, provider, project_id: str, circuit: CircuitIR, evidence: str) -> tuple[dict, object]:
        review, call = review_circuit(provider, requirements=store.get_requirements(project_id),
                                      spec=store.get_requirements_spec(project_id), circuit=circuit,
                                      evidence=evidence, datasheet_notes=datasheet_text(circuit),
                                      max_output_tokens=min(cfg.max_output_tokens, 16384))
        review["created_at"] = now_iso()
        return review, call

    # --- requirements sheet -------------------------------------------------------------------

    @router.get("/api/projects/{project_id}/requirements/spec")
    def get_spec(project_id: str):
        return {"spec": store.get_requirements_spec(project_id)}

    @router.put("/api/projects/{project_id}/requirements/spec")
    def put_spec(project_id: str, body: SpecBody):
        store.put_requirements_spec(project_id, body.spec)
        return {"spec": body.spec}

    @router.post("/api/projects/{project_id}/requirements/structure", status_code=202)
    def structure(project_id: str):
        text = store.get_requirements(project_id)
        if len(text.strip()) < 10:
            raise ApiError(409, "NO_REQUIREMENTS", "요구사항이 비어 있습니다.", recovery_hint="요구사항을 먼저 적고 저장하세요.")
        cfg = access.settings()
        provider = access.provider_for(cfg)

        def run(job_id: str) -> dict:
            record = run_record(job_id, "requirements_structure", cfg, {"chars": len(text)})
            try:
                spec, call = structure_requirements(provider, text)
            except AiError as exc:
                raise api_error(exc) from exc
            record.update(status="succeeded", usage=call.usage)
            atomic_write(store.project_dir(project_id) / "ai_runs" / f"{record['id']}.json",
                         json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            return {"spec": spec, "model": call.model}

        return jobs.submit(project_id, "requirements_structure", run)

    # --- design --------------------------------------------------------------------------------

    @router.get(BOARD + "/ai/design/preview")
    def design_preview(project_id: str, board_id: str):
        """What a design request would send, so the user can decide."""
        store.get_board(project_id, board_id)
        circuit, _ = store.get_circuit(project_id, board_id)
        cfg = access.settings()
        return {"provider": cfg.provider, "model": cfg.model, "enabled": cfg.enabled,
                "key": access.key_status(cfg.provider)["configured"],
                "requirements_chars": len(store.get_requirements(project_id).strip()),
                "spec": store.get_requirements_spec(project_id) is not None,
                "inventory_parts": len(inventory_rows()), "datasheets": len(datasheets.entries()),
                "current_circuit": circuit is not None, "library": bool(settings.symbol_dirs)}

    def submit_design(project_id: str, board_id: str, *, instructions: str | None, use_inventory: bool,
                      review: bool, base: CircuitIR | None, findings: list[dict] | None, source: str, parent: str | None):
        board = store.get_board(project_id, board_id)
        text = store.get_requirements(project_id)
        spec = store.get_requirements_spec(project_id)
        if len(text.strip()) < 10 and not spec and not (instructions or "").strip():
            raise ApiError(409, "NO_REQUIREMENTS", "요구사항이 비어 있습니다.",
                           recovery_hint="「요구사항」 탭에 만들 회로를 적고 저장하세요.")
        cfg = access.settings()
        provider = access.provider_for(cfg)
        lib = library()
        rows = inventory_rows() if use_inventory else []

        def run(job_id: str) -> dict:
            index = symbol_index(settings.symbol_dirs, settings.workspace / "cache" / "symbol_index.json")
            resolver = PartResolver(lib, index, inventory.get() if use_inventory else None, datasheets)
            inp = DesignInput(requirements=text, spec=spec, board_name=board.name, instructions=instructions,
                              previous=circuit_text(base) if base else None, findings=findings, inventory=rows)
            record = run_record(job_id, source, cfg, {"requirements_chars": len(text), "spec": spec is not None,
                                                      "inventory_rows": len(rows), "revising": parent,
                                                      "findings": len(findings or [])})
            try:
                result = design_circuit(provider, inp, resolver, project_id=project_id,
                                        max_output_tokens=cfg.max_output_tokens)
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message})
                save_run(project_id, board_id, record)
                raise api_error(exc) from exc
            record.update(usage=[c.usage for c in result.calls], attempts=result.attempts,
                          unresolved=result.unresolved, problems=result.problems)
            plan = result.plan
            if result.circuit is None:
                record.update(status="incomplete")
                save_run(project_id, board_id, record)
                raise ApiError(422, "AI_DESIGN_INCOMPLETE",
                               "AI 설계안을 완성하지 못했습니다. " + " / ".join(result.problems[:5]),
                               recovery_hint="요구사항에 부품 품번이나 조건을 더 적거나, 다시 시도하세요.",
                               details={"unresolved": result.unresolved, "problems": result.problems,
                                        "questions": plan.get("questions") or [],
                                        "assumptions": plan.get("assumptions") or []})
            ai = {"model": result.calls[-1].model, "summary": str(plan.get("summary") or ""),
                  "assumptions": [str(x) for x in plan.get("assumptions") or []],
                  "questions": [str(x) for x in plan.get("questions") or []],
                  "citations": [{"reference": str(c.get("ref", "")), "page": c.get("page"), "text": str(c.get("text", ""))}
                                for c in result.connections.get("citations") or [] if isinstance(c, dict)],
                  "parts": [{"key": p.ref, "role": p.role, "symbol_id": p.symbol_id, "how": p.how} for p in result.parts],
                  "attempts": result.attempts, "remaining_problems": result.problems,
                  "notes": str(result.connections.get("notes") or ""), "parent": parent, "review": None}
            title = str(plan.get("title") or "AI 설계안")[:150]
            p = proposals.create(project_id, board_id, title=title, ops=[ReplaceCircuit(circuit=result.circuit)],
                                 source=source, rationale=ai["summary"][:20_000], ai=ai)
            p = service.check_now(p)
            if review:
                try:
                    findings_out, call = run_review(cfg, provider, project_id, result.circuit, proposal_evidence_text(p))
                    record.setdefault("usage", []).append(call.usage)
                    p = proposals.update(p, ai={**(p.ai or {}), "review": findings_out})
                except AiError as exc:
                    p = proposals.update(p, ai={**(p.ai or {}), "review": {"summary": f"AI 검토 실패: {exc.message}",
                                                                           "findings": []}})
            record.update(status="succeeded", output={"proposal": p.id, "status": p.status})
            save_run(project_id, board_id, record)
            return {"proposal_id": p.id, "status": p.status, "attempts": result.attempts,
                    "remaining_problems": result.problems}

        return jobs.submit(project_id, source, run, board_id=board_id)

    @router.post(BOARD + "/ai/design", status_code=202)
    def design(project_id: str, board_id: str, body: DesignBody):
        return submit_design(project_id, board_id, instructions=body.instructions, use_inventory=body.use_inventory,
                             review=body.review, base=None, findings=None, source="ai_design", parent=None)

    @router.post(BOARD + "/ai/revise", status_code=202)
    def revise(project_id: str, board_id: str, body: ReviseBody):
        if body.proposal_id:
            p = proposals.get(project_id, board_id, body.proposal_id)
            base = proposals.candidate(p)
            findings = ((p.ai or {}).get("review") or {}).get("findings") or []
        else:
            base, _ = store.get_circuit(project_id, board_id)
            if base is None:
                raise ApiError(409, "NO_CIRCUIT", "이 보드에는 아직 회로가 없습니다.")
            findings = (latest_review(project_id, board_id) or {}).get("findings") or []
        if not findings and not (body.instructions or "").strip():
            raise ApiError(409, "NOTHING_TO_REVISE", "반영할 검토 의견이나 수정 요청이 없습니다.",
                           recovery_hint="먼저 AI 검토를 실행하거나, 고칠 내용을 적으세요.")
        return submit_design(project_id, board_id, instructions=body.instructions, use_inventory=True,
                             review=body.review, base=base, findings=findings, source="ai_revision",
                             parent=body.proposal_id)

    # --- review --------------------------------------------------------------------------------

    def review_path(project_id: str, board_id: str):
        return store.board_dir(project_id, board_id) / "ai_reviews" / "latest.json"

    def latest_review(project_id: str, board_id: str) -> dict | None:
        path = review_path(project_id, board_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @router.get(BOARD + "/ai/review")
    def get_review(project_id: str, board_id: str):
        store.get_board(project_id, board_id)
        return {"review": latest_review(project_id, board_id)}

    @router.post(BOARD + "/ai/review", status_code=202)
    def review(project_id: str, board_id: str, body: ReviewBody):
        store.get_board(project_id, board_id)
        cfg = access.settings()
        provider = access.provider_for(cfg)
        p = None
        if body.proposal_id:
            p = proposals.get(project_id, board_id, body.proposal_id)
            circuit, evidence, revision = proposals.candidate(p), proposal_evidence_text(p), p.base_revision
        else:
            circuit, revision = store.get_circuit(project_id, board_id)
            if circuit is None:
                raise ApiError(409, "NO_CIRCUIT", "이 보드에는 아직 회로가 없습니다.")
            lines = [f"- IR {i.severity.value}: {i.error_code} {i.message}" for i in validate_circuit(circuit).issues]
            manifest = read_manifest(store.board_dir(project_id, board_id) / "outputs")
            if manifest and manifest.source_revision == revision:
                ac = manifest.automated_check
                lines.insert(0, f"KiCad 자동 검사: {ac.status}, ERC 오류 {ac.erc_errors} 경고 {ac.erc_warnings}")
                lines += [f"- ERC {v.get('severity')}: {v.get('description')} {', '.join(v.get('items') or [])}"
                          for v in ac.violations[:20]]
            for d in datasheet_evidence(circuit, datasheets, settings.symbol_dirs):
                lines.append(f"- 데이터시트 {d['reference']}: 핀맵 {d['pin_map']}, 전압 {d['ratings']}, 외부 부품 {d['external']}")
            evidence = "\n".join(lines)

        def run(job_id: str) -> dict:
            record = run_record(job_id, "ai_review", cfg, {"proposal": body.proposal_id, "revision": revision,
                                                           "components": len(circuit.components)})
            try:
                result, call = run_review(cfg, provider, project_id, circuit, evidence)
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message})
                save_run(project_id, board_id, record)
                raise api_error(exc) from exc
            if p is not None:
                current = proposals.get(project_id, board_id, p.id)
                proposals.update(current, ai={**(current.ai or {}), "review": result})
            else:
                atomic_write(review_path(project_id, board_id),
                             json.dumps({**result, "revision": revision}, ensure_ascii=False, indent=2) + "\n")
            record.update(status="succeeded", usage=call.usage, output={"findings": len(result["findings"])})
            save_run(project_id, board_id, record)
            return {"review": result, "proposal_id": body.proposal_id}

        return jobs.submit(project_id, "ai_review", run, board_id=board_id)

    return router
