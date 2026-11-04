"""Request/response and on-disk models for the backend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROJECT_FILE_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectFile(_Model):
    """projects/<id>/project.json"""

    schema_version: int = PROJECT_FILE_VERSION
    id: str
    name: str
    description: str = ""
    created_at: str
    updated_at: str
    current_revision: int | None = None


class ProjectSummary(ProjectFile):
    path: str
    last_opened_at: str | None = None


class ProjectCreate(_Model):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class ProjectUpdate(_Model):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class Requirements(_Model):
    text: str = Field(max_length=200_000)


RevisionSource = Literal["user_edit", "import", "restore"]


class RevisionInfo(_Model):
    """projects/<id>/revisions/<n>.meta.json"""

    revision: int
    created_at: str
    source: RevisionSource
    checksum: str
    parent: int | None = None
    restored_from: int | None = None
    note: str | None = None
    component_count: int
    net_count: int


class AppSettings(_Model):
    last_project_id: str | None = None
    last_tab: str | None = Field(default=None, max_length=40)


class ExportRequest(_Model):
    force: bool = Field(default=False, description="Overwrite outputs that were modified outside the app.")


AutomatedStatus = Literal["not_checked", "passed", "failed"]


class Artifact(_Model):
    """Plan v1.2 section 7.1."""

    artifact_id: str
    artifact_type: str
    path: str
    source_revision: int
    tool_version: str
    checksum: str
    validation_status: AutomatedStatus | None
    created_at: str


class AutomatedCheck(_Model):
    status: AutomatedStatus
    reason: str | None = None
    kicad_version: str | None = None
    erc_errors: int | None = None
    erc_warnings: int | None = None
    netlist_matches_ir: bool | None = None
    violations: list[dict] = []
    netlist_diff: str | None = None


class Manifest(_Model):
    """projects/<id>/outputs/manifest.json"""

    schema_version: int = 1
    project_id: str
    source_revision: int
    generated_at: str
    generator: str
    automated_check: AutomatedCheck
    warnings: list[dict] = []
    preview_bbox_mm: list[float] | None = None
    """Drawing extent (x, y, w, h) used by the UI to crop the SVG preview; files are not modified."""
    artifacts: list[Artifact]


JobStatus = Literal["queued", "running", "succeeded", "failed"]


class Job(_Model):
    id: str
    project_id: str
    kind: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict | None = None
    error: dict | None = None
