"""Optional AI features (off by default). Gemini is the default provider.

The AI only proposes: a PDF becomes Markdown that goes through the normal importer
and a human review before it is saved; substitute suggestions are applied only when
the user picks them.
"""

from .keystore import KeyStore, KeyStoreError
from .providers import DEFAULT_GEMINI_MODEL, AiError, Attachment, LlmProvider, LlmResult, ModelInfo, make_provider

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "AiError",
    "Attachment",
    "KeyStore",
    "KeyStoreError",
    "LlmProvider",
    "LlmResult",
    "ModelInfo",
    "make_provider",
]
