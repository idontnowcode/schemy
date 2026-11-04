"""Local FastAPI backend: projects, revisions, exports and jobs (Phase 1)."""

from .app import create_app
from .config import APP_VERSION, Settings

__all__ = ["APP_VERSION", "Settings", "create_app"]
