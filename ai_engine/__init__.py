"""Optional AI features (off by default). Gemini is the default provider; Claude is the other option.

The AI only proposes: a PDF becomes Markdown that goes through the normal importer
and a human review before it is saved; substitute suggestions are applied only when
the user picks them.
"""

from .keystore import KeyStore, KeyStoreError, claude_login_status
from .providers import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MODELS,
    AiError,
    Attachment,
    LlmProvider,
    LlmResult,
    ModelInfo,
    make_provider,
)

__all__ = [
    "DEFAULT_CLAUDE_MODEL",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_MODELS",
    "AiError",
    "Attachment",
    "KeyStore",
    "KeyStoreError",
    "LlmProvider",
    "LlmResult",
    "ModelInfo",
    "claude_login_status",
    "make_provider",
]
