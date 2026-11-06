"""AI performance evaluation API.

    GET  /api/evals/cases            the test cases and what a run would send / cost
    POST /api/evals/runs             start a run (job); body: suites, cases (optional), repeats
    GET  /api/evals/progress/{job}   how far a running evaluation is
    GET  /api/evals/runs             stored runs (newest first) with their summaries
    GET  /api/evals/runs/{id}        one run, its report, and the change from the previous comparable run
    GET  /api/evals/runs/{id}/report.md   the report as a file
    DELETE /api/evals/runs/{id}
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from circuit_core.evals import DESIGN_CASES, METRIC_LABELS, NOTE_EXAMPLES, REVIEW_CASES, compare

from .ai_access import AiAccess
from .config import APP_VERSION, Settings
from .errors import ApiError
from .evals import SUITES, EvalRunner, estimate_calls, plan_items
from .jobs import JobRunner
from .store import ProjectStore

EVAL_JOBS = "_evals"
"""Jobs need a project id; evaluation runs belong to the workspace."""


class EvalRequest(BaseModel):
    suites: list[str] = Field(default_factory=lambda: list(SUITES), min_length=1)
    cases: list[str] | None = None
    repeats: int = Field(default=1, ge=1, le=5)


def build_eval_router(store: ProjectStore, jobs: JobRunner, settings: Settings, runner: EvalRunner) -> APIRouter:
    router = APIRouter()
    access = AiAccess(store, settings)

    @router.get("/api/evals/cases")
    def cases():
        cfg = access.settings()
        per_suite = {s: estimate_calls(plan_items([s], None, 1)) for s in SUITES}
        return {
            "design": [{"id": c.id, "title": c.title, "example": c.example, "checks": [ch.label for ch in c.checks]}
                       for c in DESIGN_CASES],
            "review": [{"id": d.id, "title": d.title, "example": d.example} for d in REVIEW_CASES],
            "notes": [{"id": f"note:{e}", "example": e} for e in NOTE_EXAMPLES],
            "calls_per_repeat": per_suite, "provider": cfg.provider, "model": cfg.model, "enabled": cfg.enabled,
            "kicad": settings.kicad_factory() is not None, "metrics": {k: v[0] for k, v in METRIC_LABELS.items()},
        }

    @router.post("/api/evals/runs", status_code=202)
    def start(body: EvalRequest):
        unknown = [s for s in body.suites if s not in SUITES]
        if unknown:
            raise ApiError(422, "UNKNOWN_SUITE", f"모르는 평가 묶음: {', '.join(unknown)}")
        if not plan_items(body.suites, body.cases, body.repeats):
            raise ApiError(422, "NOTHING_TO_RUN", "고른 묶음·사례에 실행할 것이 없습니다.")
        cfg = access.settings()
        provider = access.provider_for(cfg)

        def run(job_id: str) -> dict:
            result = runner.run(provider, cfg, suites=body.suites, case_ids=body.cases, repeats=body.repeats,
                                job_id=job_id, app_version=APP_VERSION)
            return {"run_id": result["id"], "summary": result["summary"]}

        return jobs.submit(EVAL_JOBS, "ai_eval", run)

    @router.get("/api/evals/progress/{job_id}")
    def progress(job_id: str):
        return runner.progress.get(job_id) or {"done": 0, "total": 0, "current": None}

    @router.get("/api/evals/runs")
    def runs():
        return {"runs": runner.runs()}

    @router.get("/api/evals/runs/{run_id}")
    def get_run(run_id: str, compare_to: str | None = None):
        run = runner.get(run_id)
        previous = runner.get(compare_to) if compare_to else runner.previous(run)
        return {"run": run, "previous_id": previous["id"] if previous else None,
                "changes": compare(run["summary"], previous["summary"]) if previous else [],
                "markdown": runner.report(run, previous)}

    @router.get("/api/evals/runs/{run_id}/report.md", response_class=PlainTextResponse)
    def report(run_id: str):
        run = runner.get(run_id)
        return PlainTextResponse(runner.report(run, runner.previous(run)), media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="ai-eval-{run_id}.md"'})

    @router.delete("/api/evals/runs/{run_id}", status_code=204)
    def delete(run_id: str):
        runner.delete(run_id)

    return router
