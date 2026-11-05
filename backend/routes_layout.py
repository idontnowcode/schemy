"""Keeping a layout arranged in KiCad (external edits, step 1: placement only).

Someone opens the generated schematic in KiCad, moves parts, saves. Here the app reads the saved file, matches
symbols to IR components by their hidden ``AICS_ID`` field and proposes ``move`` operations, so the arrangement
becomes the IR's ``layout`` and survives the next generation. Like every change, it is a proposal the person
approves. Wires, labels and power symbols are drawn again; connectivity is never read from the file.
"""

from __future__ import annotations

from fastapi import APIRouter

from circuit_core.patch import Move
from kicad_adapter.layout_import import read_layout

from .errors import ApiError
from .exports import outputs_dir, read_manifest
from .proposals import proposal_dict
from .store import ProjectStore

BOARD = "/api/projects/{project_id}/boards/{board_id}"


def build_layout_router(store: ProjectStore, proposal_service) -> APIRouter:
    router = APIRouter()

    def circuit_of(project_id: str, board_id: str):
        circuit, revision = store.get_circuit(project_id, board_id)
        if circuit is None:
            raise ApiError(409, "NO_CIRCUIT", "This board has no circuit yet.")
        return circuit, revision

    @router.post(BOARD + "/layout/import-kicad", status_code=201)
    def import_kicad(project_id: str, board_id: str):
        circuit, revision = circuit_of(project_id, board_id)
        outputs = outputs_dir(store, project_id, board_id)
        manifest = read_manifest(outputs)
        if manifest is None:
            raise ApiError(409, "NO_OUTPUTS", "There is no generated schematic to read.",
                           recovery_hint="Generate the schematic, arrange it in KiCad, save, then import.")
        if manifest.source_revision != revision:
            raise ApiError(409, "OUTPUTS_STALE", f"The schematic was generated from revision {manifest.source_revision}, "
                           f"not the current revision {revision}.",
                           recovery_hint="Positions can only be taken from a schematic of the current revision.")
        pages = [a.path for a in manifest.artifacts if a.artifact_type == "schematic" and (outputs / a.path).is_file()]
        layout = read_layout([(outputs / name).read_text(encoding="utf-8") for name in pages])
        refs = {c.id: c.reference for c in circuit.components}
        ops = [Move(component_id=cid, placement=pl) for cid, pl in layout.placements.items()
               if cid in refs and circuit.layout.get(cid) != pl]
        if not ops:
            raise ApiError(409, "NO_LAYOUT_CHANGES", "The positions in the KiCad file are already the circuit's layout.")
        flipped = [refs[op.component_id] for op in ops if op.placement and op.placement.mirror]
        notes = [f"KiCad 파일 {', '.join(pages)}에서 부품 {len(ops)}개의 위치·회전{'·반전' if flipped else ''}을 가져옵니다. "
                 "승인하면 다음 생성부터 이 배치를 유지합니다. 배선·라벨·전원 심볼은 다시 그립니다."]
        if layout.unknown:
            notes.append(f"KiCad에서 추가한 부품({', '.join(layout.unknown)})은 가져오지 않습니다. 회로에 넣으려면 제안으로 추가하세요.")
        if layout.extra_units:
            notes.append(f"다중 유닛 부품의 추가 유닛({', '.join(layout.extra_units)})은 자동 배치로 둡니다.")
        p, job = proposal_service.create_and_check(project_id, board_id, title="KiCad에서 옮긴 배치 가져오기", ops=ops,
                                                   source="kicad_layout", rationale=" ".join(notes))
        return {"proposal": proposal_dict(p), "job": job.model_dump(), "unknown": layout.unknown,
                "extra_units": layout.extra_units}

    @router.post(BOARD + "/layout/reset", status_code=201)
    def reset(project_id: str, board_id: str):
        circuit, _ = circuit_of(project_id, board_id)
        if not circuit.layout:
            raise ApiError(409, "NO_LAYOUT", "Every part is already placed automatically.")
        ops = [Move(component_id=cid, placement=None) for cid in circuit.layout]
        p, job = proposal_service.create_and_check(
            project_id, board_id, title="자동 배치로 되돌리기", ops=ops, source="kicad_layout",
            rationale=f"고정 배치 {len(ops)}개를 지우고 자동 배치로 그립니다.")
        return {"proposal": proposal_dict(p), "job": job.model_dump()}

    return router
