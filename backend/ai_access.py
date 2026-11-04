"""Shared gate for every AI call: AI must be switched on and credentials must be available
(a Gemini or Claude API key, or for Claude the login of Anthropic's ``ant`` CLI)."""

from __future__ import annotations

import uuid

from ai_engine import AiError, LlmProvider, claude_login_status

from .config import Settings
from .errors import ApiError
from .schemas import AiSettings
from .store import ProjectStore, now_iso


class AiAccess:
    def __init__(self, store: ProjectStore, settings: Settings):
        self.store = store
        self.app_settings = settings

    def settings(self) -> AiSettings:
        raw = self.store.get_setting_json("ai")
        try:
            return AiSettings.model_validate(raw or {})
        except ValueError:
            return AiSettings()

    def key_status(self, provider: str) -> dict:
        key, source = self.app_settings.keystore.get(provider)
        status = {"configured": key is not None, "source": source,
                  "keyring_available": self.app_settings.keystore.available}
        if provider == "claude":
            login = claude_login_status()
            status["login"] = login
            if key is None and login["logged_in"]:
                status.update(configured=True, source="login")
        return status

    def provider_for(self, cfg: AiSettings, *, require_enabled: bool = True) -> LlmProvider:
        if require_enabled and not cfg.enabled:
            raise ApiError(409, "AI_DISABLED", "AI 기능이 꺼져 있습니다.", recovery_hint="설정 화면에서 AI 기능을 켜세요.")
        key, _ = self.app_settings.keystore.get(cfg.provider)
        if not key:
            if cfg.provider == "claude" and claude_login_status()["logged_in"]:
                key = None  # the anthropic SDK reads the `ant auth login` profile itself
            elif cfg.provider == "claude":
                raise ApiError(409, "AI_NO_KEY", "Claude API 키나 로그인이 없습니다.",
                               recovery_hint="설정 화면에서 'Claude 로그인'을 하거나 API 키를 저장하세요 "
                                             "(ANTHROPIC_API_KEY 환경 변수도 됩니다).")
            else:
                raise ApiError(409, "AI_NO_KEY", "Gemini API 키가 없습니다.",
                               recovery_hint="설정 화면에서 API 키를 저장하거나 GEMINI_API_KEY 환경 변수를 설정하세요.")
        try:
            return self.app_settings.ai_provider_factory(cfg.provider, key, cfg.model, cfg.timeout_s)
        except AiError as exc:
            raise api_error(exc) from exc


def run_record(job_id: str, kind: str, cfg: AiSettings, input_summary: dict) -> dict:
    """Audit record of an AI run (plan v1.1 rule 11). Never contains the API key."""
    return {"id": f"{now_iso()[:19].replace(':', '')}-{uuid.uuid4().hex[:6]}", "job_id": job_id, "kind": kind,
            "created_at": now_iso(), "provider": cfg.provider, "model": cfg.model, "input": input_summary,
            "status": "running"}


def api_error(exc: AiError) -> ApiError:
    status = {"AI_AUTH_FAILED": 401, "AI_BILLING": 402, "AI_QUOTA": 429, "AI_MODEL_NOT_FOUND": 404, "NOT_A_PDF": 422,
              "AI_BAD_OUTPUT": 502, "AI_NO_KEY": 409, "AI_REFUSED": 422}.get(exc.code, 502)
    return ApiError(status, exc.code, exc.message, recovery_hint=exc.hint)
