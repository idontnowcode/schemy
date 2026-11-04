"""Optional AI features: settings, API key, models, PDF import, substitute suggestions.

AI is off until the user turns it on and stores a key. Before any call the UI shows
what will be sent; every call leaves an audit record in the board's ai_runs/ folder.
Results are proposals: imported circuits are loaded into the editor for review, and
suggestions are applied only through the normal inventory-apply endpoint.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from ai_engine import AiError, KeyStoreError, LlmProvider
from ai_engine.tasks import suggest_substitutes, suggestion_request, transcribe_pdf
from circuit_core.inventory import match_inventory
from circuit_core.schematic_md import import_schematic_markdown

from .config import Settings
from .errors import ApiError
from .inventory_store import InventoryStore
from .jobs import JobRunner
from .schemas import AiSettings, AiSettingsUpdate, ApiKeyBody, FileUpload, MarkdownImportRequest
from .store import ProjectStore, now_iso, sha256_bytes
from .uploads import MB, decode_upload

BOARD = "/api/projects/{project_id}/boards/{board_id}"
MAX_PDF_BYTES = 50 * MB


def build_ai_router(store: ProjectStore, jobs: JobRunner, inventory: InventoryStore, settings: Settings,
                    pin_lookup) -> APIRouter:
    router = APIRouter()

    def ai_settings() -> AiSettings:
        raw = store.get_setting_json("ai")
        try:
            return AiSettings.model_validate(raw or {})
        except ValueError:
            return AiSettings()

    def key_status(provider: str) -> dict:
        key, source = settings.keystore.get(provider)
        return {"configured": key is not None, "source": source, "keyring_available": settings.keystore.available}

    def provider_for(cfg: AiSettings, *, require_enabled: bool = True) -> LlmProvider:
        if require_enabled and not cfg.enabled:
            raise ApiError(409, "AI_DISABLED", "AI 기능이 꺼져 있습니다.", recovery_hint="설정 화면에서 AI 기능을 켜세요.")
        key, _ = settings.keystore.get(cfg.provider)
        if not key:
            raise ApiError(409, "AI_NO_KEY", "Gemini API 키가 없습니다.",
                           recovery_hint="설정 화면에서 API 키를 저장하거나 GEMINI_API_KEY 환경 변수를 설정하세요.")
        try:
            return settings.ai_provider_factory(cfg.provider, key, cfg.model, cfg.timeout_s)
        except AiError as exc:
            raise _api_error(exc) from exc

    # --- settings ---------------------------------------------------------------

    @router.get("/api/ai/settings")
    def get_ai_settings():
        cfg = ai_settings()
        return {"settings": cfg, "key": key_status(cfg.provider), "providers": ["gemini"]}

    @router.put("/api/ai/settings")
    def put_ai_settings(body: AiSettingsUpdate):
        cfg = ai_settings().model_copy(update=body.model_dump(exclude_none=True))
        store.put_setting_json("ai", cfg.model_dump())
        return {"settings": cfg, "key": key_status(cfg.provider), "providers": ["gemini"]}

    @router.put("/api/ai/key")
    def put_key(body: ApiKeyBody):
        cfg = ai_settings()
        try:
            settings.keystore.set(cfg.provider, body.api_key.strip())
        except KeyStoreError as exc:
            raise ApiError(409, "KEYRING_UNAVAILABLE", str(exc),
                           recovery_hint="GEMINI_API_KEY 환경 변수로 키를 지정하세요.") from exc
        return {"key": key_status(cfg.provider)}

    @router.delete("/api/ai/key")
    def delete_key():
        cfg = ai_settings()
        settings.keystore.delete(cfg.provider)
        return {"key": key_status(cfg.provider)}

    @router.get("/api/ai/models")
    def list_models():
        cfg = ai_settings()
        provider = provider_for(cfg, require_enabled=False)
        try:
            return {"models": [m.__dict__ for m in provider.list_models()], "current": cfg.model}
        except AiError as exc:
            raise _api_error(exc) from exc

    # --- Markdown import (no AI) ------------------------------------------------------

    @router.post(BOARD + "/imports/markdown")
    def import_markdown(project_id: str, board_id: str, body: MarkdownImportRequest):
        store.get_board(project_id, board_id)
        copy = store.save_import(project_id, board_id, body.filename, body.text.encode("utf-8"))
        result = import_schematic_markdown(body.text, pin_lookup(), project_id=project_id, source_name=body.filename)
        return {**result.to_dict(), "saved_copy": copy.name}

    # --- PDF import (AI) ------------------------------------------------------------------

    @router.post(BOARD + "/imports/pdf", status_code=202)
    def import_pdf(project_id: str, board_id: str, body: FileUpload):
        store.get_board(project_id, board_id)
        cfg = ai_settings()
        provider = provider_for(cfg)
        pdf = decode_upload(body.content_base64, max_bytes=MAX_PDF_BYTES, what="PDF 파일")
        if not pdf.startswith(b"%PDF"):
            raise ApiError(422, "NOT_A_PDF", f"'{body.filename}'은(는) PDF 파일이 아닙니다.")
        pdf_copy = store.save_import(project_id, board_id, body.filename, pdf)

        def run(job_id: str) -> dict:
            record = _run_record(job_id, "pdf_import", cfg, {
                "file": body.filename, "bytes": len(pdf), "sha256": sha256_bytes(pdf), "stored_as": pdf_copy.name})
            try:
                transcription = transcribe_pdf(provider, pdf, body.filename, max_output_tokens=cfg.max_output_tokens)
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message})
                store.save_ai_run(project_id, board_id, record)
                raise _api_error(exc) from exc
            md_name = f"{body.filename.rsplit('.', 1)[0]}.ai.md"
            md_copy = store.save_import(project_id, board_id, md_name, transcription.markdown.encode("utf-8"))
            result = import_schematic_markdown(transcription.markdown, pin_lookup(), project_id=project_id,
                                               source_name=body.filename)
            record.update(status="succeeded", usage=transcription.result.usage,
                          finish_reason=transcription.result.finish_reason, warnings=transcription.warnings,
                          output={"markdown_sha256": sha256_bytes(transcription.markdown.encode("utf-8")),
                                  "stored_as": md_copy.name, "import_ok": result.ok})
            store.save_ai_run(project_id, board_id, record)
            return {"markdown": transcription.markdown, "import": result.to_dict(), "warnings": transcription.warnings,
                    "model": transcription.result.model, "usage": transcription.result.usage,
                    "saved_copy": md_copy.name}

        return jobs.submit(project_id, "pdf_import", run, board_id=board_id)

    # --- substitute suggestions (AI) ----------------------------------------------------

    def _lines(project_id: str, board_id: str):
        store.get_board(project_id, board_id)
        circuit, _ = store.get_circuit(project_id, board_id)
        if circuit is None:
            raise ApiError(409, "NO_CIRCUIT", "This board has no saved Circuit IR yet.")
        inv = inventory.get()
        if inv is None:
            raise ApiError(409, "NO_INVENTORY", "보유 자재 목록이 없습니다.")
        return match_inventory(circuit, inv), inv

    @router.get(BOARD + "/inventory-suggest/preview")
    def suggest_preview(project_id: str, board_id: str):
        """Exactly what would be sent to the AI provider, so the user can decide."""
        lines, inv = _lines(project_id, board_id)
        request = suggestion_request(lines, inv)
        return {"provider": ai_settings().provider, "model": ai_settings().model,
                "line_count": len(request["lines"]),
                "candidate_count": sum(len(i["candidates"]) for i in request["lines"]), "request": request}

    @router.post(BOARD + "/inventory-suggest", status_code=202)
    def suggest(project_id: str, board_id: str):
        cfg = ai_settings()
        provider = provider_for(cfg)
        lines, inv = _lines(project_id, board_id)

        def run(job_id: str) -> dict:
            record = _run_record(job_id, "inventory_suggest", cfg, {
                "lines": sum(1 for line in lines if line.status.value != "matched"), "inventory_parts": len(inv.parts)})
            try:
                suggestions, dropped, result = suggest_substitutes(provider, lines, inv)
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message})
                store.save_ai_run(project_id, board_id, record)
                raise _api_error(exc) from exc
            record.update(status="succeeded", usage=result.usage if result else {},
                          output={"suggestions": len(suggestions), "dropped": len(dropped)})
            store.save_ai_run(project_id, board_id, record)
            return {"suggestions": [s.to_dict() for s in suggestions], "dropped": dropped,
                    "model": result.model if result else None}

        return jobs.submit(project_id, "inventory_suggest", run, board_id=board_id)

    return router


def _run_record(job_id: str, kind: str, cfg: AiSettings, input_summary: dict) -> dict:
    return {"id": f"{now_iso()[:19].replace(':', '')}-{uuid.uuid4().hex[:6]}", "job_id": job_id, "kind": kind,
            "created_at": now_iso(), "provider": cfg.provider, "model": cfg.model, "input": input_summary,
            "status": "running"}


def _api_error(exc: AiError) -> ApiError:
    status = {"AI_AUTH_FAILED": 401, "AI_QUOTA": 429, "AI_MODEL_NOT_FOUND": 404, "NOT_A_PDF": 422,
              "AI_BAD_OUTPUT": 502}.get(exc.code, 502)
    return ApiError(status, exc.code, exc.message, recovery_hint=exc.hint)
