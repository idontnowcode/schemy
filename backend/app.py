"""FastAPI application: /api routes, bearer-token check, and the built desktop UI at /.

Resources: projects -> boards (A, B, ...) -> one Circuit IR per board, whose pages
live in the IR. Requirements belong to the project; everything about a circuit
(revisions, outputs, documents, inventory match, imports) belongs to a board.
"""

from __future__ import annotations

import hmac
import logging
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from circuit_core import CircuitIR, Severity, validate_circuit
from circuit_core.export import BOM_COLUMNS, bom_rows, circuit_markdown
from kicad_adapter import SymbolLibrary, SymbolLibraryError, check_against_library

from .agent.routes import build_agent_router
from .agent.runner import AgentRunner
from .ai_access import AiAccess
from .config import APP_VERSION, Settings
from .datasheets import DatasheetLibrary
from .design_resolver import symbol_index
from .errors import ApiError, issue_dict, not_found
from .exports import external_edit_error, output_path, outputs_status, run_export
from .inventory_store import InventoryStore
from .jobs import JobRunner
from .proposals import ProposalStore
from .report import rationale_report, technical_review
from .reviews import ReviewStore
from .routes_ai import build_ai_router
from .routes_datasheets import build_datasheet_router
from .routes_design import build_design_router
from .routes_inventory import build_inventory_router
from .routes_layout import build_layout_router
from .routes_proposals import ProposalService, build_proposal_router
from .routes_reviews import build_review_router
from .routes_templates import build_template_router
from .schemas import (
    AppSettings,
    BoardCreate,
    BoardUpdate,
    ExportRequest,
    ProjectCreate,
    ProjectUpdate,
    Requirements,
)
from .store import ProjectStore, now_iso

IrBody = Annotated[Any, Body(description="Circuit IR JSON (validated by the handler)")]
log = logging.getLogger(__name__)
BOARD = "/api/projects/{project_id}/boards/{board_id}"


def _schema_errors(exc: ValidationError) -> list[dict]:
    return [{"loc": [str(p) for p in e["loc"]], "message": e["msg"], "type": e["type"]} for e in exc.errors()]


class CircuitChecker:
    """Schema (L0 types), L0/L1 rules, then library pins when a library is configured."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def library(self) -> SymbolLibrary | None:
        return SymbolLibrary(self.settings.symbol_dirs) if self.settings.symbol_dirs else None

    def check(self, data: Any) -> tuple[CircuitIR | None, dict]:
        try:
            circuit = CircuitIR.model_validate(data)
        except ValidationError as exc:
            return None, {"valid": False, "schema_errors": _schema_errors(exc), "issues": [],
                          "library_checked": False}
        issues = [{**issue_dict(i), "level": "ir"} for i in validate_circuit(circuit).issues]
        lib = self.library()
        if lib is not None:
            _, lib_issues = check_against_library(circuit, lib)
            issues += [{**issue_dict(i), "level": "library"} for i in lib_issues]
        return circuit, {
            "valid": not any(i["severity"] == Severity.ERROR.value for i in issues),
            "schema_errors": [],
            "issues": issues,
            "library_checked": lib is not None,
        }


def create_app(settings: Settings) -> FastAPI:
    store = ProjectStore(settings.workspace)
    jobs = JobRunner(store)
    inventory = InventoryStore(settings.workspace)
    datasheets = DatasheetLibrary(settings.workspace)
    proposals = ProposalStore(store)
    checker = CircuitChecker(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        jobs.shutdown()

    app = FastAPI(title="AI Circuit Studio API", version=APP_VERSION, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.jobs = jobs
    app.state.settings = settings
    app.state.inventory = inventory
    app.state.datasheets = datasheets
    app.state.proposals = proposals
    app.state.checker = checker

    @lru_cache(maxsize=1)
    def kicad_info() -> dict:
        cli = settings.kicad_factory()
        if cli is None:
            return {"available": False, "version": None, "runner": None}
        try:
            return {"available": True, "version": cli.version(), "runner": cli.describe()}
        except Exception as exc:  # noqa: BLE001 - report, don't crash the status endpoint
            return {"available": False, "version": None, "runner": cli.describe(), "error": str(exc)}

    # Detecting KiCad can take seconds (Docker start-up); warm the cache off the request path.
    threading.Thread(target=kicad_info, name="kicad-detect", daemon=True).start()

    # The KiCad symbol index for AI design takes ~10 s to build the first time; build it in the background.
    def warm_symbol_index():
        try:
            symbol_index(settings.symbol_dirs, settings.workspace / "cache" / "symbol_index.json")
        except Exception:  # noqa: BLE001 - the design job rebuilds it and reports errors
            log.exception("symbol index warm-up failed")

    if settings.symbol_dirs:
        threading.Thread(target=warm_symbol_index, name="symbol-index", daemon=True).start()

    # --- plumbing ---------------------------------------------------------

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if settings.token and request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied.encode(), f"Bearer {settings.token}".encode()):
                return JSONResponse(status_code=401, content=ApiError(
                    401, "UNAUTHORIZED", "Missing or invalid API token.").to_dict())
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        return JSONResponse(status_code=exc.status_code, content=ApiError(
            exc.status_code, code, str(exc.detail)).to_dict())

    @app.exception_handler(RequestValidationError)
    async def request_error(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=ApiError(
            422, "INVALID_REQUEST", "The request body is not valid.",
            details=[{"loc": [str(p) for p in e["loc"]], "message": e["msg"]} for e in exc.errors()],
        ).to_dict())

    api = APIRouter()

    # --- system & settings --------------------------------------------------

    @api.get("/api/system")
    def system():
        return {
            "app_version": APP_VERSION,
            "workspace": str(settings.workspace),
            "symbol_dirs": [str(d) for d in settings.symbol_dirs],
            "kicad": kicad_info(),
        }

    @api.get("/api/examples")
    def examples():
        out = []
        root = settings.examples_dir
        if root and root.is_dir():
            for f in sorted(root.glob("*/circuit.json")):
                try:
                    circuit = CircuitIR.model_validate_json(f.read_text(encoding="utf-8"))
                except (ValidationError, ValueError):
                    continue
                out.append({"id": f.parent.name, "title": circuit.title or f.parent.name,
                            "circuit": circuit.model_dump(mode="json")})
        return out

    @api.get("/api/settings")
    def get_settings():
        return store.get_settings()

    @api.put("/api/settings")
    def put_settings(body: AppSettings):
        return store.put_settings(body)

    # --- projects -------------------------------------------------------------

    @api.get("/api/projects")
    def list_projects():
        return store.list_projects()

    @api.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate):
        return store.create_project(body.name, body.description)

    @api.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return store.get_project(project_id)

    @api.patch("/api/projects/{project_id}")
    def update_project(project_id: str, body: ProjectUpdate):
        return store.update_project(project_id, name=body.name, description=body.description)

    @api.delete("/api/projects/{project_id}")
    def delete_project(project_id: str):
        return {"moved_to": str(store.delete_project(project_id))}

    @api.post("/api/projects/{project_id}/open")
    def open_project(project_id: str):
        project = store.mark_opened(project_id)
        current = store.get_settings()
        board = current.last_board_id if current.last_project_id == project_id else None
        if board not in project.boards:
            board = project.boards[0] if project.boards else None
        store.put_settings(current.model_copy(update={"last_project_id": project_id, "last_board_id": board}))
        return project

    @api.get("/api/projects/{project_id}/requirements")
    def get_requirements(project_id: str):
        return {"text": store.get_requirements(project_id)}

    @api.put("/api/projects/{project_id}/requirements")
    def put_requirements(project_id: str, body: Requirements):
        store.put_requirements(project_id, body.text)
        return {"text": body.text}

    # --- boards ---------------------------------------------------------------

    @api.get("/api/projects/{project_id}/boards")
    def list_boards(project_id: str):
        return store.list_boards(project_id)

    @api.post("/api/projects/{project_id}/boards", status_code=201)
    def create_board(project_id: str, body: BoardCreate):
        return store.create_board(project_id, body.name, body.description)

    @api.get(BOARD)
    def get_board(project_id: str, board_id: str):
        return store.get_board(project_id, board_id)

    @api.patch(BOARD)
    def update_board(project_id: str, board_id: str, body: BoardUpdate):
        return store.update_board(project_id, board_id, name=body.name, description=body.description)

    @api.delete(BOARD)
    def delete_board(project_id: str, board_id: str):
        return {"moved_to": str(store.delete_board(project_id, board_id))}

    # --- circuit & revisions (per board) -----------------------------------------

    @api.get(BOARD + "/circuit")
    def get_circuit(project_id: str, board_id: str):
        circuit, revision = store.get_circuit(project_id, board_id)
        return {"circuit": circuit.model_dump(mode="json") if circuit else None, "revision": revision}

    @api.post(BOARD + "/circuit/validate")
    def validate(project_id: str, board_id: str, data: IrBody):
        store.get_board(project_id, board_id)
        _, report = checker.check(data)
        return report

    @api.put(BOARD + "/circuit")
    def put_circuit(project_id: str, board_id: str, data: IrBody, source: str = "user_edit"):
        store.get_board(project_id, board_id)
        if source not in ("user_edit", "import"):
            raise ApiError(422, "INVALID_SOURCE", "source must be 'user_edit' or 'import'.")
        circuit, report = checker.check(data)
        if circuit is None:
            raise ApiError(422, "INVALID_CIRCUIT", "The Circuit IR does not match the schema.",
                           recovery_hint="Fix the fields listed in details.", details=report)
        # IR rule errors block saving. Library mismatches only block generation, because the
        # library on this machine may differ from the one the design targets.
        blocking = [i for i in report["issues"] if i["severity"] == "error" and i["level"] == "ir"]
        if blocking:
            raise ApiError(422, "INVALID_CIRCUIT", "The Circuit IR has rule errors (L0/L1).",
                           affected_component_ids=sorted({c for i in blocking for c in i["affected_component_ids"]}),
                           recovery_hint="Fix the listed issues and save again.", details=report)
        warnings = []
        if circuit.project_id != project_id:
            warnings.append(f"project_id '{circuit.project_id}' was changed to '{project_id}' to match this project.")
        info, created = store.save_circuit(project_id, board_id, circuit, source=source)  # type: ignore[arg-type]
        return {"revision": info, "created": created, "warnings": warnings, "validation": report}

    @api.get(BOARD + "/revisions")
    def list_revisions(project_id: str, board_id: str):
        return store.list_revisions(project_id, board_id)

    @api.get(BOARD + "/revisions/{revision}")
    def get_revision(project_id: str, board_id: str, revision: int):
        return {"info": store.get_revision_info(project_id, board_id, revision),
                "circuit": store.get_revision(project_id, board_id, revision).model_dump(mode="json")}

    @api.post(BOARD + "/revisions/{revision}/restore")
    def restore_revision(project_id: str, board_id: str, revision: int):
        info, created = store.restore_revision(project_id, board_id, revision)
        return {"revision": info, "created": created}

    # --- documents ------------------------------------------------------------------

    def current_circuit(project_id: str, board_id: str) -> tuple[CircuitIR, int]:
        circuit, revision = store.get_circuit(project_id, board_id)
        if circuit is None or revision is None:
            raise ApiError(409, "NO_CIRCUIT", "This board has no saved Circuit IR yet.",
                           recovery_hint="Save a Circuit IR first.")
        return circuit, revision

    @api.get(BOARD + "/documents/markdown")
    def markdown(project_id: str, board_id: str):
        circuit, revision = current_circuit(project_id, board_id)
        return {"revision": revision, "markdown": circuit_markdown(circuit, revision=revision)}

    @api.get(BOARD + "/documents/rationale")
    def rationale(project_id: str, board_id: str):
        status = outputs_status(store, project_id, board_id)
        manifest = status["manifest"]
        check = manifest["automated_check"] if manifest and not status["stale"] else None
        return rationale_report(store, project_id, board_id, automated_check=check, generated_at=now_iso())

    @api.get(BOARD + "/technical-review")
    def technical(project_id: str, board_id: str):
        return technical_review(store, project_id, board_id)

    @api.get(BOARD + "/documents/bom")
    def bom(project_id: str, board_id: str):
        circuit, revision = current_circuit(project_id, board_id)
        return {"revision": revision, "columns": list(BOM_COLUMNS), "rows": bom_rows(circuit)}

    # --- exports, outputs, jobs ---------------------------------------------------------

    @api.post(BOARD + "/exports", status_code=202)
    def export(project_id: str, board_id: str, body: ExportRequest | None = None):
        body = body or ExportRequest()
        current_circuit(project_id, board_id)
        status = outputs_status(store, project_id, board_id)
        if status["externally_modified"] and not body.force:
            # Refuse up front so the UI can ask before queueing anything.
            raise external_edit_error(status["externally_modified"])
        return jobs.submit(project_id, "export", lambda job_id: run_export(
            store, project_id, board_id, symbol_dirs=settings.symbol_dirs, kicad_factory=settings.kicad_factory,
            force=body.force, run_id=job_id), board_id=board_id)

    @api.get(BOARD + "/outputs")
    def outputs(project_id: str, board_id: str):
        return outputs_status(store, project_id, board_id)

    @api.get(BOARD + "/outputs/files/{name}")
    def output_file(project_id: str, board_id: str, name: str):
        store.get_board(project_id, board_id)
        path = output_path(store, project_id, board_id, name)
        if path is None:
            raise not_found("output", name)
        media = {".svg": "image/svg+xml", ".json": "application/json", ".md": "text/markdown; charset=utf-8",
                 ".csv": "text/csv; charset=utf-8"}.get(path.suffix, "text/plain; charset=utf-8")
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})

    @api.get(BOARD + "/jobs")
    def board_jobs(project_id: str, board_id: str):
        store.get_board(project_id, board_id)
        return store.list_jobs(project_id, board_id)

    @api.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return store.get_job(job_id)

    def pin_lookup():
        lib = checker.library()

        def lookup(symbol_id: str):
            if lib is None:
                return None
            try:
                return [(p.number, p.name) for p in lib.get(symbol_id).pins]
            except SymbolLibraryError:
                return None
        return lookup

    app.include_router(api)
    app.include_router(build_inventory_router(store, inventory))
    proposal_service = ProposalService(store, jobs, proposals, datasheets, settings, checker)
    app.state.proposal_service = proposal_service
    app.include_router(build_ai_router(store, jobs, inventory, settings, pin_lookup, datasheets, proposal_service))
    app.include_router(build_template_router(store, proposal_service, pin_lookup))
    app.include_router(build_layout_router(store, proposal_service, settings))
    reviews = ReviewStore(store)
    app.state.reviews = reviews
    app.include_router(build_review_router(store, reviews))

    access = AiAccess(store, settings)

    def agent_provider():
        cfg = access.settings()
        return access.provider_for(cfg), cfg

    def search_symbols(query: str) -> list[dict]:
        if not settings.symbol_dirs:
            return []
        index = symbol_index(settings.symbol_dirs, settings.workspace / "cache" / "symbol_index.json")
        return [{"symbol_id": s.lib_id, "description": s.description, "pins": s.pins, "units": s.units}
                for _, s in index.search([query], limit=10)]

    agent_runner = AgentRunner(store, jobs, proposals, proposal_service, provider_for=agent_provider,
                               library=checker.library, kicad=settings.kicad_factory, search_symbols=search_symbols)
    app.state.agent_runner = agent_runner
    app.include_router(build_agent_router(agent_runner))
    app.include_router(build_datasheet_router(store, jobs, datasheets, settings, checker.library))
    app.include_router(build_proposal_router(proposal_service))
    app.include_router(build_design_router(store, jobs, proposal_service, inventory, datasheets, settings))

    # --- desktop UI ---------------------------------------------------------------------

    if settings.ui_dir and Path(settings.ui_dir, "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    else:
        @app.get("/")
        def root():
            return JSONResponse({"app": "AI Circuit Studio API", "docs": "/api/docs",
                                 "ui": "not built (run npm run build in desktop/)"})

    return app
