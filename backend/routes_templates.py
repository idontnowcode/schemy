"""Reviewed circuit templates: list them and place one on a board as a proposal (plan Phase 3, ADR-007)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from circuit_core.patch import AddComponent, Connect, PinRef, ReplaceCircuit
from circuit_core.templates import TEMPLATES, TemplateError, as_circuit, instantiate

from .errors import ApiError
from .proposals import proposal_dict
from .store import ProjectStore

BOARD = "/api/projects/{project_id}/boards/{board_id}"


class TemplatePlacement(BaseModel):
    params: dict[str, str] = Field(default_factory=dict)


def template_ops(store: ProjectStore, project_id: str, board_id: str, template_id: str, params: dict[str, str],
                 pin_lookup) -> tuple[list, dict]:
    """Patch ops that place the template on the board, and the template record kept on the proposal."""
    t = TEMPLATES.get(template_id)
    if t is None:
        raise ApiError(404, "NOT_FOUND", f"Template '{template_id}' not found.")
    current, _ = store.get_circuit(project_id, board_id)
    try:
        inst = instantiate(t, params, current, pin_lookup())
    except TemplateError as exc:
        raise ApiError(422, "TEMPLATE_INVALID", str(exc)) from exc
    if current is None:
        board = store.get_board(project_id, board_id)
        ops: list = [ReplaceCircuit(circuit=as_circuit(inst, project_id, board.name))]
    else:
        ops = [AddComponent(component=c) for c in inst.components]
        ops += [Connect(net=name, nodes=[PinRef(component_id=c, pin=p) for c, p in nodes])
                for name, nodes in inst.nets.items()]
    record = {**t.to_dict(), "params": inst.params,
              "components": {c.id: c.reference for c in inst.components}}
    return ops, record


def build_template_router(store: ProjectStore, proposal_service, pin_lookup) -> APIRouter:
    router = APIRouter()

    @router.get("/api/templates")
    def list_templates():
        return {"templates": [t.to_dict() for t in TEMPLATES.values()]}

    @router.get("/api/templates/{template_id}")
    def get_template(template_id: str):
        t = TEMPLATES.get(template_id)
        if t is None:
            raise ApiError(404, "NOT_FOUND", f"Template '{template_id}' not found.")
        return t.to_dict()

    @router.post(BOARD + "/templates/{template_id}", status_code=201)
    def place(project_id: str, board_id: str, template_id: str, body: TemplatePlacement):
        ops, record = template_ops(store, project_id, board_id, template_id, body.params, pin_lookup)
        refs = ", ".join(record["components"].values())
        p, job = proposal_service.create_and_check(
            project_id, board_id, title=f"템플릿: {record['title']}", ops=ops, source="template",
            rationale=f"{record['summary']} 부품 {refs}. 체크리스트 {len(record['checklist'])}개를 엔지니어 검토에서 확인하세요.",
            template=record)
        return {"proposal": proposal_dict(p), "job": job.model_dump()}

    return router
