"""Optional AI features: settings, API key or Claude login, models, PDF import, substitute suggestions.

AI is off until the user turns it on and stores a key. Before any call the UI shows
what will be sent; every call leaves an audit record in the board's ai_runs/ folder.
Results are proposals: imported circuits are loaded into the editor for review, and
suggestions are applied only through the normal inventory-apply endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter

from ai_engine import DEFAULT_MODELS, AiError, KeyStoreError
from ai_engine.tasks import suggest_substitutes, suggestion_request, summarize_datasheet, transcribe_pdf
from circuit_core.datasheet_md import import_datasheet_markdown
from circuit_core.inventory import match_inventory
from circuit_core.patch import ReplaceCircuit
from circuit_core.schematic_md import import_schematic_markdown

from .ai_access import AiAccess, api_error, run_record
from .claude_login import ClaudeLogin
from .config import Settings
from .datasheets import DatasheetError, DatasheetLibrary
from .errors import ApiError
from .inventory_store import InventoryStore
from .jobs import JobRunner
from .schemas import AiSettingsUpdate, ApiKeyBody, FileUpload, MarkdownImportRequest
from .store import ProjectStore, sha256_bytes
from .uploads import MB, decode_upload

BOARD = "/api/projects/{project_id}/boards/{board_id}"
PROVIDERS = ["gemini", "claude", "chatgpt"]
MAX_PDF_BYTES = 50 * MB


def build_ai_router(store: ProjectStore, jobs: JobRunner, inventory: InventoryStore, settings: Settings,
                    pin_lookup, datasheets: DatasheetLibrary, proposal_service=None,
                    claude_login: ClaudeLogin | None = None) -> APIRouter:
    router = APIRouter()
    claude_login = claude_login or ClaudeLogin()

    access = AiAccess(store, settings)
    ai_settings = access.settings
    key_status = access.key_status
    provider_for = access.provider_for

    # --- settings ---------------------------------------------------------------

    @router.get("/api/ai/settings")
    def get_ai_settings():
        cfg = ai_settings()
        return {"settings": cfg, "key": key_status(cfg.provider), "providers": PROVIDERS}

    @router.put("/api/ai/settings")
    def put_ai_settings(body: AiSettingsUpdate):
        current = ai_settings()
        update = body.model_dump(exclude_none=True)
        if body.provider and body.provider != current.provider and body.model is None:
            update["model"] = DEFAULT_MODELS[body.provider]
        cfg = current.model_copy(update=update)
        store.put_setting_json("ai", cfg.model_dump())
        return {"settings": cfg, "key": key_status(cfg.provider), "providers": PROVIDERS}

    @router.put("/api/ai/key")
    def put_key(body: ApiKeyBody):
        cfg = ai_settings()
        if cfg.provider == "chatgpt":
            raise ApiError(409, "LOGIN_ONLY", "ChatGPT는 API 키 대신 내 ChatGPT 계정으로 로그인해 씁니다.",
                           recovery_hint="설정에서 'ChatGPT로 계속하기'를 누르세요.")
        try:
            settings.keystore.set(cfg.provider, body.api_key.strip())
        except KeyStoreError as exc:
            env = "ANTHROPIC_API_KEY" if cfg.provider == "claude" else "GEMINI_API_KEY"
            raise ApiError(409, "KEYRING_UNAVAILABLE", str(exc), recovery_hint=f"{env} 환경 변수로 키를 지정하세요.") from exc
        return {"key": key_status(cfg.provider)}

    # --- Claude login through Anthropic's CLI --------------------------------------------------

    @router.get("/api/ai/claude/login")
    def claude_login_state():
        return claude_login.status()

    @router.post("/api/ai/claude/login")
    def claude_login_start():
        state = claude_login.start()
        if not state["cli_found"]:
            raise ApiError(409, "ANT_CLI_NOT_FOUND", "Anthropic CLI(`ant`)를 찾지 못했습니다.",
                           recovery_hint=state["install_hint"])
        return state

    @router.post("/api/ai/claude/logout")
    def claude_logout():
        return claude_login.logout()

    # --- ChatGPT with the user's account (Sign in with ChatGPT) ----------------------------------

    @router.get("/api/ai/chatgpt/login")
    def chatgpt_login_state():
        return settings.chatgpt_login.status()

    @router.post("/api/ai/chatgpt/login")
    def chatgpt_login_start():
        try:
            return settings.chatgpt_login.start()
        except AiError as exc:
            raise _api_error(exc) from exc

    @router.post("/api/ai/chatgpt/logout")
    def chatgpt_logout(forget: bool = False):
        return settings.chatgpt_login.logout(forget=forget)

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
            proposal_id = None
            if result.ok and proposal_service is not None:
                # AI output reaches the board only through the proposal approval path.
                p = proposal_service.proposals.create(
                    project_id, board_id, title=f"PDF 가져오기: {body.filename}"[:150],
                    ops=[ReplaceCircuit(circuit=result.circuit)], source="ai_pdf_import",
                    rationale="AI가 PDF 회로도를 '회로도 MD'로 옮겨 적고, 앱이 KiCad 라이브러리 핀과 대조해 만든 회로입니다.",
                    ai={"model": transcription.result.model, "summary": "", "questions": [
                        f"불확실 항목: {u.get('내용')}" for u in result.uncertain][:20], "review": None})
                proposal_id = proposal_service.check_now(p).id
                record.setdefault("output", {})["proposal"] = proposal_id
            store.save_ai_run(project_id, board_id, record)
            return {"markdown": transcription.markdown, "import": result.to_dict(), "warnings": transcription.warnings,
                    "model": transcription.result.model, "usage": transcription.result.usage,
                    "saved_copy": md_copy.name, "proposal_id": proposal_id}

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

    # --- datasheet summary (AI) ---------------------------------------------------------

    @router.post(BOARD + "/datasheets/{entry_id}/summarize", status_code=202)
    def summarize(project_id: str, board_id: str, entry_id: str):
        store.get_board(project_id, board_id)
        cfg = ai_settings()
        provider = provider_for(cfg)
        try:
            entry = datasheets.get(entry_id)
        except DatasheetError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
        pdf = datasheets.pdf_path(entry).read_bytes()

        def run(job_id: str) -> dict:
            record = _run_record(job_id, "datasheet_summary", cfg, {
                "datasheet": entry.id, "file": entry.original_name, "bytes": len(pdf), "sha256": entry.sha256,
                "pages": entry.page_count})
            try:
                summary = summarize_datasheet(provider, pdf, entry.original_name, max_output_tokens=cfg.max_output_tokens)
            except AiError as exc:
                record.update(status="failed", error={"code": exc.code, "message": exc.message})
                datasheets.save_ai_run(record)
                raise _api_error(exc) from exc
            result = import_datasheet_markdown(summary.markdown, source_name=f"{entry.id}.ai.md")
            saved = None
            if result.ok:
                saved = datasheets.save_summary(entry.id, markdown=summary.markdown, facts=result.facts, origin="ai",
                                                source_name=f"{entry.id}.ai.md",
                                                warnings=result.stats.get("warnings", 0), model=summary.result.model)
            record.update(status="succeeded", usage=summary.result.usage, finish_reason=summary.result.finish_reason,
                          warnings=summary.warnings, output={
                              "markdown_sha256": sha256_bytes(summary.markdown.encode("utf-8")),
                              "import_ok": result.ok, "issues": len(result.issues)})
            datasheets.save_ai_run(record)
            return {"markdown": summary.markdown, "import": {k: v for k, v in result.to_dict().items() if k != "facts"},
                    "warnings": summary.warnings, "model": summary.result.model, "usage": summary.result.usage,
                    "saved": saved is not None}

        return jobs.submit(project_id, "datasheet_summary", run, board_id=board_id)

    return router


_run_record = run_record
_api_error = api_error
