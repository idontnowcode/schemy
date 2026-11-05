"""Backend settings. Everything that differs between machines is configured here."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ai_engine import ChatGptLogin, KeyStore, LlmProvider, make_provider
from kicad_adapter import KiCadCli
from kicad_adapter.symbols import default_symbol_dirs

from .datasheets import Fetcher, fetch_pdf

APP_VERSION = "0.1.0"
REPO_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def default_workspace() -> Path:
    if env := os.environ.get("AICS_WORKSPACE"):
        return Path(env)
    return Path.home() / "Documents" / "AI Circuit Studio"


def _symbol_dirs_from_env() -> list[Path]:
    if env := os.environ.get("AICS_SYMBOL_DIRS"):
        return [Path(p) for p in env.split(os.pathsep) if p]
    return [d for d in default_symbol_dirs() if d.is_dir()]


@dataclass
class Settings:
    workspace: Path = field(default_factory=default_workspace)
    token: str | None = None
    """When set, every /api request must send ``Authorization: Bearer <token>``."""
    symbol_dirs: list[Path] = field(default_factory=_symbol_dirs_from_env)
    ui_dir: Path | None = None
    """Built desktop UI (desktop/dist) to serve at ``/``; omitted in API-only mode."""
    kicad_factory: Callable[[], KiCadCli | None] = KiCadCli.detect
    examples_dir: Path | None = REPO_EXAMPLES
    """Folders with a circuit.json each, offered as starting points in the UI."""
    keystore: KeyStore = field(default_factory=KeyStore)
    chatgpt_login: ChatGptLogin = field(default_factory=ChatGptLogin)
    """The ChatGPT account of this installation (Sign in with ChatGPT); tests point it at a fake server."""
    ai_provider_factory: Callable[[str, str, str, float], LlmProvider] = make_provider
    """(provider, api_key, model, timeout_s) -> provider; tests inject a fake."""
    datasheet_fetcher: Fetcher = fetch_pdf
    """url -> (pdf bytes, final url); tests inject a fake. Only links from the KiCad library are fetched."""
