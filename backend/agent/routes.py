"""Agent runs API (plan section 8): create (dry run), read with its log, approve the scope, cancel.

The result of a run is a proposal; it is approved or rejected through the one approval path
(``/proposals/{id}/approve|reject``), which decides COMMITTED or REJECTED for the run."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .gateway import EDA_TOOLS, ScopeItem
from .runner import AgentLimits, AgentRunner

BOARD = "/api/projects/{project_id}/boards/{board_id}"


class AgentRunCreate(BaseModel):
    request: str = Field(min_length=3, max_length=4000)
    limits: AgentLimits | None = None


class ScopeApproval(BaseModel):
    scope: list[ScopeItem] | None = Field(default=None, description="Subset of the plan's scope; None approves all.")


def build_agent_router(runner: AgentRunner) -> APIRouter:
    router = APIRouter()

    def view(run, with_log: bool = False) -> dict:
        out = run.model_dump(mode="json")
        if with_log:
            out["log"] = runner.read_log(run)
        return out

    @router.get("/api/agent/tools")
    def tools():
        return {"tools": list(EDA_TOOLS), "defaults": AgentLimits().model_dump()}

    @router.get(BOARD + "/agent-runs")
    def list_runs(project_id: str, board_id: str):
        return {"runs": [view(r) for r in runner.list(project_id, board_id)]}

    @router.post(BOARD + "/agent-runs", status_code=202)
    def create(project_id: str, board_id: str, body: AgentRunCreate):
        run, job = runner.create(project_id, board_id, body.request, body.limits)
        return {"run": view(run), "job": job.model_dump()}

    @router.get(BOARD + "/agent-runs/{run_id}")
    def get(project_id: str, board_id: str, run_id: str):
        return view(runner.get(project_id, board_id, run_id), with_log=True)

    @router.post(BOARD + "/agent-runs/{run_id}/approve-scope", status_code=202)
    def approve_scope(project_id: str, board_id: str, run_id: str, body: ScopeApproval):
        run, job = runner.approve_scope(project_id, board_id, run_id, body.scope)
        return {"run": view(run), "job": job.model_dump()}

    @router.post(BOARD + "/agent-runs/{run_id}/cancel")
    def cancel(project_id: str, board_id: str, run_id: str):
        return view(runner.cancel(project_id, board_id, run_id))

    return router
