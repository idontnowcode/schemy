"""/api/datasheets (workspace library) and the per-board datasheet report and download job.

Nothing here changes a circuit. The report compares each component with its datasheet summary — for
the saved circuit or for a proposal before it is approved. Downloads only use links the app found
for the board's parts (KiCad symbol link, the maker's address pattern, a distributor search answer),
after the user confirmed them.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ai_engine import KeyStoreError
from ai_engine.keystore import ENV_VARS
from ai_engine.tasks import datasheet_prompt, pdf_prompt
from circuit_core.datasheet_checks import check_component
from circuit_core.datasheet_md import import_datasheet_markdown, summary_template
from circuit_core.datasheet_sources import (
    DatasheetCandidate,
    maker_candidate,
    part_key,
    search_term,
    symbol_candidate,
    url_part_name,
)
from circuit_core.datasheets import identity
from circuit_core.export import bom_rows, natural_key
from circuit_core.models import Component
from circuit_core.parts import CATEGORY_LABELS, category_of
from kicad_adapter import SymbolLibrary, SymbolLibraryError

from .config import Settings
from .datasheet_search import KEY_NAMES, key_status
from .datasheets import MAX_DATASHEET_BYTES, DatasheetEntry, DatasheetError, DatasheetLibrary, PartSearch
from .errors import ApiError
from .jobs import JobRunner
from .proposals import ProposalStore
from .schemas import (
    DatasheetFetchRequest,
    DatasheetLink,
    DatasheetReviewed,
    DatasheetSearchRequest,
    DatasheetUpload,
    DistributorKeyBody,
    MarkdownImportRequest,
)
from .store import ProjectStore, atomic_write, now_iso
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
                           symbol_library, proposals: ProposalStore | None = None) -> APIRouter:
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

    def board_circuit(project_id: str, board_id: str, proposal_id: str | None):
        """The saved circuit, or a proposal's candidate circuit (checked before it is approved)."""
        store.get_board(project_id, board_id)
        if proposal_id:
            if proposals is None:
                raise ApiError(404, "NOT_FOUND", "제안을 찾을 수 없습니다.")
            p = proposals.get(project_id, board_id, proposal_id)
            return proposals.candidate(p), p.base_revision
        return store.get_circuit(project_id, board_id)

    def candidates_for(comp: Component, url: str | None, searches: dict) -> list[DatasheetCandidate]:
        """Distributor answers (exact part number) first, then the KiCad link, then the maker's pattern."""
        out: list[DatasheetCandidate] = []
        key = part_key(comp)
        found = searches.get(key) if key else None
        if found:
            out += sorted(found.candidates, key=lambda c: c.source != "digikey")
        for extra in (symbol_candidate(comp, url), maker_candidate(comp, url)):
            if extra and all(extra.urls[0] not in c.urls for c in out):
                out.append(extra)
        return out

    def board_report(project_id: str, board_id: str, proposal_id: str | None = None) -> dict:
        circuit, revision = board_circuit(project_id, board_id, proposal_id)
        info = SymbolInfo(symbol_library())
        keys = key_status(settings.keystore)
        base = {"revision": revision, "proposal_id": proposal_id, "library_available": info.lib is not None,
                "search": {"mouser": keys["mouser"]["configured"], "digikey": keys["digikey"]["configured"]}}
        if circuit is None:
            return {**base, "components": [], "fetchable": []}
        searches = library.searches()
        rows = []
        fetch: dict[str, dict] = {}
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
            cands = candidates_for(comp, url, searches) if ident != "generic" else []
            can_fetch = bool(cands) and entry is None
            if can_fetch:
                for c in cands:
                    item = fetch.setdefault(c.id, {**c.model_dump(mode="json"), "url": c.urls[0], "references": [],
                                                   "symbol_ids": []})
                    item["references"].append(comp.reference)
                    if comp.symbol_id not in item["symbol_ids"]:
                        item["symbol_ids"].append(comp.symbol_id)
            key = part_key(comp)
            searched = searches.get(key) if key and ident != "generic" else None
            rows.append({
                "id": comp.id, "reference": comp.reference, "value": comp.value, "symbol_id": comp.symbol_id,
                "footprint_id": comp.footprint_id, "manufacturer": comp.manufacturer,
                "manufacturer_part_number": comp.manufacturer_part_number,
                "category": category, "category_label": CATEGORY_LABELS.get(category or "", None),
                "identity": ident, "relevant": ident != "generic" or entry is not None or category in RELEVANT_CATEGORIES,
                "symbol_datasheet_url": url, "can_fetch": can_fetch,
                "candidates": [c.id for c in cands] if entry is None else [],
                "searched": {"at": searched.searched_at, "term": searched.term, "errors": searched.errors,
                             "found": len(searched.candidates)} if searched else None,
                "datasheet": entry_dict(library, entry) if entry else None, "checks": checks,
                "notes": [n.model_dump() for n in facts.notes] if facts else [],
                "uncertain": facts.uncertain if facts else [],
            })
        return {**base, "components": rows, "fetchable": list(fetch.values())}

    @router.get(BOARD + "/datasheets")
    def board_datasheets(project_id: str, board_id: str, proposal_id: str | None = None):
        return board_report(project_id, board_id, proposal_id)

    @router.get(BOARD + "/datasheets/template/{component_id}")
    def summary_skeleton(project_id: str, board_id: str, component_id: str, proposal_id: str | None = None):
        """An empty summary MD for one part (maker, part number and pin numbers filled in) to complete from its
        datasheet by hand or with Copilot, then bring back with 「요약 MD 가져오기」."""
        circuit, _ = board_circuit(project_id, board_id, proposal_id)
        comp = next((c for c in (circuit.components if circuit else []) if c.id == component_id), None)
        if comp is None:
            raise ApiError(404, "COMPONENT_NOT_FOUND", f"부품 '{component_id}'이(가) 회로에 없습니다.")
        package = comp.footprint_id.split(":", 1)[-1] if comp.footprint_id else None
        numbers = sorted({p.number for p in comp.pins}, key=natural_key)
        text = summary_template(manufacturer=comp.manufacturer, part_number=comp.manufacturer_part_number,
                                value=comp.value, pins=numbers, package=package)
        stem = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in (comp.manufacturer_part_number or comp.value))
        return {"filename": f"{stem or comp.reference}-datasheet-summary.md", "text": text}

    @router.post(BOARD + "/datasheets/search", status_code=202)
    def search(project_id: str, board_id: str, body: DatasheetSearchRequest):
        """Ask the configured distributors for the parts that still have no datasheet. Only part numbers are sent."""
        clients = settings.datasheet_searchers(settings.keystore)
        if not clients:
            raise ApiError(409, "NO_SEARCH_KEYS", "Mouser나 DigiKey 키가 없어 유통사 검색을 할 수 없습니다.",
                           recovery_hint="설정 › 데이터시트 검색에서 Mouser API 키나 DigiKey Client ID·Secret을 저장하세요. "
                                         "키 없이도 KiCad 심볼 링크와 제조사 주소 규칙으로 받을 수 있습니다.")
        circuit, _ = board_circuit(project_id, board_id, body.proposal_id)
        if circuit is None:
            raise ApiError(409, "NO_CIRCUIT", "저장된 회로가 없습니다.")
        info = SymbolInfo(symbol_library())
        wanted = set(body.references or [])
        terms: dict[str, tuple[str, list[str]]] = {}
        for comp in circuit.components:
            url = info.url(comp.symbol_id)
            if identity(comp, url) == "generic" or (wanted and comp.reference not in wanted):
                continue
            if not wanted and library.find(comp, url) is not None:
                continue
            key, term = part_key(comp), search_term(comp)
            if key and term:
                terms.setdefault(key, (term, []))[1].append(comp.reference)
        if not terms:
            raise ApiError(409, "NOTHING_TO_SEARCH", "찾을 부품이 없습니다. 데이터시트가 없는 IC·제조사 품번이 있는 부품만 찾습니다.")

        def run(job_id: str) -> dict:
            results = []
            for key, (term, refs) in terms.items():
                found, errors = [], []
                for client in clients:
                    try:
                        found += client.search(term)
                    except DatasheetError as exc:
                        errors.append({"source": client.name, "error_code": exc.code, "message": exc.message,
                                       "hint": exc.hint})
                library.save_search(key, PartSearch(term=term, searched_at=now_iso(), candidates=found, errors=errors))
                results.append({"term": term, "references": refs, "found": len(found), "errors": errors})
            return {"results": results, "searched": len(results), "sources": [c.name for c in clients]}

        return jobs.submit(project_id, "datasheet_search", run, board_id=board_id)

    @router.post(BOARD + "/datasheets/fetch", status_code=202)
    def fetch(project_id: str, board_id: str, body: DatasheetFetchRequest):
        """Download the candidates the user confirmed. A candidate tries its URLs in order; with ``fallback`` a part
        whose chosen candidate failed tries its other candidates. Only links this app found for the board are used."""
        report = board_report(project_id, board_id, body.proposal_id)
        allowed = {f["id"]: f for f in report["fetchable"]}
        by_url = {f["url"]: f for f in report["fetchable"]}
        chosen: list[dict] = []
        unknown = []
        for cid in body.candidates:
            (chosen.append(allowed[cid]) if cid in allowed else unknown.append(cid))
        for url in body.urls:
            (chosen.append(by_url[url]) if url in by_url else unknown.append(url))
        if unknown:
            raise ApiError(422, "URL_NOT_FROM_LIBRARY", "이 보드에서 찾은 링크가 아니어서 받지 않습니다: "
                           + ", ".join(u[:80] for u in unknown[:3]),
                           recovery_hint="데이터시트 탭을 새로 고친 뒤 다시 시도하세요. 다른 파일은 'PDF 올리기'로 넣으세요.")
        if not chosen:
            raise ApiError(422, "NOTHING_CHOSEN", "받을 링크를 고르세요.")
        circuit, _ = board_circuit(project_id, board_id, body.proposal_id)
        by_ref = {c.reference: c for c in circuit.components}
        rows = {r["reference"]: r for r in report["components"]}
        chosen = list({c["id"]: c for c in chosen}.values())

        def attempt(cand: dict, comps: list[Component]) -> tuple[dict | None, list[dict]]:
            tried = []
            for url in cand["urls"]:
                try:
                    data, final_url = settings.datasheet_fetcher(url)
                    entry, created = library.add(
                        data, original_name=_url_filename(final_url or url), source=cand["source"], url=url,
                        final_url=final_url,
                        manufacturer=next((c.manufacturer for c in comps if c.manufacturer), None)
                        or cand.get("manufacturer"),
                        part_numbers=[c.manufacturer_part_number for c in comps if c.manufacturer_part_number],
                        symbols=sorted({c.symbol_id for c in comps}),
                        expect=[url_part_name(url)] if cand["source"] == "maker_site" else ())
                    return {"entry_id": entry.id, "created": created, "identity": entry.identity.get("status"),
                            "url": url}, tried
                except DatasheetError as exc:
                    tried.append({"url": url, "error_code": exc.code, "message": exc.message, "hint": exc.hint})
            return None, tried

        def run(job_id: str) -> dict:
            results, done_refs = [], set()
            for cand in chosen:
                refs = [r for r in cand["references"] if r not in done_refs]
                if not refs:
                    continue
                comps = [by_ref[r] for r in refs if r in by_ref]
                order = [cand]
                if body.fallback:
                    others = [allowed[i] for r in refs for i in rows[r]["candidates"] if i in allowed]
                    order += [o for o in dict((o["id"], o) for o in others).values() if o["id"] != cand["id"]]
                tried_all: list[dict] = []
                got = None
                for c in order:
                    got, tried = attempt(c, comps)
                    tried_all += [{**t, "source": c["source"], "label": c["label"]} for t in tried]
                    if got:
                        got.update(source=c["source"], label=c["label"])
                        break
                last = tried_all[-1] if tried_all else {}
                results.append({"url": (got or {}).get("url") or cand["url"], "references": refs, "ok": got is not None,
                                **(got or {"error_code": last.get("error_code"), "message": last.get("message"),
                                           "hint": last.get("hint"), "product_url": cand.get("product_url")}),
                                "tried": tried_all})
                if got:
                    done_refs.update(refs)
            return {"results": results, "downloaded": sum(r["ok"] for r in results),
                    "failed": sum(not r["ok"] for r in results)}

        return jobs.submit(project_id, "datasheet_fetch", run, board_id=board_id)

    # --- BOM stock / price / lifecycle --------------------------------------------------------------

    def availability_path(project_id: str, board_id: str):
        return store.board_dir(project_id, board_id) / "bom_availability.json"

    @router.get(BOARD + "/documents/bom/availability")
    def bom_availability(project_id: str, board_id: str):
        store.get_board(project_id, board_id)
        path = availability_path(project_id, board_id)
        last = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        keys = key_status(settings.keystore)
        return {"result": last, "search": {"mouser": keys["mouser"]["configured"], "digikey": keys["digikey"]["configured"]}}

    @router.post(BOARD + "/documents/bom/availability", status_code=202)
    def lookup_availability(project_id: str, board_id: str):
        """Stock, unit price and lifecycle of the BOM lines that have a maker part number (only the part numbers
        are sent). The result is kept with the board so the BOM tab can show it later."""
        clients = settings.datasheet_searchers(settings.keystore)
        if not clients:
            raise ApiError(409, "NO_SEARCH_KEYS", "Mouser나 DigiKey 키가 없어 재고·가격을 조회할 수 없습니다.",
                           recovery_hint="설정 › 데이터시트 검색에서 Mouser API 키나 DigiKey Client ID·Secret을 저장하세요.")
        circuit, revision = store.get_circuit(project_id, board_id)
        if circuit is None:
            raise ApiError(409, "NO_CIRCUIT", "저장된 회로가 없습니다.")
        lines = [r for r in bom_rows(circuit) if r["MPN"]]
        if not lines:
            raise ApiError(409, "NO_MPN", "제조사 품번(MPN)이 있는 BOM 줄이 없습니다.",
                           recovery_hint="회로 (IR)에서 manufacturer_part_number를 넣거나 「보유 자재 적용」으로 채우세요.")

        def run(job_id: str) -> dict:
            out = []
            for line in lines:
                offers, errors = [], []
                for client in clients:
                    try:
                        offers += client.offers(line["MPN"])
                    except DatasheetError as exc:
                        errors.append({"source": client.name, "error_code": exc.code, "message": exc.message})
                out.append({"references": line["References"], "quantity": int(line["Quantity"]), "mpn": line["MPN"],
                            "manufacturer": line["Manufacturer"], "offers": offers, "errors": errors})
            result = {"revision": revision, "checked_at": now_iso(), "lines": out,
                      "sources": [c.name for c in clients]}
            atomic_write(availability_path(project_id, board_id), json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            return result

        return jobs.submit(project_id, "bom_availability", run, board_id=board_id)

    # --- distributor keys ----------------------------------------------------------------------

    @router.get("/api/datasheet-search/keys")
    def search_keys():
        return key_status(settings.keystore)

    @router.put("/api/datasheet-search/keys/{name}")
    def put_search_key(name: str, body: DistributorKeyBody):
        if name not in KEY_NAMES:
            raise ApiError(404, "NOT_FOUND", f"'{name}'은(는) 지원하지 않는 검색입니다. mouser 또는 digikey입니다.")
        values = [body.api_key] if name == "mouser" else [body.client_id, body.client_secret]
        if not all(v and v.strip() for v in values):
            raise ApiError(422, "KEY_REQUIRED", "Mouser는 API 키, DigiKey는 Client ID와 Client Secret이 모두 필요합니다.")
        try:
            for key_name, value in zip(KEY_NAMES[name], values, strict=True):
                settings.keystore.set(key_name, value.strip())
        except KeyStoreError as exc:
            env = " / ".join(v for k in KEY_NAMES[name] for v in ENV_VARS[k])
            raise ApiError(409, "KEYRING_UNAVAILABLE", str(exc),
                           recovery_hint=f"환경 변수 {env}로 지정하고 앱을 다시 시작하세요.") from exc
        return key_status(settings.keystore)

    @router.delete("/api/datasheet-search/keys/{name}")
    def delete_search_key(name: str):
        if name not in KEY_NAMES:
            raise ApiError(404, "NOT_FOUND", f"'{name}'은(는) 지원하지 않는 검색입니다.")
        for key_name in KEY_NAMES[name]:
            settings.keystore.delete(key_name)
        return key_status(settings.keystore)

    return router
