"""LLM providers behind one small interface (plan v1.2 section 4).

The app talks to ``LlmProvider`` only, so a provider can be swapped without touching
features. Gemini (Google AI, ``google-genai`` SDK) is the default and so far the only
real provider; tests use a fake.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
"""Alias used throughout the google-genai SDK documentation; it follows Google's current Flash model.
Users can pick another model from the list the API returns for their key."""

INLINE_LIMIT_BYTES = 18 * 1024 * 1024
"""Larger attachments go through the Files API (inline requests are limited to about 20 MB)."""


@dataclass(frozen=True)
class Attachment:
    name: str
    mime_type: str
    data: bytes


@dataclass
class LlmResult:
    text: str
    model: str
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None


@dataclass(frozen=True)
class ModelInfo:
    name: str
    display_name: str | None
    input_token_limit: int | None
    output_token_limit: int | None


class AiError(Exception):
    """A provider call failed; ``code`` is one of our error codes, ``hint`` says what to do."""

    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


class LlmProvider(Protocol):
    name: str
    model: str

    def generate(self, *, prompt: str, attachments: Iterable[Attachment] = (), json_schema: dict | None = None,
                 max_output_tokens: int = 8192, temperature: float = 0.0) -> LlmResult: ...

    def list_models(self) -> list[ModelInfo]: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL, timeout_s: float = 300):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self.model = model

    def _call(self, fn):
        from google.genai import errors

        try:
            return fn()
        except errors.APIError as exc:
            raise _map_error(exc.code, exc.status, exc.message) from exc
        except Exception as exc:  # network errors, timeouts
            raise AiError("AI_UNAVAILABLE", f"Gemini API에 연결하지 못했습니다: {exc}",
                          "네트워크 연결을 확인하고 다시 시도하세요.") from exc

    def generate(self, *, prompt: str, attachments: Iterable[Attachment] = (), json_schema: dict | None = None,
                 max_output_tokens: int = 8192, temperature: float = 0.0) -> LlmResult:
        types = self._types
        contents: list = []
        for a in attachments:
            if len(a.data) <= INLINE_LIMIT_BYTES:
                contents.append(types.Part.from_bytes(data=a.data, mime_type=a.mime_type))
            else:
                contents.append(self._call(lambda a=a: self._client.files.upload(
                    file=io.BytesIO(a.data), config={"mime_type": a.mime_type, "display_name": a.name})))
        contents.append(prompt)
        options: dict = {"temperature": temperature, "max_output_tokens": max_output_tokens}
        if json_schema is not None:
            options.update(response_mime_type="application/json", response_json_schema=json_schema)
        config = types.GenerateContentConfig(**options)
        response = self._call(lambda: self._client.models.generate_content(
            model=self.model, contents=contents, config=config))
        usage = {}
        if response.usage_metadata is not None:
            for key in ("prompt_token_count", "candidates_token_count", "total_token_count"):
                usage[key] = getattr(response.usage_metadata, key, None)
        finish = None
        if response.candidates:
            reason = response.candidates[0].finish_reason
            finish = getattr(reason, "value", None) or (str(reason) if reason is not None else None)
        return LlmResult(text=response.text or "", model=self.model, usage=usage, finish_reason=finish)

    def list_models(self) -> list[ModelInfo]:
        def collect():
            out = []
            for m in self._client.models.list():
                if "generateContent" not in (m.supported_actions or []):
                    continue
                out.append(ModelInfo(name=(m.name or "").removeprefix("models/"), display_name=m.display_name,
                                     input_token_limit=m.input_token_limit, output_token_limit=m.output_token_limit))
            return out

        return self._call(collect)


def _map_error(code: int | None, status: str | None, message: str | None) -> AiError:
    text = message or status or "unknown error"
    if code in (401, 403) or status in ("PERMISSION_DENIED", "UNAUTHENTICATED"):
        return AiError("AI_AUTH_FAILED", f"Gemini API 키가 거부되었습니다: {text}",
                       "설정에서 API 키를 확인하세요 (Google AI Studio에서 발급).")
    if code == 400 and "API key" in text:
        return AiError("AI_AUTH_FAILED", f"Gemini API 키가 올바르지 않습니다: {text}", "설정에서 API 키를 확인하세요.")
    if code == 404:
        return AiError("AI_MODEL_NOT_FOUND", f"모델을 찾지 못했습니다: {text}",
                       "설정에서 '모델 목록 불러오기'로 사용 가능한 모델을 고르세요.")
    if re.search(r"prepay|prepaid|credits? (are|is) (depleted|exhausted)|billing|insufficient (funds|balance)", text, re.I):
        return AiError("AI_BILLING", f"Gemini API 결제 크레딧이 없습니다: {text}",
                       "이 API 키의 Google 프로젝트는 선불 결제로 설정되어 있고 크레딧이 바닥났습니다. "
                       "Google AI Studio에서 크레딧을 충전하거나, 무료로 쓰려면 결제가 연결되지 않은 프로젝트에서 "
                       "새 API 키를 만들어 설정에 저장하세요. 모델(Flash/Pro)을 바꿔서 해결되는 문제가 아닙니다.")
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return AiError("AI_QUOTA", f"Gemini API 요청 한도를 넘었습니다: {text}",
                       "무료 등급은 분당·하루 요청 수가 제한됩니다. 잠시 후 다시 시도하세요. "
                       "AI 설계는 한 번에 3~5회 요청하므로, '설계 후 AI 검토'를 끄면 요청이 줄어듭니다.")
    if code and code >= 500:
        return AiError("AI_UNAVAILABLE", f"Gemini 서버 오류 ({code}): {text}", "잠시 후 다시 시도하세요.")
    return AiError("AI_REQUEST_FAILED", f"Gemini 요청 실패 ({code} {status}): {text}")


def make_provider(provider: str, api_key: str, model: str, timeout_s: float = 300) -> LlmProvider:
    if provider == "gemini":
        return GeminiProvider(api_key=api_key, model=model or DEFAULT_GEMINI_MODEL, timeout_s=timeout_s)
    raise AiError("AI_PROVIDER_UNKNOWN", f"지원하지 않는 AI 공급자입니다: {provider}")
