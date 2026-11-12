"""Requirements -> AI design -> proposal, and AI review (Phase 2b).

Every AI result here ends as a proposal (or as review findings that change nothing). The proposal is
checked in its own folder (IR rules, KiCad ERC and netlist, datasheets) before a person approves it.
"""

from __future__ import annotations

import json
import re
import time

from fastapi import APIRouter
from pydantic import Field, ValidationError

from ai_engine import AiError
from ai_engine.design import (
    DesignInput,
    circuit_text,
    counted,
    design_circuit,
    review_circuit,
    revise_by_edits,
    structure_requirements,
    suggest_alternatives,
)
from ai_engine.review_passes import review_perspective, select_perspectives
from circuit_core import CircuitIR, validate_circuit
from circuit_core.diff import diff_circuits
from circuit_core.guides import prompt_text as guide_prompt_text
from circuit_core.guides import relevant
from circuit_core.models import Component
from circuit_core.patch import ReplaceCircuit
from kicad_adapter import SymbolLibrary, SymbolLibraryError

from .ai_access import AiAccess, api_error, run_record
from .config import Settings
from .datasheet_flow import DatasheetPrep, report_text
from .datasheets import DatasheetLibrary
from .design_resolver import PartResolver, facts_notes, symbol_index
from .errors import ApiError
from .exports import read_manifest
from .guides import GuideLibrary
from .inventory_store import InventoryStore
from .jobs import JobRunner
from .proposals import Proposal, datasheet_evidence
from .routes_guides import circuit_checklist
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
    datasheets: bool = False
    """Download the chosen parts' datasheets and have the AI summarize them, as a step of the design. It is the
    person's confirmation (ADR-005): off, only summaries already in the library are used."""


class ReviewBody(_Model):
    proposal_id: str | None = None
    write_note: bool = False
    """Also write a research note (연구노트) from this review: a second AI call, saved as a draft."""
    deep: bool = False
    """Also review by perspective (requirements, power, signals, control): one more AI call per perspective that
    applies, each answering a checklist item by item (ai_engine.review_passes)."""


class AutoReviewBody(_Model):
    """Review → revise → review again until the stop condition holds (step 5 of the step screens)."""

    proposal_id: str
    max_warnings: int = Field(default=1, ge=0, le=9, description="Stop when 수정 필요 is 0 and 수정 권장 is at most this.")
    max_rounds: int = Field(default=3, ge=1, le=5, description="Reviews at most this many proposals (revisions = rounds − 1).")
    datasheets: bool = Field(default=False, description="Revisions fetch and summarize new parts' datasheets (DesignBody).")
    deep: bool = Field(default=False, description="Every round's review also runs the perspective passes (ReviewBody).")


class ResumeChoice(_Model):
    ref: str = Field(max_length=20)
    option_id: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, max_length=2000, description="「기타 의견」: the person's own direction.")


class ResumeAnswer(_Model):
    question: str = Field(max_length=1000)
    answer: str = Field(max_length=2000)


class DecisionBody(_Model):
    title: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=2, max_length=2000)
    proposal_id: str | None = None


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
    datasheets: bool = Field(default=False, description="Fetch and summarize new parts' datasheets (DesignBody).")


def _norm(title) -> str:
    return " ".join(str(title or "").split()).lower()


def _bigrams(text) -> set[str]:
    t = re.sub(r"\s+", "", str(text or "").lower())
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def similar(a, b, threshold: float = 0.5) -> bool:
    """Two finding titles say the same thing (character-pair overlap, so 「역극성 보호 없음」 and 「역극성 보호
    회로가 없음」 match; Korean word endings change too much for word comparison)."""
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / max(1, len(x | y)) >= threshold


def mark_decided(findings: list[dict], decisions: list[dict]) -> list[dict]:
    """Mark the error/warning findings a person decided to keep as designed (same or similar title)."""
    for f in findings:
        f.pop("decided", None)
        if f.get("severity") not in ("error", "warning"):
            continue
        d = next((d for d in decisions if similar(f.get("title"), d.get("title"))), None)
        if d is not None:
            f["decided"] = {"id": d["id"], "reason": d.get("reason") or ""}
    return findings


_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def merge_pass_findings(findings: list[dict], extra: list[dict]) -> list[dict]:
    """Add a perspective pass's findings to a review's. The same point raised twice (similar titles) is kept once —
    the one whose basis checked out, else the more severe — and remembers every perspective that raised it."""
    for f in extra:
        twin = next((x for x in findings if similar(x.get("title"), f.get("title"))), None)
        if twin is None:
            findings.append(f)
            continue
        seen = [*(twin.get("perspectives") or [twin.get("perspective") or "general"]), f.get("perspective")]
        better = ((f.get("verified") is True and twin.get("verified") is not True)
                  or (f.get("verified") == twin.get("verified")
                      and _SEVERITY_ORDER[f["severity"]] < _SEVERITY_ORDER[twin["severity"]]))
        if better:
            twin.clear()
            twin.update(f)
        twin["perspectives"] = list(dict.fromkeys(x for x in seen if x))
    findings.sort(key=lambda f: (_SEVERITY_ORDER[f["severity"]], f.get("verified") is False))
    return findings


def _count_text(findings: list[dict]) -> str:
    needs, recommended, unverified = counted(findings)
    info = sum(1 for f in findings if f.get("severity") == "info")
    decided = sum(1 for f in findings if f.get("decided"))
    return (f"수정 필요 {len(needs)} · 수정 권장 {len(recommended)} · 참고 {info}"
            + (f" · 근거 확인 안 됨 {len(unverified)}" if unverified else "")
            + (f" · 사람이 정함 {decided}" if decided else ""))


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
             "no_progress": "의견이 줄지 않음", "no_change": "수정안이 회로를 바꾸지 않음",
             "declined": "AI가 남은 의견을 고치지 않기로 함",
             "check_failed": "수정안이 KiCad 검사를 통과하지 못함", "revise_failed": "수정안을 만들지 못함",
             "ai_failed": "AI 검토 실패"}


def revision_title(summary: str, changes: list[str]) -> str:
    """A revision's title for people: the first sentence of the AI's summary (without its "F1, F2:" prefix), else
    the first changes."""
    first = re.split(r"(?<=[.。])\s", re.sub(r"^\s*(F\d+\s*[,·/]?\s*)+[:：]\s*", "", summary or "").strip())[0]
    text = first or (", ".join(changes[:3]) + (" 외" if len(changes) > 3 else ""))
    return ("수정: " + text)[:150]


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
                 "manufacturer": p.manufacturer, "stock": p.stock, "rating": p.rating, "tolerance": p.tolerance,
                 "notes": p.notes} for p in rows]

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
                   context: dict | None = None, previous: list[dict] | None = None, *, deep: bool = False,
                   base: dict | None = None, changes: list[str] | None = None,
                   step=None) -> tuple[dict, list[dict]]:
        """The AI review of a circuit and the usage of its calls. ``deep`` adds the perspective passes
        (ai_engine.review_passes): what each checked is kept in ``passes`` and their findings join the general
        review's. ``base`` is a review of this same circuit made before (its general pass is kept and only the
        passes are added); ``changes`` is what a revision changed, for the passes to look at first."""
        entries = applicable_guides(project_id, circuit)
        board_id = (context or {}).get("board_id")
        decisions = store.get_review_decisions(project_id, board_id) if board_id else []
        requirements = store.get_requirements(project_id)
        common = {"requirements": requirements, "spec": store.get_requirements_spec(project_id), "circuit": circuit,
                  "evidence": evidence, "datasheet_notes": datasheet_text(circuit),
                  "guides": guide_prompt_text(entries), "guide_ids": {e.id for e in entries},
                  "max_output_tokens": min(cfg.max_output_tokens, 16384), "previous": previous, "decided": decisions}
        usage: list[dict] = []
        if base is not None and base.get("findings") is not None:
            review = {**base, "findings": [dict(f) for f in base["findings"]]}
        else:
            review, call = review_circuit(provider, **common)
            usage.append(call.usage)
        if deep:
            for f in review["findings"]:
                f.setdefault("perspective", "general")
            chosen = select_perspectives(circuit, requirements)
            passes = []
            for i, (perspective, items) in enumerate(chosen):
                if step:
                    step(f"관점별 검토 {i + 1}/{len(chosen)}: {perspective.label} (확인 항목 {len(items)}개)")
                try:
                    done, call = review_perspective(provider, perspective, items, changes=changes, **common)
                except AiError as exc:  # one pass failing does not lose the others or the general review
                    passes.append({"key": perspective.key, "label": perspective.label, "asked": len(items),
                                   "error": exc.message, "checks": [], "skipped": [], "summary": "", "found": []})
                    continue
                usage.append(call.usage)
                merge_pass_findings(review["findings"], done.pop("findings"))
                passes.append(done)
            for done in passes:
                done["found"] = [f["title"] for f in review["findings"]
                                 if done["key"] in (f.get("perspectives") or [f.get("perspective")])]
            review.update(passes=passes, depth="deep")
        mark_decided(review["findings"], decisions)
        review["created_at"] = now_iso()
        review["guides_checked"] = [e.id for e in entries]
        flagged = [f["guide"] for f in review["findings"] if f.get("guide")]
        if guides is not None and flagged:
            guides.record("flagged", flagged, by="AI 검토", **(context or {"project_id": project_id}))
        return review, usage

    # --- review findings kept as designed ----------------------------------------------------------

    def _remark(project_id: str, board_id: str) -> None:
        """Re-mark the open proposals' reviews after the decisions changed (no AI call)."""
        decisions = store.get_review_decisions(project_id, board_id)
        for p in proposals.list(project_id, board_id):
            review = (p.ai or {}).get("review")
            if p.status in ("checking", "ready", "invalid", "check_failed") and review and review.get("findings"):
                mark_decided(review["findings"], decisions)
                proposals.update(p, ai={**(p.ai or {}), "review": review})

    def note_declined(project_id: str, board_id: str, proposal_id: str, not_addressed: list[dict]) -> None:
        """Keep the AI's reason for not fixing a finding on the finding itself. A refusal makes no new proposal, so
        without this the reason lives only in the job message and the person deciding never sees it."""
        try:
            p = proposals.get(project_id, board_id, proposal_id)
        except Exception:  # noqa: BLE001 - the parent may be gone; the job message still carries the reason
            return
        review = (p.ai or {}).get("review")
        if not review or not review.get("findings"):
            return
        for f in review["findings"]:
            hit = next((x for x in not_addressed if x.get("title") == f.get("title")), None) \
                or next((x for x in not_addressed if similar(f.get("title"), x.get("title"))), None)
            if hit is not None and hit.get("why"):
                f["ai_declined"] = str(hit["why"])
        proposals.update(p, ai={**(p.ai or {}), "review": review})

    @router.get("/api/projects/{project_id}/boards/{board_id}/review/decisions")
    def review_decisions(project_id: str, board_id: str):
        return {"decisions": store.get_review_decisions(project_id, board_id)}

    @router.post("/api/projects/{project_id}/boards/{board_id}/review/decisions", status_code=201)
    def add_review_decision(project_id: str, board_id: str, body: DecisionBody):
        """A person keeps a review finding as designed, with the reason. Later reviews are told and do not count it;
        nothing in the circuit changes."""
        decisions = store.get_review_decisions(project_id, board_id)
        entry = {"id": f"dec-{len(decisions) + 1:03d}-{int(time.time())}", "title": body.title.strip(),
                 "reason": body.reason.strip(), "proposal_id": body.proposal_id, "by": "engineer", "at": now_iso()}
        store.put_review_decisions(project_id, board_id, [*decisions, entry])
        _remark(project_id, board_id)
        return {"decision": entry}

    @router.delete("/api/projects/{project_id}/boards/{board_id}/review/decisions/{decision_id}")
    def remove_review_decision(project_id: str, board_id: str, decision_id: str):
        decisions = store.get_review_decisions(project_id, board_id)
        if not any(d.get("id") == decision_id for d in decisions):
            raise ApiError(404, "NOT_FOUND", "그런 결정이 없습니다.")
        store.put_review_decisions(project_id, board_id, [d for d in decisions if d.get("id") != decision_id])
        _remark(project_id, board_id)
        return {"ok": True}

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
                    plan: dict | None = None, fetch_datasheets: bool = False):
        """Check the inputs now and return the design work as ``run(job_id)``. ``base`` is the design being revised
        (None for a new design); the result's diff against it is recorded so the user sees what the revision changed.
        Steps (parts, datasheets, pins, check, proposal, review) are recorded on the job for the step screens.
        ``fetch_datasheets``: the person allowed downloading the chosen parts' datasheets and summarizing them with
        the AI in this job (backend.datasheet_flow)."""
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

            prep = DatasheetPrep(library=datasheets, symbol_url=lib.datasheet_url, fetcher=settings.datasheet_fetcher,
                                 searchers=settings.datasheet_searchers(settings.keystore) if fetch_datasheets else [],
                                 provider=provider, cfg=cfg, job_id=job_id, step=lambda t: step("datasheets", t),
                                 fetch=fetch_datasheets)

            def datasheets_for_parts(parts, blocks) -> None:
                """The chosen parts' datasheets, before the pins are connected: what the summaries say (required
                external parts, recommended ranges) goes into the connection prompt."""
                comps = [Component(id=x.ref.lower(), reference=x.ref, value=x.value or "?", symbol_id=x.symbol_id,
                                   manufacturer=x.manufacturer, manufacturer_part_number=x.mpn,
                                   pins=[{"number": n} for n, _, _ in x.pins]) for x in parts]
                step("datasheets", "고른 부품의 데이터시트를 찾습니다" if fetch_datasheets
                     else "보관된 데이터시트 요약을 찾습니다 (자동 받기 꺼짐)")
                prep.run([*comps, *(c for b in blocks for c in b.components)])
                step("datasheets", report_text(prep.report()))
                for x, c in zip(parts, comps, strict=True):
                    entry = datasheets.find(c, lib.datasheet_url(c.symbol_id))
                    x.datasheet_notes = (facts_notes(datasheets.facts(entry)) if entry else None) or x.datasheet_notes

            def guides_for_parts(parts, blocks) -> str:
                """Guide text for the connection step: the plan's guides plus those the chosen parts bring."""
                if guides is None:
                    return inp.guides
                comps = [{"id": x.ref.lower(), "reference": x.ref, "value": x.value or "?", "symbol_id": x.symbol_id,
                          "manufacturer_part_number": x.mpn, "description": x.role[:200] or None,
                          "pins": [{"number": n, "name": nm if nm and nm != "~" else None} for n, nm, _ in x.pins]}
                         for x in parts] + [c.model_dump(mode="json") for b in blocks for c in b.components]
                try:
                    provisional = CircuitIR.model_validate({"project_id": project_id, "components": comps, "nets": []})
                except ValidationError:
                    return inp.guides
                chosen = relevant(guides.entries(), circuit=provisional, text=f"{text}\n{instructions or ''}",
                                  text_topics=True)
                merged = list({e.id: e for e in [*chosen, *entries]}.values())  # strongest first, within the size limit
                entries[:] = merged  # what the proposal records as offered
                return guide_prompt_text(merged)

            def guide_pass(circuit: CircuitIR) -> tuple[CircuitIR, dict | None]:
                """Run the app's own checks behind the adopted guides on a fresh design; must/should violations are fixed
                with one edit pass before anyone reviews it. Kept only if it leaves fewer violations."""
                if guides is None:
                    return circuit, None

                def violations(c: CircuitIR) -> list[dict]:
                    return [r for r in circuit_checklist(store, guides, settings, project_id, c)
                            if r["status"] in ("fail", "warn") and r["strength"] in ("must", "should")]

                bad = violations(circuit)
                if not bad:
                    step("check", "설계 지침 자동 점검: 위반 없음")
                    return circuit, {"before": [], "after": []}
                step("check", f"설계 지침 자동 점검: 위반 {len(bad)}개 ({', '.join(r['id'] for r in bad[:4])}) → 고칠 곳만 편집")
                asked = [{"severity": "error" if r["strength"] == "must" else "warning", "title": f"{r['id']} {r['title']}",
                          "detail": r["rule"], "evidence": "; ".join(r["notes"])[:300] or None, "suggestion": None,
                          "source": "guide"} for r in bad]
                applicable = relevant(guides.entries(), circuit=circuit, text=text, text_topics=False)
                ginp = DesignInput(**{**inp.__dict__, "guides": guide_prompt_text(applicable)})
                info = {"before": [r["id"] for r in bad], "after": [r["id"] for r in bad]}
                try:
                    # Its steps are part of the check: logged under "parts"/"pins" they would replace what the
                    # design step rows show ("부품 41개 선택", "연결 완료").
                    fixed = revise_by_edits(provider, ginp, circuit, asked, resolver,
                                            max_output_tokens=cfg.max_output_tokens, max_repairs=1,
                                            on_step=lambda _s, text, *a, **k: step("check", text, *a, **k))
                except AiError as exc:
                    step("check", f"지침 위반을 고치지 못함: {exc.message}")
                    return circuit, {**info, "error": exc.message}
                record.setdefault("usage", []).extend(c.usage for c in fixed.calls)
                if fixed.circuit is None:
                    step("check", "지침 위반을 편집으로 고치지 못함 — 검토에서 다룹니다")
                    return circuit, {**info, "not_addressed": fixed.not_addressed}
                left = violations(fixed.circuit)
                if len(left) >= len(bad):
                    step("check", "편집해도 지침 위반이 줄지 않아 원래 설계를 둡니다")
                    return circuit, info
                step("check", f"지침 위반 고침: {len(bad)} → {len(left)} ({', '.join(fixed.changes[:4])})")
                return fixed.circuit, {"before": info["before"], "after": [r["id"] for r in left], "changes": fixed.changes,
                                       "not_addressed": fixed.not_addressed}

            def finish(circuit: CircuitIR, ai: dict, title: str, out: dict) -> dict:
                """Guide self-check → proposal → isolated KiCad check → AI review (of a revision: with what it tried
                to fix)."""
                circuit, ai["guide_check"] = guide_pass(circuit)
                if prep.run(circuit.components):
                    # Parts not seen before the pins were connected: a revision's new parts, a guide fix's.
                    step("datasheets", report_text(prep.report()))
                ai["datasheets"] = prep.report()
                record.setdefault("usage", []).extend(prep.usage)
                if base is not None and ai.get("diff_from_parent") is not None:
                    ai["diff_from_parent"] = diff_circuits(base, circuit)
                p = proposals.create(project_id, board_id, title=title, ops=[ReplaceCircuit(circuit=circuit)],
                                     source=source, rationale=ai["summary"][:20_000], ai=ai)
                applied = (ai.get("guides") or {}).get("applied") or []
                if guides is not None and applied:
                    guides.record("cited", applied, by="AI 설계", project_id=project_id, board_id=board_id, proposal_id=p.id)
                step("check", "KiCad로 회로도를 만들어 ERC 실행")
                p = service.check_now(p)
                step("check", f"KiCad 검사: {_check_text(p)}")
                step("proposal", f"제안 #{int(p.id)} 만듦 (승인 전에는 회로가 바뀌지 않음)")
                if review:
                    step("review", "AI 검토 중")
                    try:
                        findings_out, used = run_review(cfg, provider, project_id, circuit, proposal_evidence_text(p),
                                                        {"project_id": project_id, "board_id": board_id,
                                                         "proposal_id": p.id},
                                                        previous=ai.get("addressed") if base is not None else None)
                        record.setdefault("usage", []).extend(used)
                        p = proposals.update(p, ai={**(p.ai or {}), "review": findings_out})
                        step("review", "AI 검토: " + _count_text(findings_out["findings"]))
                    except AiError as exc:
                        step("review", f"AI 검토 실패: {exc.message}")
                        p = proposals.update(p, ai={**(p.ai or {}), "review": {"summary": f"AI 검토 실패: {exc.message}",
                                                                               "findings": []}})
                record.update(status="succeeded", output={"proposal": p.id, "status": p.status})
                save_run(project_id, board_id, record)
                return {"proposal_id": p.id, "status": p.status, **out}

            if base is not None:
                # A revision first edits the current circuit; only what it cannot do that way is designed again.
                try:
                    er = revise_by_edits(provider, inp, base, findings or [], resolver,
                                         max_output_tokens=cfg.max_output_tokens, on_step=step)
                except AiError as exc:
                    if exc.code != "AI_BAD_OUTPUT":
                        record.update(status="failed", error={"code": exc.code, "message": exc.message})
                        save_run(project_id, board_id, record)
                        raise api_error(exc) from exc
                    er = None  # unreadable edits even after asking again: a redesign may still work
                    record.update(edit_error={"message": exc.message, "details": exc.details})
                    step("parts", f"편집 응답을 읽지 못해 회로를 다시 설계합니다: {exc.message}")
                if er is not None:
                    record.update(usage=[c.usage for c in er.calls], method="edits", edit_attempts=er.attempts,
                                  edit_problems=er.problems)
                if er is not None and er.circuit is not None and not er.changes:
                    # Edits that leave the circuit as it was: say so instead of a new proposal identical to its parent.
                    record.update(status="no_change")
                    save_run(project_id, board_id, record)
                    raise ApiError(422, "AI_NO_CHANGE",
                                   "AI가 낸 편집이 회로를 바꾸지 않았습니다 (모두 지금과 같음). 새 제안을 만들지 않았습니다.",
                                   recovery_hint="고칠 내용을 직접 적어 다시 요청하거나, 의견을 직접 판단하세요.",
                                   details={"edits": er.edits[:20]})
                if er is not None and er.circuit is not None:
                    ai = {"model": er.calls[-1].model, "method": "edits", "summary": er.summary, "edits": er.edits,
                          "changes": er.changes, "assumptions": [], "questions": [], "citations": [],
                          "parts": [{"key": x.ref, "role": x.role, "symbol_id": x.symbol_id, "how": x.how} for x in er.added],
                          "templates": [], "attempts": er.attempts, "remaining_problems": [], "notes": "", "parent": parent,
                          "review": None, "instructions": instructions,
                          "addressed": [{"severity": f.get("severity"), "title": f.get("title"), "detail": f.get("detail"),
                                         "source": f.get("source", "ai")} for f in er.addressed],
                          "not_addressed": er.not_addressed,
                          "diff_from_parent": diff_circuits(base, er.circuit),
                          "guides": {"offered": [e.id for e in entries], "applied": []}}
                    title = revision_title(er.summary, er.changes)
                    return finish(er.circuit, ai, title, {"attempts": er.attempts, "remaining_problems": [],
                                                          "method": "edits"})
                if er is not None and er.declined:
                    record.update(status="declined")
                    save_run(project_id, board_id, record)
                    if parent:
                        note_declined(project_id, board_id, parent, er.not_addressed)
                    reasons = " / ".join(f"{x['title']}: {x['why']}" for x in er.not_addressed[:4])
                    raise ApiError(422, "AI_NOTHING_TO_FIX", f"AI가 남은 의견을 고치지 않기로 했습니다. {reasons}",
                                   recovery_hint="의견을 직접 판단하거나, 고칠 내용을 직접 적어 수정안을 요청하세요.",
                                   details={"not_addressed": er.not_addressed})
                if er is not None:
                    step("parts", "고칠 곳만으로는 해결하지 못해 회로를 다시 설계합니다: " + " / ".join(er.problems[:2]))
            try:
                result = design_circuit(provider, inp, resolver, project_id=project_id,
                                        max_output_tokens=cfg.max_output_tokens, pins_of=_pins_of(lib), on_step=step,
                                        plan=plan, guides_for_parts=guides_for_parts,
                                        after_parts=datasheets_for_parts)
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message,
                                                     **({"details": exc.details} if exc.details else {})})
                save_run(project_id, board_id, record)
                raise api_error(exc) from exc
            record.update(usage=[*record.get("usage", []), *(c.usage for c in result.calls)], attempts=result.attempts,
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
                                   plan=plan_out, instructions=instructions, use_inventory=use_inventory, review=review,
                                   datasheets=fetch_datasheets)
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
            ai["method"] = "redesign"
            return finish(result.circuit, ai, str(plan_out.get("title") or "AI 설계안")[:150],
                          {"attempts": result.attempts, "remaining_problems": result.problems})

        return run

    def submit_design(project_id: str, board_id: str, **kw):
        return jobs.submit(project_id, kw["source"], design_task(project_id, board_id, **kw), board_id=board_id)

    @router.post(BOARD + "/ai/design", status_code=202)
    def design(project_id: str, board_id: str, body: DesignBody):
        return submit_design(project_id, board_id, instructions=body.instructions, use_inventory=body.use_inventory,
                             review=body.review, base=None, findings=None, source="ai_design", parent=None,
                             fetch_datasheets=body.datasheets)

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
              "base": None, "findings": None, "source": "ai_design", "parent": None,
              "fetch_datasheets": bool(details.get("datasheets", False))}
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
                             parent=body.proposal_id, fetch_datasheets=body.datasheets)

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
                fresh = (not review or "findings" not in review
                         or str(review.get("summary", "")).startswith("AI 검토 실패"))
                # A review made without the perspective passes (the design job's) gets them added, not redone.
                if fresh or (body.deep and review.get("depth") != "deep"):
                    jobs.step(job_id, "review", f"{k}회차: 제안 #{int(p.id)} AI 검토 중" if fresh
                              else f"{k}회차: 제안 #{int(p.id)} 관점별 검토를 더합니다")
                    try:
                        review, _ = run_review(cfg, provider, project_id, proposals.candidate(p), proposal_evidence_text(p),
                                               {"project_id": project_id, "board_id": board_id, "proposal_id": p.id},
                                               previous=(p.ai or {}).get("addressed") or None, deep=body.deep,
                                               base=None if fresh else review, changes=(p.ai or {}).get("changes"),
                                               step=lambda text, k=k: jobs.step(job_id, "review", f"{k}회차: {text}"))
                    except AiError as exc:
                        stop, message, why = "ai_failed", exc.message, exc.details
                        break
                    p = proposals.update(proposals.get(project_id, board_id, p.id), ai={**(p.ai or {}), "review": review})
                # Only findings whose basis checked out count and go into a revision; the rest stay visible.
                needs, recommended, unverified = counted(review["findings"])
                failed_check = p.status in ("invalid", "check_failed")
                entry = {"round": k, "proposal_id": p.id, "errors": len(needs) + (1 if failed_check else 0),
                         "warnings": len(recommended), "check": _check_text(p), "check_failed": failed_check,
                         "said": [str(f.get("title", "")) for f in [*needs, *recommended]][:4],
                         "unverified": len(unverified), "unverified_said": [str(f.get("title", "")) for f in unverified][:4]}
                kept = [f for f in review["findings"] if f.get("decided")]
                if kept:
                    entry.update(decided=len(kept), decided_said=[str(f.get("title", "")) for f in kept][:4])
                if review.get("passes"):
                    checks = [c for x in review["passes"] for c in x.get("checks") or []]
                    entry.update(passes=len(review["passes"]), checks=len(checks),
                                 unknown=sum(1 for c in checks if c.get("result") == "unknown"),
                                 unknown_said=[re.sub(r"^\(\d+\)\s*", "", str(c.get("item", "")))
                                               for c in checks if c.get("result") == "unknown"][:6])
                if (p.ai or {}).get("not_addressed"):
                    entry["declined"] = [f"{x.get('title')}: {x.get('why')}" for x in p.ai["not_addressed"]][:4]
                prev = rounds[-1] if rounds else None
                rounds.append(entry)
                again = sorted({str(f.get("title")) for f in [*needs, *recommended]
                                if any(similar(f.get("title"), a) for a in asked)})
                if entry["errors"] == 0 and entry["warnings"] <= body.max_warnings:
                    stop = "met"
                elif failed_check and k > 1:
                    stop = "check_failed"
                elif k == body.max_rounds:
                    stop = "max_rounds"
                elif again:
                    stop, entry["repeated"] = "repeated", again
                elif prev is not None and (entry["errors"], entry["warnings"]) >= (prev["errors"], prev["warnings"]):
                    stop = "no_progress"  # the revision did not reduce what is left: another round will not either
                else:
                    stop = ""
                jobs.step(job_id, "round", f"{k}회차: 제안 #{int(p.id)} 수정 필요 {entry['errors']} · 수정 권장 "
                                            f"{entry['warnings']}", data=entry)
                if stop:
                    break
                chosen = [{**f, "source": "ai"} for f in [*needs, *recommended]] + proposal_check_findings(p)
                asked |= {str(f.get("title")) for f in [*needs, *recommended]}
                jobs.step(job_id, "revise", f"{k}회차: 의견 {len(chosen)}개를 반영한 수정안 만드는 중")
                try:
                    out = design_task(project_id, board_id, instructions=None, use_inventory=True, review=True,
                                      base=proposals.candidate(p), findings=chosen, source="ai_revision",
                                      parent=p.id, fetch_datasheets=body.datasheets)(job_id)
                except ApiError as exc:
                    stop = {"AI_NOTHING_TO_FIX": "declined", "AI_NO_CHANGE": "no_change"}.get(exc.error_code, "revise_failed")
                    message, why = exc.message, (exc.details if stop == "revise_failed" else None)
                    break
                revised = proposals.get(project_id, board_id, out["proposal_id"])
                diff = ((revised.ai or {}).get("diff_from_parent") or {}).get("summary")
                entry["revised_to"] = revised.id
                entry["fix"] = {"summary": str((revised.ai or {}).get("summary") or "")[:300], "diff": diff,
                                "method": (revised.ai or {}).get("method", "redesign")}
                jobs.step(job_id, "revised", f"{k}회차: 수정안 #{int(revised.id)} 만듦", data=entry)
                p = revised
                if diff is not None and not any(diff.values()):
                    stop = "no_change"  # the revision is the same circuit: reviewing it again changes nothing
                    break
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
                result, used = run_review(cfg, provider, project_id, circuit, evidence,
                                          {"project_id": project_id, "board_id": board_id,
                                           **({"proposal_id": body.proposal_id} if body.proposal_id else {})},
                                          deep=body.deep, step=lambda text: jobs.step(job_id, "review", text))
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
            record.update(status="succeeded", usage=used[0] if len(used) == 1 else used,
                          output={"findings": len(result["findings"]), "passes": len(result.get("passes") or [])})
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
