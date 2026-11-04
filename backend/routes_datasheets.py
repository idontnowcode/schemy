"""/api/datasheets (workspace library) and the per-board datasheet report and download job.

Nothing here changes a circuit. The report compares each component with its datasheet summary;
downloads only use links found in the board's own KiCad symbols, after the user confirmed them.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ai_engine.tasks import datasheet_prompt, pdf_prompt
from circuit_core.datasheet_checks import check_component
from circuit_core.datasheet_md import import_datasheet_markdown
from circuit_core.datasheets import identity
from circuit_core.models import Component
from circuit_core.parts import CATEGORY_LABELS, category_of
from kicad_adapter import SymbolLibrary, SymbolLibraryError

from .config import Settings
from .datasheets import MAX_DATASHEET_BYTES, DatasheetEntry, DatasheetError, DatasheetLibrary
from .errors import ApiError
from .jobs import JobRunner
from .schemas import (
    DatasheetFetchRequest,
    DatasheetLink,
    DatasheetReviewed,
    DatasheetUpload,
    MarkdownImportRequest,
)
from .store import ProjectStore
from .uploads import decode_upload

BOARD = "/api/projects/{project_id}/boards/{board_id}"
RELEVANT_CATEGORIES = {None, "ic", "transistor", "crystal"}


def api_error(exc: DatasheetError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message, recovery_hint=exc.hint)


def entry_dict(library: DatasheetLibrary, entry: DatasheetEntry) -> dict:
    return {**entry.model_dump(mode="json"), "path": str(library.pdf_path(entry))}


def _url_filename(url: str) -> str:
    name = PurePosixPath(unquote(urlparse(url).path)).name
    return name if name.lower().endswith(".pdf") else (name or "datasheet") + ".pdf"


class SymbolInfo:
    """Cached lookups in the KiCad library for one request."""

    def __init__(self, lib: SymbolLibrary | None):
        self.lib = lib
        self._pins: dict[str, list[tuple[str, str]] | None] = {}

    def url(self, symbol_id: str) -> str | None:
        return self.lib.datasheet_url(symbol_id) if self.lib else None

    def description(self, symbol_id: str) -> str | None:
        if not self.lib:
            return None
        try:
            return self.lib.properties(symbol_id).get("Description") or None
        except SymbolLibraryError:
            return None

    def pins(self, symbol_id: str) -> list[tuple[str, str]] | None:
        if symbol_id not in self._pins:
            try:
                self._pins[symbol_id] = [(p.number, p.name) for p in self.lib.get(symbol_id).pins] if self.lib else None
            except SymbolLibraryError:
                self._pins[symbol_id] = None
        return self._pins[symbol_id]


def build_datasheet_router(store: ProjectStore, jobs: JobRunner, library: DatasheetLibrary, settings: Settings,
                           symbol_library) -> APIRouter:
    router = APIRouter()

    def guarded(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DatasheetError as exc:
            raise api_error(exc) from exc

    # --- prompts for Copilot ---------------------------------------------------------------------

    prompts = {"datasheet-summary": datasheet_prompt, "pdf-schematic": pdf_prompt}

    @router.get("/api/prompts/{name}")
    def prompt(name: str):
        if name not in prompts:
            raise ApiError(404, "NOT_FOUND", f"프롬프트 '{name}'가 없습니다.")
        return {"name": name, "text": prompts[name]()}

    # --- library -------------------------------------------------------------------------------

    @router.get("/api/datasheets")
    def list_datasheets():
        return {"entries": [entry_dict(library, e) for e in library.entries()], "dir": str(library.dir)}

    @router.post("/api/datasheets/upload")
    def upload(body: DatasheetUpload):
        data = decode_upload(body.content_base64, max_bytes=MAX_DATASHEET_BYTES, what="PDF 파일")
        entry, created = guarded(library.add, data, original_name=body.filename, source="upload",
                                 manufacturer=body.manufacturer, part_numbers=body.part_numbers, symbols=body.symbols)
        return {"entry": entry_dict(library, entry), "created": created}

    @router.post("/api/datasheets/{entry_id}/link")
    def link(entry_id: str, body: DatasheetLink):
        entry = guarded(library.link, entry_id, part_numbers=body.part_numbers, symbols=body.symbols,
                        manufacturer=body.manufacturer)
        return entry_dict(library, entry)

    @router.delete("/api/datasheets/{entry_id}", status_code=204)
    def delete(entry_id: str):
        guarded(library.delete, entry_id)

    @router.get("/api/datasheets/{entry_id}/file")
    def pdf_file(entry_id: str):
        entry = guarded(library.get, entry_id)
        return FileResponse(library.pdf_path(entry), media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="{entry.id}.pdf"'})

    @router.get("/api/datasheets/{entry_id}/summary")
    def get_summary(entry_id: str):
        entry = guarded(library.get, entry_id)
        facts = library.facts(entry)
        return {"entry": entry_dict(library, entry), "markdown": library.summary_markdown(entry),
                "facts": facts.model_dump(mode="json") if facts else None}

    @router.post("/api/datasheets/{entry_id}/summary")
    def import_summary(entry_id: str, body: MarkdownImportRequest):
        guarded(library.get, entry_id)
        result = import_datasheet_markdown(body.text, source_name=body.filename)
        entry = None
        if result.ok:
            entry = guarded(library.save_summary, entry_id, markdown=body.text, facts=result.facts, origin="markdown",
                            source_name=body.filename, warnings=result.stats.get("warnings", 0))
        return {**result.to_dict(), "entry": entry_dict(library, entry) if entry else None}

    @router.put("/api/datasheets/{entry_id}/reviewed")
    def reviewed(entry_id: str, body: DatasheetReviewed):
        return entry_dict(library, guarded(library.set_reviewed, entry_id, body.reviewed))

    # --- per board -----------------------------------------------------------------------------

    def board_report(project_id: str, board_id: str) -> dict:
        store.get_board(project_id, board_id)
        circuit, revision = store.get_circuit(project_id, board_id)
        info = SymbolInfo(symbol_library())
        if circuit is None:
            return {"revision": None, "components": [], "fetchable": [], "library_available": info.lib is not None}
        rows = []
        fetch: dict[str, list[Component]] = defaultdict(list)
        for comp in circuit.components:
            url = info.url(comp.symbol_id)
            ident = identity(comp, url)
            entry = library.find(comp, url)
            facts = library.facts(entry) if entry else None
            category = category_of(comp.symbol_id, comp.reference)
            checks = None
            if facts is not None:
                checks = check_component(circuit, comp, facts, info.pins(comp.symbol_id),
                                         [info.description(comp.symbol_id)])
            can_fetch = ident != "generic" and url is not None and entry is None
            if can_fetch:
                fetch[url].append(comp)
            rows.append({
                "id": comp.id, "reference": comp.reference, "value": comp.value, "symbol_id": comp.symbol_id,
                "footprint_id": comp.footprint_id, "manufacturer": comp.manufacturer,
                "manufacturer_part_number": comp.manufacturer_part_number,
                "category": category, "category_label": CATEGORY_LABELS.get(category or "", None),
                "identity": ident, "relevant": ident != "generic" or entry is not None or category in RELEVANT_CATEGORIES,
                "symbol_datasheet_url": url, "can_fetch": can_fetch,
                "datasheet": entry_dict(library, entry) if entry else None, "checks": checks,
                "notes": [n.model_dump() for n in facts.notes] if facts else [],
                "uncertain": facts.uncertain if facts else [],
            })
        fetchable = [{"url": url, "references": [c.reference for c in comps],
                      "symbol_ids": sorted({c.symbol_id for c in comps})} for url, comps in fetch.items()]
        return {"revision": revision, "components": rows, "fetchable": fetchable,
                "library_available": info.lib is not None}

    @router.get(BOARD + "/datasheets")
    def board_datasheets(project_id: str, board_id: str):
        return board_report(project_id, board_id)

    @router.post(BOARD + "/datasheets/fetch", status_code=202)
    def fetch(project_id: str, board_id: str, body: DatasheetFetchRequest):
        report = board_report(project_id, board_id)
        allowed = {f["url"]: f for f in report["fetchable"]}
        unknown = [u for u in body.urls if u not in allowed]
        if unknown:
            raise ApiError(422, "URL_NOT_FROM_LIBRARY", "이 보드의 KiCad 심볼에 없는 링크는 받지 않습니다: "
                           + ", ".join(u[:80] for u in unknown[:3]),
                           recovery_hint="데이터시트 탭을 새로 고친 뒤 다시 시도하세요. 다른 파일은 'PDF 올리기'로 넣으세요.")
        circuit, _ = store.get_circuit(project_id, board_id)
        by_ref = {c.reference: c for c in circuit.components}
        targets = [(u, [by_ref[r] for r in allowed[u]["references"] if r in by_ref]) for u in dict.fromkeys(body.urls)]

        def run(job_id: str) -> dict:
            results = []
            for url, comps in targets:
                refs = [c.reference for c in comps]
                try:
                    data, final_url = settings.datasheet_fetcher(url)
                    entry, created = library.add(
                        data, original_name=_url_filename(final_url or url), source="kicad_symbol", url=url,
                        final_url=final_url,
                        manufacturer=next((c.manufacturer for c in comps if c.manufacturer), None),
                        part_numbers=[c.manufacturer_part_number for c in comps if c.manufacturer_part_number],
                        symbols=sorted({c.symbol_id for c in comps}))
                    results.append({"url": url, "references": refs, "ok": True, "entry_id": entry.id,
                                    "created": created, "identity": entry.identity.get("status")})
                except DatasheetError as exc:
                    results.append({"url": url, "references": refs, "ok": False, "error_code": exc.code,
                                    "message": exc.message, "hint": exc.hint})
            return {"results": results, "downloaded": sum(r["ok"] for r in results),
                    "failed": sum(not r["ok"] for r in results)}

        return jobs.submit(project_id, "datasheet_fetch", run, board_id=board_id)

    return router
