"""Requirements -> AI design -> proposal, and AI review (Phase 2b).

Every AI result here ends as a proposal (or as review findings that change nothing). The proposal is
checked in its own folder (IR rules, KiCad ERC and netlist, datasheets) before a person approves it.
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from pydantic import Field

from ai_engine import AiError
from ai_engine.design import (
    DesignInput,
    circuit_text,
    design_circuit,
    review_circuit,
    structure_requirements,
    suggest_alternatives,
)
from circuit_core import CircuitIR, validate_circuit
from circuit_core.diff import diff_circuits
from circuit_core.guides import prompt_text as guide_prompt_text
from circuit_core.guides import relevant
from circuit_core.patch import ReplaceCircuit
from kicad_adapter import SymbolLibrary, SymbolLibraryError

from .ai_access import AiAccess, api_error, run_record
from .config import Settings
from .datasheets import DatasheetLibrary
from .design_resolver import PartResolver, facts_notes, symbol_index
from .errors import ApiError
from .exports import read_manifest
from .guides import GuideLibrary
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
    write_note: bool = False
    """Also write a research note (연구노트) from this review: a second AI call, saved as a draft."""


class AutoReviewBody(_Model):
    """Review → revise → review again until the stop condition holds (step 5 of the step screens)."""

    proposal_id: str
    max_warnings: int = Field(default=1, ge=0, le=9, description="Stop when 수정 필요 is 0 and 수정 권장 is at most this.")
    max_rounds: int = Field(default=3, ge=1, le=5, description="Reviews at most this many proposals (revisions = rounds − 1).")


class ResumeChoice(_Model):
    ref: str = Field(max_length=20)
    option_id: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, max_length=2000, description="「기타 의견」: the person's own direction.")


class ResumeAnswer(_Model):
    question: str = Field(max_length=1000)
    answer: str = Field(max_length=2000)


class ResumeBody(_Model):
    """Continue a design that stopped at parts with no KiCad symbol, with what the person chose for each."""

    job_id: str
    choices: list[ResumeChoice] = Field(default_factory=list)
    answers: list[ResumeAnswer] = Field(default_factory=list)


class ReviseBody(_Model):
    proposal_id: str | None = None
    instructions: str | None = Field(default=None, max_length=10_000)
    finding_indexes: list[int] | None = Field(default=None, description="AI review findings to address; None = all.")
    include_checks: bool = Field(default=True, description="Also address problems from the automated checks.")
    review: bool = True


def _norm(title) -> str:
    return " ".join(str(title or "").split()).lower()


def _count_text(findings: list[dict]) -> str:
    n = {s: sum(1 for f in findings if f.get("severity") == s) for s in ("error", "warning", "info")}
    return f"수정 필요 {n['error']} · 수정 권장 {n['warning']} · 참고 {n['info']}"


def _check_text(p: Proposal) -> str:
    ev = p.evidence
    if ev is None:
        return "검사 전"
    ac = ev.automated_check
    if ac is None:
        return "규칙 오류" if ev.blocking else "생성 실패"
    status = {"passed": "통과", "failed": "실패", "not_checked": "검사 안 함"}.get(ac.status, ac.status)
    if ac.erc_errors is None:
        return status + (" (KiCad 없음)" if ac.status == "not_checked" else "")
    return f"{status}, ERC 오류 {ac.erc_errors} · 경고 {ac.erc_warnings}"


STOP_TEXT = {"met": "조건 충족", "max_rounds": "최대 횟수에 도달", "repeated": "같은 의견이 다시 나옴",
             "check_failed": "수정안이 KiCad 검사를 통과하지 못함", "revise_failed": "수정안을 만들지 못함",
             "ai_failed": "AI 검토 실패"}


def _stop_text(stop: str, message: str) -> str:
    return "멈춤: " + STOP_TEXT.get(stop, stop) + (f" ({message})" if message else "")


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


def _finding(severity: str, title: str, detail: str, refs: list[str] | None = None, source: str = "check") -> dict:
    return {"severity": severity, "title": title[:200], "detail": detail[:2000], "references": refs or [],
            "suggestion": None, "source": source}


def proposal_check_findings(p: Proposal) -> list[dict]:
    """Problems found by the automated checks of a proposal, phrased as review findings."""
    out: list[dict] = []
    ev = p.evidence
    if ev is not None:
        for i in ev.ir_issues:
            if i["severity"] in ("error", "warning"):
                out.append(_finding(i["severity"], f"회로 규칙: {i['error_code']}", i["message"]))
        if ev.generation_error:
            for i in ev.generation_error.get("issues") or [{"error_code": ev.generation_error["error_code"],
                                                             "message": ev.generation_error["message"]}]:
                out.append(_finding("error", f"회로도 생성 실패: {i['error_code']}", i["message"]))
        if ev.automated_check:
            for v in ev.automated_check.violations:
                out.append(_finding("error" if v.get("severity") == "error" else "warning",
                                    f"KiCad ERC: {v.get('description')}", ", ".join(v.get("items") or [])))
        for d in ev.datasheets:
            for x in d["ratings_problems"]:
                out.append(_finding("warning", f"데이터시트 전압 점검 ({d['reference']})", x, [d["reference"]]))
            for x in d["external_missing"]:
                out.append(_finding("warning", f"데이터시트 외부 부품 ({d['reference']})", f"확인 필요: {x}",
                                    [d["reference"]]))
            if d["pin_map"] == "mismatch":
                out.append(_finding("error", f"데이터시트 핀맵 불일치 ({d['reference']})",
                                    "데이터시트 핀 표와 KiCad 심볼 핀이 다릅니다.", [d["reference"]]))
    for x in (p.ai or {}).get("remaining_problems") or []:
        out.append(_finding("error", "AI 연결 검사에서 남은 문제", x))
    return out


def _pins_of(lib: SymbolLibrary):
    def lookup(symbol_id: str):
        try:
            return [(p.number, p.name) for p in lib.get(symbol_id).pins]
        except SymbolLibraryError:
            return None
    return lookup


def build_design_router(store: ProjectStore, jobs: JobRunner, service: ProposalService, inventory: InventoryStore,
                        datasheets: DatasheetLibrary, settings: Settings, guides: GuideLibrary | None = None,
                        note_writer=None) -> APIRouter:
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

    def applicable_guides(project_id: str, circuit: CircuitIR | None, extra: str = "") -> list:
        """Adopted design-guide entries for this circuit and requirements (empty without a guide library)."""
        if guides is None:
            return []
        spec = store.get_requirements_spec(project_id)
        text = " ".join([store.get_requirements(project_id), extra, json.dumps(spec, ensure_ascii=False) if spec else ""])
        return relevant(guides.entries(), circuit=circuit, text=text, text_topics=True)

    def run_review(cfg, provider, project_id: str, circuit: CircuitIR, evidence: str,
                   context: dict | None = None) -> tuple[dict, object]:
        entries = applicable_guides(project_id, circuit)
        review, call = review_circuit(provider, requirements=store.get_requirements(project_id),
                                      spec=store.get_requirements_spec(project_id), circuit=circuit,
                                      evidence=evidence, datasheet_notes=datasheet_text(circuit),
                                      guides=guide_prompt_text(entries), guide_ids={e.id for e in entries},
                                      max_output_tokens=min(cfg.max_output_tokens, 16384))
        review["created_at"] = now_iso()
        review["guides_checked"] = [e.id for e in entries]
        flagged = [f["guide"] for f in review["findings"] if f.get("guide")]
        if guides is not None and flagged:
            guides.record("flagged", flagged, by="AI 검토", **(context or {"project_id": project_id}))
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
                "current_circuit": circuit is not None, "library": bool(settings.symbol_dirs),
                "guides": [{"id": e.id, "title": e.title} for e in applicable_guides(project_id, circuit)]}

    def part_choices(provider, cfg, inp: DesignInput, plan: dict, unresolved: list[dict], resolve, index, record,
                     step) -> list[dict]:
        """Ways forward for parts with no usable KiCad symbol: the AI's alternatives, close library symbols and an
        external-module header. Every option that changes the part is checked against this machine's library and
        kept only if it resolves; the AI's other ideas are kept as directions for a new design."""
        hits, library_text = {}, {}
        for u in unresolved:
            terms = [str(t) for t in (u.get("mpn"), u.get("value"), *(u.get("search_terms") or []), u.get("role")) if t]
            hits[u["ref"]] = index.search(terms, limit=8)
            library_text[u["ref"]] = [f"{sym.lib_id} — {sym.description[:80]}" for _, sym in hits[u["ref"]]]
        suggested: list[dict] = []
        step("parts", "AI에 대안을 묻는 중")
        try:
            suggested, call = suggest_alternatives(provider, inp, plan, unresolved, library_text,
                                                   max_output_tokens=min(cfg.max_output_tokens, 8192))
            record.setdefault("usage", []).append(call.usage)
        except AiError as exc:
            step("parts", f"AI 대안 제안 실패: {exc.message}")

        def offer(options: list, ref: str, u: dict, kind: str, title: str, detail: str, part: dict | None,
                  source: str) -> bool:
            option = {"id": f"{ref}-{len(options) + 1}", "kind": kind, "title": title[:200], "detail": detail[:600],
                      "source": source, "executable": False, "symbol_id": None, "part": None}
            if part is not None:
                resolved, _ = resolve({**part, "ref": ref, "role": u.get("role") or ""})
                if resolved is None:
                    return False  # names something this library does not have: not offered
                keep = {k: part[k] for k in ("category", "value", "mpn", "manufacturer", "package", "pin_count",
                                             "search_terms") if part.get(k)}
                option.update(executable=True, symbol_id=resolved.symbol_id,
                              part={**keep, "kicad_symbol": resolved.symbol_id})
                if any(o["symbol_id"] == resolved.symbol_id and o["kind"] == kind for o in options):
                    return False
            options.append(option)
            return True

        out = []
        for u in unresolved:
            ref, options = u["ref"], []
            for o in suggested:
                if str(o.get("ref", "")).upper() != ref:
                    continue
                raw = {k: v for k, v in (o.get("part") or {}).items() if v not in (None, "", [])}
                part = ({"category": u.get("category") or "ic", **raw}
                        if raw.get("kicad_symbol") or raw.get("mpn") or raw.get("category") == "connector" else None)
                if o["kind"] == "replace" and part is None:
                    continue  # a replacement must name a part
                offer(options, ref, u, o["kind"], str(o.get("title", "")), str(o.get("detail", "")), part, "ai")
            for _, sym in hits[ref]:
                if sum(1 for o in options if o["kind"] == "replace") >= 4:
                    break
                name = sym.lib_id.split(":", 1)[1]
                offer(options, ref, u, "replace", name, f"KiCad 라이브러리 검색 결과: {sym.description or sym.lib_id}",
                    {"category": u.get("category") or "ic", "value": name, "kicad_symbol": sym.lib_id}, "library")
            if not any(o["kind"] == "approach" and o["executable"] for o in options):
                for n in dict.fromkeys(x for x in (int(u.get("pin_count") or 0), 20, 10) if x):
                    if offer(options, ref, u, "approach", f"{ref}를 외부 모듈로 두고 {n}핀 헤더로 연결",
                           "이 보드에는 핀 헤더만 두고 부품은 별도 모듈(개발 보드 등)에 둡니다. 필요한 신호·전원을 헤더 핀으로 연결합니다.",
                           {"category": "connector", "pin_count": n, "value": f"{u.get('value') or ref} 모듈"}, "app"):
                        break
            out.append({"ref": ref, "role": str(u.get("role") or ""), "value": str(u.get("value") or ""),
                        "mpn": str(u.get("mpn") or ""), "why": str(u.get("why") or ""), "options": options})
        n = sum(len(c["options"]) for c in out)
        step("parts", f"대안 {n}개 — 진행할 방법을 고르면 이어서 설계합니다")
        return out

    def design_task(project_id: str, board_id: str, *, instructions: str | None, use_inventory: bool,
                    review: bool, base: CircuitIR | None, findings: list[dict] | None, source: str, parent: str | None,
                    plan: dict | None = None):
        """Check the inputs now and return the design work as ``run(job_id)``. ``base`` is the design being revised
        (None for a new design); the result's diff against it is recorded so the user sees what the revision changed.
        Steps (parts, pins, check, proposal, review) are recorded on the job for the step screens."""
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
            def step(name: str, text: str) -> None:
                jobs.step(job_id, name, text)

            index = symbol_index(settings.symbol_dirs, settings.workspace / "cache" / "symbol_index.json")
            resolver = PartResolver(lib, index, inventory.get() if use_inventory else None, datasheets)
            entries = applicable_guides(project_id, base, instructions or "")
            inp = DesignInput(requirements=text, spec=spec, board_name=board.name, instructions=instructions,
                              previous=circuit_text(base) if base else None, findings=findings, inventory=rows,
                              guides=guide_prompt_text(entries))
            record = run_record(job_id, source, cfg, {"requirements_chars": len(text), "spec": spec is not None,
                                                      "inventory_rows": len(rows), "revising": parent,
                                                      "findings": len(findings or [])})
            try:
                result = design_circuit(provider, inp, resolver, project_id=project_id,
                                        max_output_tokens=cfg.max_output_tokens, pins_of=_pins_of(lib), on_step=step,
                                        plan=plan)
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message,
                                                     **({"details": exc.details} if exc.details else {})})
                save_run(project_id, board_id, record)
                raise api_error(exc) from exc
            record.update(usage=[c.usage for c in result.calls], attempts=result.attempts,
                          unresolved=result.unresolved, problems=result.problems)
            plan_out = result.plan
            if result.circuit is None:
                details = {"unresolved": result.unresolved, "problems": result.problems,
                           "questions": plan_out.get("questions") or [], "assumptions": plan_out.get("assumptions") or []}
                message = "AI 설계안을 완성하지 못했습니다. " + " / ".join(result.problems[:5])
                hint = "요구사항에 부품 품번이나 조건을 더 적거나, 다시 시도하세요."
                if result.unresolved_parts and base is None:
                    # Not a dead end: offer ways forward and keep the plan so the design can continue from here.
                    details.update(choices=part_choices(provider, cfg, inp, plan_out, result.unresolved_parts, resolver,
                                                        index, record, step),
                                   plan=plan_out, instructions=instructions, use_inventory=use_inventory, review=review)
                    message = (f"부품 {len(result.unresolved_parts)}개를 KiCad 라이브러리에서 찾지 못했습니다. "
                               "부품마다 진행할 방법을 고르면 이어서 설계합니다.")
                    hint = "다른 부품으로 대체, 다른 방법으로 진행, 또는 직접 방향을 적을 수 있습니다."
                record.update(status="incomplete")
                save_run(project_id, board_id, record)
                raise ApiError(422, "AI_DESIGN_INCOMPLETE", message, recovery_hint=hint, details=details)
            ai = {"model": result.calls[-1].model, "summary": str(plan_out.get("summary") or ""),
                  "assumptions": [str(x) for x in plan_out.get("assumptions") or []],
                  "questions": [str(x) for x in plan_out.get("questions") or []],
                  "citations": [{"reference": str(c.get("ref", "")), "page": c.get("page"), "text": str(c.get("text", ""))}
                                for c in result.connections.get("citations") or [] if isinstance(c, dict)],
                  "parts": [{"key": p.ref, "role": p.role, "symbol_id": p.symbol_id, "how": p.how} for p in result.parts],
                  "templates": [{"ref": b.ref, "title": b.title, "params": b.params,
                                 "parts": [c.reference for c in b.components]} for b in result.templates],
                  "attempts": result.attempts, "remaining_problems": result.problems,
                  "notes": str(result.connections.get("notes") or ""), "parent": parent, "review": None,
                  "addressed": [{"severity": f.get("severity"), "title": f.get("title"), "detail": f.get("detail"),
                                 "source": f.get("source", "ai")} for f in findings or []],
                  "instructions": instructions,
                  "diff_from_parent": diff_circuits(base, result.circuit) if base is not None else None}
            offered = [e.id for e in entries]
            said = [str(x).strip() for x in [*(plan_out.get("guides_applied") or []),
                                              *(result.connections.get("guides_applied") or [])]]
            ai["guides"] = {"offered": offered, "applied": [g for g in dict.fromkeys(said) if g in offered]}
            title = str(plan_out.get("title") or "AI 설계안")[:150]
            p = proposals.create(project_id, board_id, title=title, ops=[ReplaceCircuit(circuit=result.circuit)],
                                 source=source, rationale=ai["summary"][:20_000], ai=ai)
            if guides is not None and ai["guides"]["applied"]:
                guides.record("cited", ai["guides"]["applied"], by="AI 설계", project_id=project_id, board_id=board_id,
                              proposal_id=p.id)
            step("check", "KiCad로 회로도를 만들어 ERC 실행")
            p = service.check_now(p)
            step("check", f"KiCad 검사: {_check_text(p)}")
            step("proposal", f"제안 #{int(p.id)} 만듦 (승인 전에는 회로가 바뀌지 않음)")
            if review:
                step("review", "AI 검토 중")
                try:
                    findings_out, call = run_review(cfg, provider, project_id, result.circuit, proposal_evidence_text(p),
                                                    {"project_id": project_id, "board_id": board_id,
                                                     "proposal_id": p.id})
                    record.setdefault("usage", []).append(call.usage)
                    p = proposals.update(p, ai={**(p.ai or {}), "review": findings_out})
                    step("review", "AI 검토: " + _count_text(findings_out["findings"]))
                except AiError as exc:
                    step("review", f"AI 검토 실패: {exc.message}")
                    p = proposals.update(p, ai={**(p.ai or {}), "review": {"summary": f"AI 검토 실패: {exc.message}",
                                                                           "findings": []}})
            record.update(status="succeeded", output={"proposal": p.id, "status": p.status})
            save_run(project_id, board_id, record)
            return {"proposal_id": p.id, "status": p.status, "attempts": result.attempts,
                    "remaining_problems": result.problems}

        return run

    def submit_design(project_id: str, board_id: str, **kw):
        return jobs.submit(project_id, kw["source"], design_task(project_id, board_id, **kw), board_id=board_id)

    @router.post(BOARD + "/ai/design", status_code=202)
    def design(project_id: str, board_id: str, body: DesignBody):
        return submit_design(project_id, board_id, instructions=body.instructions, use_inventory=body.use_inventory,
                             review=body.review, base=None, findings=None, source="ai_design", parent=None)

    @router.post(BOARD + "/ai/design/resume", status_code=202)
    def resume_design(project_id: str, board_id: str, body: ResumeBody):
        """Continue a design that stopped at parts with no KiCad symbol. A chosen replacement or module header goes
        into the saved parts plan and the design continues from it (no new parts plan); a direction in words (the
        AI's idea or the person's own) starts a new design with it. Answers to the AI's questions are appended to
        the requirements either way."""
        job = store.get_job(body.job_id)
        details = (job.error or {}).get("details") or {}
        if (job.project_id != project_id or job.board_id != board_id or job.status != "failed"
                or not details.get("plan") or not details.get("choices")):
            raise ApiError(409, "NOT_RESUMABLE", "이어서 설계할 수 있는 작업이 아닙니다.",
                           recovery_hint="부품을 찾지 못해 멈춘 AI 설계에서만 이어서 할 수 있습니다.")
        picked = {c.ref.upper(): c for c in body.choices}
        plan, notes, parts = details["plan"], [], [dict(x) for x in details["plan"].get("parts") or [] if isinstance(x, dict)]
        for c in details["choices"]:
            pick = picked.get(c["ref"])
            option = next((o for o in c["options"] if pick and o["id"] == pick.option_id), None)
            text = (pick.text or "").strip() if pick else ""
            if option is None and not text:
                raise ApiError(409, "CHOICE_MISSING", f"{c['ref']}({c['role']})를 어떻게 할지 고르세요.",
                               recovery_hint="다른 부품으로 대체, 다른 방법으로 진행, 기타 의견 중 하나를 고르세요.")
            original = " ".join(x for x in (c["value"], c["mpn"]) if x)
            if option is not None and option["executable"] and not text:
                role = c["role"] + (" (외부 모듈 헤더)" if option["part"].get("category") == "connector" else "")
                entry = {**option["part"], "ref": c["ref"], "role": role, "basis": "changed", "request": original,
                         "reason": f"{original}은(는) KiCad 라이브러리에 심볼이 없어 사람이 고른 대안: {option['title']}. "
                                   f"{option['detail']}"[:600]}
                parts = [entry if str(x.get("ref", "")).upper() == c["ref"] else x for x in parts]
            else:
                direction = text or f"{option['title']} — {option['detail']}"
                notes.append(f"- {c['ref']} ({c['role']}, 원래 {original}, KiCad 심볼 없음): {direction}")
        answered = [(a.question.strip(), a.answer.strip()) for a in body.answers if a.answer.strip()]
        if answered:
            block = "\n".join(f"- 질문: {q}\n  답: {a}" for q, a in answered)
            store.put_requirements(project_id, f"{store.get_requirements(project_id).rstrip()}\n\n## AI 질문에 대한 답\n{block}\n")
        kw = {"use_inventory": bool(details.get("use_inventory", True)), "review": bool(details.get("review", True)),
              "base": None, "findings": None, "source": "ai_design", "parent": None}
        if notes:
            instructions = "\n".join(x for x in (details.get("instructions") or "",
                                                 "[KiCad 심볼이 없는 부품에 대해 사람이 정한 방향]", *notes) if x)
            return submit_design(project_id, board_id, instructions=instructions, **kw)
        return submit_design(project_id, board_id, instructions=details.get("instructions"),
                             plan={**plan, "parts": parts}, **kw)

    def revise_inputs(project_id: str, board_id: str, proposal_id: str | None):
        """(base circuit, AI review findings, automated-check findings) for a revision."""
        if proposal_id:
            p = proposals.get(project_id, board_id, proposal_id)
            return (proposals.candidate(p), ((p.ai or {}).get("review") or {}).get("findings") or [],
                    proposal_check_findings(p))
        base, revision = store.get_circuit(project_id, board_id)
        if base is None:
            raise ApiError(409, "NO_CIRCUIT", "이 보드에는 아직 회로가 없습니다.")
        checks = [_finding(i.severity.value, f"회로 규칙: {i.error_code}", i.message)
                  for i in validate_circuit(base).issues if i.severity.value in ("error", "warning")]
        manifest = read_manifest(store.board_dir(project_id, board_id) / "outputs")
        if manifest and manifest.source_revision == revision:
            checks += [_finding("error" if v.get("severity") == "error" else "warning",
                                f"KiCad ERC: {v.get('description')}", ", ".join(v.get("items") or []))
                       for v in manifest.automated_check.violations]
        for d in datasheet_evidence(base, datasheets, settings.symbol_dirs):
            checks += [_finding("warning", f"데이터시트 전압 점검 ({d['reference']})", x, [d["reference"]])
                       for x in d["ratings_problems"]]
            checks += [_finding("warning", f"데이터시트 외부 부품 ({d['reference']})", f"확인 필요: {x}",
                                [d["reference"]]) for x in d["external_missing"]]
        return base, (latest_review(project_id, board_id) or {}).get("findings") or [], checks

    @router.get(BOARD + "/ai/revise/preview")
    def revise_preview(project_id: str, board_id: str, proposal_id: str | None = None):
        """What a revision would address: AI review findings (selectable) and automated-check problems."""
        _, findings, checks = revise_inputs(project_id, board_id, proposal_id)
        return {"findings": [{**f, "index": i} for i, f in enumerate(findings)], "checks": checks}

    @router.post(BOARD + "/ai/revise", status_code=202)
    def revise(project_id: str, board_id: str, body: ReviseBody):
        base, findings, checks = revise_inputs(project_id, board_id, body.proposal_id)
        if body.finding_indexes is not None:
            findings = [findings[i] for i in body.finding_indexes if 0 <= i < len(findings)]
        chosen = [{**f, "source": "ai"} for f in findings] + (checks if body.include_checks else [])
        if not chosen and not (body.instructions or "").strip():
            raise ApiError(409, "NOTHING_TO_REVISE", "반영할 검토 의견, 검사 문제, 수정 요청이 없습니다.",
                           recovery_hint="고칠 내용을 직접 적거나, 먼저 AI 검토를 실행하세요.")
        return submit_design(project_id, board_id, instructions=body.instructions, use_inventory=True,
                             review=body.review, base=base, findings=chosen, source="ai_revision",
                             parent=body.proposal_id)

    # --- automatic review loop (step 5) ---------------------------------------------------------

    @router.post(BOARD + "/ai/auto-review", status_code=202)
    def auto_review(project_id: str, board_id: str, body: AutoReviewBody):
        """Review the proposal; while 수정 필요 > 0 or 수정 권장 > max_warnings, make a revision from the findings
        and review that. Stops when the condition holds, after max_rounds reviews, when a finding comes back after
        a revision meant to fix it, when a revision fails KiCad checks, or when the AI fails. Every round is a
        separate proposal; nothing on the board changes until a person approves one."""
        first = proposals.get(project_id, board_id, body.proposal_id)
        if first.status in ("approved", "rejected", "stale"):
            raise ApiError(409, "PROPOSAL_CLOSED", "이미 결정된 제안은 자동 검토할 수 없습니다.",
                           recovery_hint="열려 있는 제안(승인 대기)을 고르세요.")
        cfg = access.settings()
        provider = access.provider_for(cfg)
        library()  # revisions need the symbol library; fail now rather than in round 2

        def run(job_id: str) -> dict:
            rounds: list[dict] = []
            p = first
            asked: set[str] = set()
            stop, message, why = "max_rounds", "", None
            for k in range(1, body.max_rounds + 1):
                p = proposals.get(project_id, board_id, p.id)
                review = (p.ai or {}).get("review")
                if not review or "findings" not in review or str(review.get("summary", "")).startswith("AI 검토 실패"):
                    jobs.step(job_id, "review", f"{k}회차: 제안 #{int(p.id)} AI 검토 중")
                    try:
                        review, _ = run_review(cfg, provider, project_id, proposals.candidate(p), proposal_evidence_text(p),
                                               {"project_id": project_id, "board_id": board_id, "proposal_id": p.id})
                    except AiError as exc:
                        stop, message, why = "ai_failed", exc.message, exc.details
                        break
                    p = proposals.update(proposals.get(project_id, board_id, p.id), ai={**(p.ai or {}), "review": review})
                findings = review["findings"]
                needs = [f for f in findings if f.get("severity") == "error"]
                recommended = [f for f in findings if f.get("severity") == "warning"]
                failed_check = p.status in ("invalid", "check_failed")
                entry = {"round": k, "proposal_id": p.id, "errors": len(needs) + (1 if failed_check else 0),
                         "warnings": len(recommended), "check": _check_text(p), "check_failed": failed_check,
                         "said": [str(f.get("title", "")) for f in [*needs, *recommended]][:4]}
                rounds.append(entry)
                if entry["errors"] == 0 and entry["warnings"] <= body.max_warnings:
                    stop = "met"
                elif failed_check and k > 1:
                    stop = "check_failed"
                elif k == body.max_rounds:
                    stop = "max_rounds"
                elif {_norm(f.get("title")) for f in [*needs, *recommended]} & asked:
                    stop = "repeated"
                    entry["repeated"] = sorted({str(f.get("title")) for f in [*needs, *recommended]
                                                if _norm(f.get("title")) in asked})
                else:
                    stop = ""
                jobs.step(job_id, "round", f"{k}회차: 제안 #{int(p.id)} 수정 필요 {entry['errors']} · 수정 권장 "
                                            f"{entry['warnings']}", data=entry)
                if stop:
                    break
                chosen = [{**f, "source": "ai"} for f in [*needs, *recommended]] + proposal_check_findings(p)
                asked |= {_norm(f.get("title")) for f in [*needs, *recommended]}
                jobs.step(job_id, "revise", f"{k}회차: 의견 {len(chosen)}개를 반영한 수정안 만드는 중")
                try:
                    out = design_task(project_id, board_id, instructions=None, use_inventory=True, review=True,
                                      base=proposals.candidate(p), findings=chosen, source="ai_revision",
                                      parent=p.id)(job_id)
                except ApiError as exc:
                    stop, message, why = "revise_failed", exc.message, exc.details
                    break
                revised = proposals.get(project_id, board_id, out["proposal_id"])
                diff = ((revised.ai or {}).get("diff_from_parent") or {}).get("summary")
                entry["revised_to"] = revised.id
                entry["fix"] = {"summary": str((revised.ai or {}).get("summary") or "")[:300], "diff": diff}
                jobs.step(job_id, "revised", f"{k}회차: 수정안 #{int(revised.id)} 만듦", data=entry)
                p = revised
            last = proposals.get(project_id, board_id, p.id)
            result = {"rounds": rounds, "stop": stop, "message": message, "why": why, "proposal_id": last.id,
                      "met": stop == "met", "max_warnings": body.max_warnings, "max_rounds": body.max_rounds}
            jobs.step(job_id, "done", _stop_text(stop, message))
            return result

        return jobs.submit(project_id, "ai_auto_review", run, board_id=board_id)

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
                result, call = run_review(cfg, provider, project_id, circuit, evidence,
                                          {"project_id": project_id, "board_id": board_id,
                                           **({"proposal_id": body.proposal_id} if body.proposal_id else {})})
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message,
                                                     **({"details": exc.details} if exc.details else {})})
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
            out = {"review": result, "proposal_id": body.proposal_id}
            if body.write_note and note_writer is not None:
                # The research note is written from the same evidence plus this review (a second call).
                try:
                    note = note_writer(cfg, provider, project_id, board_id, body.proposal_id, job_id=job_id,
                                       review=result)
                    out["note"] = {"id": note.id, "title": note.title}
                except ApiError as exc:  # the review is kept even when the note fails
                    out["note_error"] = {"error_code": exc.error_code, "message": exc.message}
            return out

        return jobs.submit(project_id, "ai_review", run, board_id=board_id)

    return router
