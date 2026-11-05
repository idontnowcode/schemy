"""Edits made in KiCad, brought back as proposals.

Step 1 (``layout/import-kicad``): placement only.

Someone opens the generated schematic in KiCad, moves parts, saves. Here the app reads the saved file, matches
symbols to IR components by their hidden ``AICS_ID`` field and proposes ``move`` operations, so the arrangement
becomes the IR's ``layout`` and survives the next generation. Like every change, it is a proposal the person
approves. Wires, labels and power symbols are drawn again; connectivity is never read from the file.

Step 2 (``kicad/import-edits``, ADR-012): connections, values, footprints, added and deleted parts, from the netlist
KiCad extracts from the edited file (``kicad-cli``), plus the parts moved away from where the generator drew them.
"""

from __future__ import annotations

import shutil
import uuid

from fastapi import APIRouter

from circuit_core.patch import Move, RemoveComponent
from kicad_adapter import SymbolLibrary, generate_schematic
from kicad_adapter.cli import KiCadCliError
from kicad_adapter.edit_import import edits_from_kicad
from kicad_adapter.layout_import import read_layout

from .errors import ApiError
from .exports import outputs_dir, read_manifest
from .proposals import proposal_dict
from .store import ProjectStore

BOARD = "/api/projects/{project_id}/boards/{board_id}"


def build_layout_router(store: ProjectStore, proposal_service, settings=None) -> APIRouter:
    router = APIRouter()

    def circuit_of(project_id: str, board_id: str):
        circuit, revision = store.get_circuit(project_id, board_id)
        if circuit is None:
            raise ApiError(409, "NO_CIRCUIT", "This board has no circuit yet.")
        return circuit, revision

    def current_outputs(project_id: str, board_id: str, revision: int | None):
        outputs = outputs_dir(store, project_id, board_id)
        manifest = read_manifest(outputs)
        if manifest is None:
            raise ApiError(409, "NO_OUTPUTS", "There is no generated schematic to read.",
                           recovery_hint="Generate the schematic, edit it in KiCad, save, then import.")
        if manifest.source_revision != revision:
            raise ApiError(409, "OUTPUTS_STALE", f"The schematic was generated from revision {manifest.source_revision}, "
                           f"not the current revision {revision}.",
                           recovery_hint="Edits can only be taken from a schematic of the current revision.")
        pages = [a.path for a in manifest.artifacts if a.artifact_type == "schematic" and (outputs / a.path).is_file()]
        return outputs, manifest, pages

    @router.post(BOARD + "/layout/import-kicad", status_code=201)
    def import_kicad(project_id: str, board_id: str):
        circuit, revision = circuit_of(project_id, board_id)
        outputs, _, pages = current_outputs(project_id, board_id, revision)
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

    @router.post(BOARD + "/kicad/import-edits", status_code=201)
    def import_edits(project_id: str, board_id: str):
        circuit, revision = circuit_of(project_id, board_id)
        outputs, manifest, pages = current_outputs(project_id, board_id, revision)
        cli = settings.kicad_factory() if settings is not None else None
        if cli is None:
            raise ApiError(409, "KICAD_UNAVAILABLE", "kicad-cli is needed to read the connections of the edited file.",
                           recovery_hint="Install KiCad 10 (or set AICS_KICAD_CLI). Positions alone can be imported "
                           "with the layout import.")
        root = outputs / f"{manifest.kicad_project}.kicad_sch"
        if not root.is_file():
            raise ApiError(409, "NO_OUTPUTS", f"{root.name} is missing.")
        work = store.board_dir(project_id, board_id) / f".kicad-import-{uuid.uuid4().hex}"
        work.mkdir()
        try:
            netlist = cli.export_netlist(root, work / "edited.net")
        except KiCadCliError as exc:
            raise ApiError(502, "KICAD_FAILED", f"kicad-cli could not read the edited schematic: {exc}") from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)
        library = SymbolLibrary(settings.symbol_dirs) if settings is not None and settings.symbol_dirs else None
        edits = edits_from_kicad(circuit, netlist, library)
        # Parts moved in KiCad: away from where the generator drew them for this revision.
        removed = {op.component_id for op in edits.ops if isinstance(op, RemoveComponent)}
        moves = []
        if library is not None:
            try:
                drawn = read_layout([p.text for p in generate_schematic(
                    circuit, library, project_name=manifest.kicad_project).pages]).placements
            except Exception:  # noqa: BLE001 - without a reference drawing, positions are simply not compared
                drawn = {}
            saved = read_layout([(outputs / name).read_text(encoding="utf-8") for name in pages]).placements
            moves = [Move(component_id=cid, placement=pl) for cid, pl in saved.items()
                     if cid not in removed and drawn.get(cid) is not None and drawn[cid] != pl]
        ops = edits.ops + moves
        if not ops:
            raise ApiError(409, "NO_KICAD_CHANGES", "The KiCad file matches the circuit.",
                           details={"skipped": edits.skipped})
        refs = {c.id: c.reference for c in circuit.components}
        notes = ["KiCad에서 고친 회로도를 kicad-cli로 읽어 회로와 비교했습니다.", *edits.notes]
        if moves:
            notes.append("KiCad에서 옮긴 부품(다음 생성부터 그 위치 유지): " + ", ".join(refs[m.component_id] for m in moves) + ".")
        if edits.skipped:
            notes.append("가져오지 않은 것: " + " ".join(edits.skipped))
        p, job = proposal_service.create_and_check(project_id, board_id, title="KiCad에서 고친 회로 가져오기", ops=ops,
                                                   source="kicad_edit", rationale=" ".join(notes))
        return {"proposal": proposal_dict(p), "job": job.model_dump(), "skipped": edits.skipped,
                "counts": {**edits.counts, "moved": len(moves)}}

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
