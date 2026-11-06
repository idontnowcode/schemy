"""Runs the AI performance evaluation (``circuit_core.evals``) with the configured AI and keeps the results.

    <workspace>/evals/runs/<run-id>.json   one file per run: settings, every result, the summary

Only the repository's example boards are sent (their requirements and circuits), plus the adopted design-guide
entries the designer would see anyway; nothing from the user's projects. The runner never creates proposals or
touches a board: designs are scored in memory and checked with KiCad in a temporary folder.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from ai_engine import AiError, LlmResult
from ai_engine.design import DesignInput, circuit_text, design_circuit, review_circuit
from ai_engine.notes import write_note
from circuit_core import CircuitIR, validate_circuit
from circuit_core.engineering import engineering_review
from circuit_core.evals import (
    DESIGN_CASES,
    NOTE_EXAMPLES,
    REVIEW_CASES,
    found_by_ai,
    found_by_code,
    plant,
    report_markdown,
    run_check,
    summarize,
    usage_tokens,
)
from kicad_adapter import SchematicGenerationError, SymbolLibrary, SymbolLibraryError, generate_schematic

from .errors import ApiError
from .exports import _run_checks
from .notes import collect_evidence
from .store import atomic_write, now_iso

SUITES = ("design", "review", "notes")
CALLS_PER_DESIGN = 3
"""Plan + connect, plus usually one repair: used only to estimate the cost before a run."""
_RUN_ID = re.compile(r"^[0-9T-]{15,}-[0-9a-f]{6}$")


class Recording:
    """Wraps a provider: counts calls and tokens and keeps the last raw answer."""

    def __init__(self, provider):
        self.provider, self.calls, self.input, self.output, self.last = provider, 0, 0, 0, ""
        self.name, self.model = getattr(provider, "name", ""), getattr(provider, "model", "")

    def generate(self, **kwargs) -> LlmResult:
        result = self.provider.generate(**kwargs)
        self.calls += 1
        i, o = usage_tokens(result.usage)
        self.input, self.output, self.last = self.input + i, self.output + o, result.text or ""
        return result

    def list_models(self):
        return self.provider.list_models()

    def cost(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input, "output_tokens": self.output}


def plan_items(suites: list[str], case_ids: list[str] | None, repeats: int) -> list[dict]:
    """Everything a run will do, in order (also used for the cost estimate shown before the run)."""
    wanted = set(case_ids or [])
    items = []
    for rep in range(1, repeats + 1):
        if "design" in suites:
            items += [{"suite": "design", "case": c.id, "repeat": rep} for c in DESIGN_CASES if not wanted or c.id in wanted]
        if "review" in suites:
            defects = [d for d in REVIEW_CASES if not wanted or d.id in wanted]
            items += [{"suite": "review", "case": d.id, "repeat": rep, "defect": d.id} for d in defects]
            for example in dict.fromkeys(d.example for d in defects):  # the same boards without a defect
                items.append({"suite": "review", "case": f"clean:{example}", "repeat": rep, "defect": None,
                              "example": example})
        if "notes" in suites:
            items += [{"suite": "notes", "case": f"note:{e}", "repeat": rep, "example": e} for e in NOTE_EXAMPLES
                      if not wanted or f"note:{e}" in wanted]
    return items


def estimate_calls(items: list[dict]) -> int:
    return sum(CALLS_PER_DESIGN if i["suite"] == "design" else 1 for i in items)


class EvalRunner:
    def __init__(self, workspace: Path, *, examples_dir: Path | None, symbol_dirs: list[Path],
                 index: Callable[[], object] | None = None, resolver_factory: Callable | None = None,
                 kicad_factory: Callable | None = None, guides_text: Callable[[CircuitIR | None, str], str] | None = None):
        self.dir = Path(workspace) / "evals" / "runs"
        self.examples_dir = examples_dir
        self.symbol_dirs = symbol_dirs
        self.resolver_factory = resolver_factory
        self.kicad_factory = kicad_factory
        self.guides_text = guides_text or (lambda circuit, text: "")
        self.progress: dict[str, dict] = {}
        self._lock = threading.Lock()

    # --- examples ---------------------------------------------------------------------------------

    def _example(self, name: str) -> tuple[str, CircuitIR]:
        if self.examples_dir is None or not (self.examples_dir / name).is_dir():
            raise ApiError(409, "NO_EXAMPLES", "예제 보드 폴더를 찾지 못해 평가할 수 없습니다.",
                           recovery_hint="앱을 다시 설치하거나 AICS_EXAMPLES_DIR을 확인하세요.")
        folder = self.examples_dir / name
        req = folder / "requirements.md"
        return (req.read_text(encoding="utf-8") if req.is_file() else "",
                CircuitIR.model_validate_json((folder / "circuit.json").read_text(encoding="utf-8")))

    # --- one item each --------------------------------------------------------------------------------

    def _design(self, ai: Recording, cfg, case) -> dict:
        requirements, _ = self._example(case.example)
        lib = SymbolLibrary(self.symbol_dirs) if self.symbol_dirs else None
        if lib is None or self.resolver_factory is None:
            raise ApiError(409, "NO_SYMBOL_LIBRARY", "KiCad 심볼 라이브러리가 없어 설계 평가를 할 수 없습니다.")

        def pins_of(symbol_id: str):
            try:
                return [(p.number, p.name) for p in lib.get(symbol_id).pins]
            except SymbolLibraryError:
                return None

        inp = DesignInput(requirements=requirements, board_name=case.title, guides=self.guides_text(None, requirements))
        result = design_circuit(ai, inp, self.resolver_factory(lib), project_id="eval",
                                max_output_tokens=cfg.max_output_tokens, pins_of=pins_of)
        out = {"attempts": result.attempts, "problems": result.problems[:10], "unresolved": result.unresolved[:10],
               "completed": result.circuit is not None, "templates": len(result.templates)}
        c = result.circuit
        if c is None:
            return out
        issues = validate_circuit(c).issues
        calc = engineering_review(c)
        decided = {cid for d in c.decisions for cid in d.components}
        checks = [{"label": ch.label, "ok": run_check(ch, c)} for ch in case.checks]
        out.update(
            components=len(c.components), nets=len(c.nets),
            ir_errors=sum(1 for i in issues if i.severity.value == "error"),
            ir_warnings=sum(1 for i in issues if i.severity.value == "warning"),
            calc={"fail": sum(1 for f in calc if f.severity == "fail"), "warn": sum(1 for f in calc if f.severity == "warn"),
                  "fails": [f.title for f in calc if f.severity == "fail"][:10]},
            reasons=round(len(decided & {x.id for x in c.components}) / len(c.components), 3) if c.components else 0.0,
            requirements={"rate": round(sum(x["ok"] for x in checks) / len(checks), 3) if checks else 1.0, "checks": checks},
        )
        out["kicad"] = self._kicad(c, ir_errors=out["ir_errors"])
        return out

    def _kicad(self, circuit: CircuitIR, ir_errors: int) -> dict:
        if self.kicad_factory is None or not self.symbol_dirs or ir_errors:
            return {"status": "not_checked"}
        try:
            generated = generate_schematic(circuit, SymbolLibrary(self.symbol_dirs), project_name="eval")
        except SchematicGenerationError as exc:
            return {"status": "generation_failed", "issues": [i.message for i in exc.issues][:5]}
        with tempfile.TemporaryDirectory(prefix="aics-eval-") as tmp:
            sch = generated.write(Path(tmp))
            check = _run_checks(circuit, generated, sch, self.kicad_factory)
        return {"status": check.status, "erc_errors": check.erc_errors, "erc_warnings": check.erc_warnings,
                "netlist_ok": check.netlist_matches_ir, "reason": check.reason}

    def _review(self, ai: Recording, item: dict) -> dict:
        defect = next((d for d in REVIEW_CASES if d.id == item.get("defect")), None)
        requirements, clean = self._example(defect.example if defect else item["example"])
        circuit = plant(defect, clean) if defect else clean
        evidence = "\n".join(f"- IR {i.severity.value}: {i.error_code} {i.message}" for i in validate_circuit(circuit).issues)
        review, _ = review_circuit(ai, requirements=requirements, spec=None, circuit=circuit, evidence=evidence,
                                   guides=self.guides_text(circuit, requirements))
        findings = review["findings"]
        out = {"completed": True, "findings": len(findings),
               "errors": sum(1 for f in findings if f["severity"] == "error"),
               "warnings": sum(1 for f in findings if f["severity"] == "warning"),
               "defect": defect.id if defect else None, "defect_title": defect.title if defect else None}
        if defect:
            hit = found_by_ai(defect, findings)
            out.update(found=hit is not None, found_by=hit["title"] if hit else None,
                       found_by_code=found_by_code(defect, circuit))
        return out

    def _note(self, ai: Recording, item: dict) -> dict:
        requirements, circuit = self._example(item["example"])
        evidence = collect_evidence(circuit, requirements=requirements, datasheet_rows=[], datasheet_notes=[],
                                    guide_items=[], kicad=[], review=None)
        draft, _ = write_note(ai, requirements=requirements, circuit=circuit_text(circuit), evidence=evidence)
        known = {e.id for e in evidence}
        try:
            raw = json.loads(ai.last)
        except ValueError:
            raw = {}
        raw_ids = [str(i).strip().upper().strip("[]") for group in ("checks", "learnings", "apply_next", "open_questions")
                   for x in raw.get(group) or [] if isinstance(x, dict) for i in x.get("evidence") or []]
        lines = [*draft.checks, *draft.learnings, *draft.apply_next, *draft.open_questions]
        texts = [getattr(x, "text", "") or getattr(x, "what", "") for x in lines] + \
                [f"{x.how} {x.result}" for x in draft.checks] + [x.why for x in draft.apply_next]
        filled = [bool(draft.summary), bool(draft.background), bool(draft.checks), bool(draft.learnings),
                  bool(draft.apply_next), bool(draft.open_questions)]
        return {"completed": True, "lines": len(lines),
                "cited_rate": round(sum(1 for x in lines if x.evidence) / len(lines), 3) if lines else None,
                "bad_ids": sum(1 for i in raw_ids if i not in known),
                "page_flags": sum(t.count("[쪽 번호 근거 없음") for t in texts),
                "sections": round(sum(filled) / len(filled), 3), "evidence": len(evidence)}

    # --- a run ------------------------------------------------------------------------------------------

    def run(self, provider, cfg, *, suites: list[str], case_ids: list[str] | None, repeats: int, job_id: str,
            app_version: str = "") -> dict:
        items = plan_items(suites, case_ids, repeats)
        if not items:
            raise ApiError(422, "NOTHING_TO_RUN", "고른 묶음·사례에 실행할 것이 없습니다.")
        run_id = f"{now_iso()[:19].replace(':', '')}-{uuid.uuid4().hex[:6]}"
        results = []
        with self._lock:
            self.progress[job_id] = {"run_id": run_id, "done": 0, "total": len(items), "current": None}
        for n, item in enumerate(items):
            with self._lock:
                self.progress[job_id].update(done=n, current=f"{item['suite']} · {item['case']} · {item['repeat']}회")
            ai = Recording(provider)
            started = time.monotonic()
            res = {**item}
            try:
                if item["suite"] == "design":
                    res.update(self._design(ai, cfg, next(c for c in DESIGN_CASES if c.id == item["case"])))
                elif item["suite"] == "review":
                    res.update(self._review(ai, item))
                else:
                    res.update(self._note(ai, item))
            except AiError as exc:
                res.update(completed=False, error=f"[{exc.code}] {exc.message}")
            except ApiError:
                raise
            except Exception as exc:  # noqa: BLE001 - one broken case must not lose the whole run
                res.update(completed=False, error=f"[INTERNAL] {exc}")
            res.update(**ai.cost(), seconds=round(time.monotonic() - started, 2))
            results.append(res)
        kicad = any(r.get("kicad", {}).get("status") in ("passed", "failed") for r in results)
        run = {"id": run_id, "created_at": now_iso(), "created_ns": time.time_ns(), "provider": cfg.provider, "model": cfg.model,
               "app_version": app_version, "suites": suites, "cases": case_ids or [], "repeats": repeats,
               "kicad": kicad, "results": results,
               "summary": summarize(results)}
        atomic_write(self.dir / f"{run_id}.json", json.dumps(run, ensure_ascii=False, indent=2) + "\n")
        with self._lock:
            self.progress[job_id].update(done=len(items), current=None)
        return run

    # --- stored runs --------------------------------------------------------------------------------------

    def runs(self) -> list[dict]:
        """Stored runs, newest first."""
        out = []
        for path in self.dir.glob("*.json"):
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append({k: run.get(k) for k in ("id", "created_at", "created_ns", "provider", "model", "suites",
                                                 "repeats", "summary", "app_version", "kicad")})
        return sorted(out, key=lambda r: (r.get("created_ns") or 0, r["id"]), reverse=True)

    def get(self, run_id: str) -> dict:
        path = self.dir / f"{run_id}.json"
        if not _RUN_ID.fullmatch(run_id) or not path.is_file():
            raise ApiError(404, "EVAL_RUN_NOT_FOUND", f"평가 기록 '{run_id}'가 없습니다.")
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, run_id: str) -> None:
        self.get(run_id)
        (self.dir / f"{run_id}.json").unlink()

    def previous(self, run: dict) -> dict | None:
        """The run before this one that measured the same suites (to show what changed)."""
        older = [r for r in self.runs() if (r.get("created_ns") or 0) < (run.get("created_ns") or 0)
                 and set(r["suites"] or []) == set(run["suites"])]
        return self.get(older[0]["id"]) if older else None

    def report(self, run: dict, compare_to: dict | None) -> str:
        return report_markdown(run, compare_to)
