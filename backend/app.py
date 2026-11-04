"""FastAPI application: /api routes, bearer-token check, and the built desktop UI at /."""

from __future__ import annotations

import hmac
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from circuit_core import CircuitIR, Severity, validate_circuit
from circuit_core.export import BOM_COLUMNS, bom_rows, circuit_markdown
from kicad_adapter import SymbolLibrary, check_against_library

from .config import APP_VERSION, Settings
from .errors import ApiError, issue_dict, not_found
from .exports import ARTIFACT_TYPES, outputs_status, run_export
from .jobs import JobRunner
from .schemas import AppSettings, ExportRequest, ProjectCreate, ProjectUpdate, Requirements
from .store import ProjectStore

IrBody = Annotated[Any, Body(description="Circuit IR JSON (validated by the handler)")]


def _schema_errors(exc: ValidationError) -> list[dict]:
    return [{"loc": [str(p) for p in e["loc"]], "message": e["msg"], "type": e["type"]} for e in exc.errors()]


def create_app(settings: Settings) -> FastAPI:
    store = ProjectStore(settings.workspace)
    jobs = JobRunner(store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        jobs.shutdown()

    app = FastAPI(title="AI Circuit Studio API", version=APP_VERSION, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.jobs = jobs
    app.state.settings = settings

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

    def library() -> SymbolLibrary | None:
        return SymbolLibrary(settings.symbol_dirs) if settings.symbol_dirs else None

    def check_ir(data: Any) -> tuple[CircuitIR | None, dict]:
        """Schema (L0 types), L0/L1 rules, then library pins when a library is configured."""
        try:
            circuit = CircuitIR.model_validate(data)
        except ValidationError as exc:
            return None, {"valid": False, "schema_errors": _schema_errors(exc), "issues": [],
                          "library_checked": False}
        issues = [{**issue_dict(i), "level": "ir"} for i in validate_circuit(circuit).issues]
        lib = library()
        if lib is not None:
            _, lib_issues = check_against_library(circuit, lib)
            issues += [{**issue_dict(i), "level": "library"} for i in lib_issues]
        return circuit, {
            "valid": not any(i["severity"] == Severity.ERROR.value for i in issues),
            "schema_errors": [],
            "issues": issues,
            "library_checked": lib is not None,
        }

    # --- system & settings --------------------------------------------------

    @app.get("/api/system")
    def system():
        return {
            "app_version": APP_VERSION,
            "workspace": str(settings.workspace),
            "symbol_dirs": [str(d) for d in settings.symbol_dirs],
            "kicad": kicad_info(),
        }

    @app.get("/api/examples")
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

    @app.get("/api/settings")
    def get_settings():
        return store.get_settings()

    @app.put("/api/settings")
    def put_settings(body: AppSettings):
        return store.put_settings(body)

    # --- projects -------------------------------------------------------------

    @app.get("/api/projects")
    def list_projects():
        return store.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate):
        return store.create_project(body.name, body.description)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return store.get_project(project_id)

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: str, body: ProjectUpdate):
        return store.update_project(project_id, name=body.name, description=body.description)

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str):
        target = store.delete_project(project_id)
        return {"moved_to": str(target)}

    @app.post("/api/projects/{project_id}/open")
    def open_project(project_id: str):
        project = store.mark_opened(project_id)
        store.put_settings(store.get_settings().model_copy(update={"last_project_id": project_id}))
        return project

    # --- requirements -------------------------------------------------------------

    @app.get("/api/projects/{project_id}/requirements")
    def get_requirements(project_id: str):
        return {"text": store.get_requirements(project_id)}

    @app.put("/api/projects/{project_id}/requirements")
    def put_requirements(project_id: str, body: Requirements):
        store.put_requirements(project_id, body.text)
        return {"text": body.text}

    # --- circuit & revisions ---------------------------------------------------------

    @app.get("/api/projects/{project_id}/circuit")
    def get_circuit(project_id: str):
        circuit, revision = store.get_circuit(project_id)
        return {"circuit": circuit.model_dump(mode="json") if circuit else None, "revision": revision}

    @app.post("/api/projects/{project_id}/circuit/validate")
    def validate(project_id: str, data: IrBody):
        store.get_project(project_id)
        _, report = check_ir(data)
        return report

    @app.put("/api/projects/{project_id}/circuit")
    def put_circuit(project_id: str, data: IrBody, source: str = "user_edit"):
        store.get_project(project_id)
        if source not in ("user_edit", "import"):
            raise ApiError(422, "INVALID_SOURCE", "source must be 'user_edit' or 'import'.")
        circuit, report = check_ir(data)
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
        info, created = store.save_circuit(project_id, circuit, source=source)  # type: ignore[arg-type]
        return {"revision": info, "created": created, "warnings": warnings, "validation": report}

    @app.get("/api/projects/{project_id}/revisions")
    def list_revisions(project_id: str):
        return store.list_revisions(project_id)

    @app.get("/api/projects/{project_id}/revisions/{revision}")
    def get_revision(project_id: str, revision: int):
        return {"info": store.get_revision_info(project_id, revision),
                "circuit": store.get_revision(project_id, revision).model_dump(mode="json")}

    @app.post("/api/projects/{project_id}/revisions/{revision}/restore")
    def restore_revision(project_id: str, revision: int):
        info, created = store.restore_revision(project_id, revision)
        return {"revision": info, "created": created}

    # --- documents ------------------------------------------------------------------

    def _current_circuit(project_id: str) -> tuple[CircuitIR, int]:
        circuit, revision = store.get_circuit(project_id)
        if circuit is None or revision is None:
            raise ApiError(409, "NO_CIRCUIT", "This project has no saved Circuit IR yet.",
                           recovery_hint="Save a Circuit IR first.")
        return circuit, revision

    @app.get("/api/projects/{project_id}/documents/markdown")
    def markdown(project_id: str):
        circuit, revision = _current_circuit(project_id)
        return {"revision": revision, "markdown": circuit_markdown(circuit, revision=revision)}

    @app.get("/api/projects/{project_id}/documents/bom")
    def bom(project_id: str):
        circuit, revision = _current_circuit(project_id)
        return {"revision": revision, "columns": list(BOM_COLUMNS), "rows": bom_rows(circuit)}

    # --- exports, outputs, jobs ---------------------------------------------------------

    @app.post("/api/projects/{project_id}/exports", status_code=202)
    def export(project_id: str, body: ExportRequest | None = None):
        body = body or ExportRequest()
        _current_circuit(project_id)
        status = outputs_status(store, project_id)
        if status["externally_modified"] and not body.force:
            # Refuse up front so the UI can ask before queueing anything.
            raise ApiError(409, "EXTERNAL_MODIFICATION",
                           "Generated files were changed outside the app: " + ", ".join(status["externally_modified"]),
                           recovery_hint="Copy your edits elsewhere if you need them, then regenerate with force.",
                           details={"paths": status["externally_modified"]})
        job = jobs.submit(project_id, "export", lambda job_id: run_export(
            store, project_id, symbol_dirs=settings.symbol_dirs, kicad_factory=settings.kicad_factory,
            force=body.force, run_id=job_id))
        return job

    @app.get("/api/projects/{project_id}/outputs")
    def outputs(project_id: str):
        return outputs_status(store, project_id)

    @app.get("/api/projects/{project_id}/outputs/files/{name}")
    def output_file(project_id: str, name: str):
        if name not in ARTIFACT_TYPES:
            raise not_found("output", name)
        path = store.project_dir(project_id) / "outputs" / name
        if not path.is_file():
            raise not_found("output", name)
        media = {".svg": "image/svg+xml", ".json": "application/json", ".md": "text/markdown; charset=utf-8",
                 ".csv": "text/csv; charset=utf-8"}.get(path.suffix, "text/plain; charset=utf-8")
        return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{project_id}/jobs")
    def project_jobs(project_id: str):
        store.get_project(project_id)
        return store.list_jobs(project_id)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return store.get_job(job_id)

    # --- desktop UI ---------------------------------------------------------------------

    if settings.ui_dir and Path(settings.ui_dir, "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    else:
        @app.get("/")
        def root():
            return JSONResponse({"app": "AI Circuit Studio API", "docs": "/api/docs",
                                 "ui": "not built (run npm run build in desktop/)"})

    return app
