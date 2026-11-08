"""Request/response and on-disk models for the backend."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from circuit_core.guides import GuideDraft

PROJECT_FILE_VERSION = 2
"""2: boards live in boards/<id>/ (version 1 kept one circuit at the project root)."""
BOARD_FILE_VERSION = 1


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
    boards: list[str] = Field(default_factory=list, description="Board ids in display order")


class BoardFile(_Model):
    """projects/<id>/boards/<board>/board.json — one board = one circuit = one KiCad project."""

    schema_version: int = BOARD_FILE_VERSION
    id: str
    name: str
    description: str = ""
    created_at: str
    updated_at: str
    current_revision: int | None = None


class BoardSummary(BoardFile):
    path: str
    kicad_project: str


class ProjectSummary(ProjectFile):
    path: str
    last_opened_at: str | None = None
    board_list: list[BoardSummary] = []


class ProjectCreate(_Model):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class ProjectUpdate(_Model):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class Requirements(_Model):
    text: str = Field(max_length=200_000)


RevisionSource = Literal["user_edit", "import", "restore", "inventory", "proposal"]


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


class BoardCreate(_Model):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)


class BoardUpdate(_Model):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)


class AppSettings(_Model):
    last_project_id: str | None = None
    last_board_id: str | None = None
    last_tab: str | None = Field(default=None, max_length=40)
    last_view: Literal["flow", "advanced"] | None = None
    """Project screen: the seven-step flow (default) or the advanced tabs (ADR-017)."""


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


class PageOutput(_Model):
    number: int
    sheet_id: str
    name: str
    schematic: str
    svg: str | None = None
    paper: str
    bbox_mm: list[float] | None = None
    """Drawing extent (x, y, w, h) the UI uses to crop the SVG preview; the files are not modified."""


class Manifest(_Model):
    """projects/<id>/boards/<board>/outputs/manifest.json"""

    schema_version: int = 2
    project_id: str
    board_id: str
    kicad_project: str
    source_revision: int
    generated_at: str
    generator: str
    automated_check: AutomatedCheck
    warnings: list[dict] = []
    pages: list[PageOutput] = []
    artifacts: list[Artifact]


JobStatus = Literal["queued", "running", "succeeded", "failed"]


class Job(_Model):
    id: str
    project_id: str
    board_id: str | None = None
    kind: str
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict | None = None
    error: dict | None = None
    progress: list[dict] = Field(default_factory=list)
    """Steps a long job has reached, oldest first: {at, step, text, data?}. The step screens show them live."""


class FileUpload(_Model):
    """A file sent as base64 inside JSON (keeps the API free of multipart handling)."""

    filename: str = Field(min_length=1, max_length=255)
    content_base64: str


class InventoryImportRequest(FileUpload):
    save: bool = Field(default=False, description="False = preview only")


class InventoryApplyRequest(_Model):
    apply_matched: bool = True
    choices: dict[str, str] = Field(default_factory=dict, description="component id -> inventory part id")


class MarkdownImportRequest(_Model):
    filename: str = Field(default="schematic.md", min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=2_000_000)


class DatasheetUpload(FileUpload):
    """A datasheet PDF for one part; the part number / symbol link it to components."""

    manufacturer: str | None = Field(default=None, max_length=120)
    part_numbers: list[str] = Field(default_factory=list, max_length=20)
    symbols: list[str] = Field(default_factory=list, max_length=20)


class DatasheetLink(_Model):
    manufacturer: str | None = Field(default=None, max_length=120)
    part_numbers: list[str] = Field(default_factory=list, max_length=20)
    symbols: list[str] = Field(default_factory=list, max_length=20)


class DatasheetFetchRequest(_Model):
    candidates: list[str] = Field(default_factory=list, max_length=200,
                                  description="Candidate ids the user confirmed (from the board report).")
    urls: list[str] = Field(default_factory=list, max_length=200,
                            description="Or first URLs of candidates. Only links the app found for the board are used.")
    proposal_id: str | None = Field(default=None, max_length=20)
    fallback: bool = Field(default=True, description="If a part's chosen candidate fails, try its other candidates.")


class DatasheetSearchRequest(_Model):
    proposal_id: str | None = Field(default=None, max_length=20)
    references: list[str] = Field(default_factory=list, max_length=200,
                                  description="Only these parts (default: the specific parts with no datasheet).")


class DistributorKeyBody(_Model):
    api_key: str | None = Field(default=None, max_length=200)
    client_id: str | None = Field(default=None, max_length=200)
    client_secret: str | None = Field(default=None, max_length=200)


class DatasheetReviewed(_Model):
    reviewed: bool


class ProposalCreate(_Model):
    title: str = Field(min_length=1, max_length=200)
    rationale: str | None = Field(default=None, max_length=20_000)
    ops: list[dict] = Field(min_length=1, max_length=2000, description="circuit_core.patch operations")


class QuickFixChoice(_Model):
    fix_id: str = Field(min_length=1, max_length=200)
    kind: Literal["resistor", "direct", "no_connect"]
    net: str | None = Field(default=None, max_length=64)
    value: str | None = Field(default=None, max_length=20)


class QuickFixApply(_Model):
    proposal_id: str | None = Field(default=None, description="Fix this open proposal (a follow-up proposal); "
                                    "None fixes the saved circuit.")
    choices: list[QuickFixChoice] = Field(min_length=1, max_length=50)


class ProposalApprove(_Model):
    accept_failed_checks: bool = False
    generate: bool = Field(default=True, description="Regenerate the board outputs after saving.")


class ProposalReject(_Model):
    note: str | None = Field(default=None, max_length=2000)


class AiSettings(_Model):
    """Stored in the SQLite settings table. Never contains the API key."""

    enabled: bool = False
    provider: Literal["gemini", "claude", "chatgpt"] = "gemini"
    model: str = Field(default="gemini-flash-latest", min_length=1, max_length=100)
    max_output_tokens: int = Field(default=32768, ge=1024, le=1_000_000)
    timeout_s: int = Field(default=300, ge=10, le=3600)


class AiSettingsUpdate(_Model):
    enabled: bool | None = None
    provider: Literal["gemini", "claude", "chatgpt"] | None = None
    """Switching provider without a model picks that provider's default model."""
    model: str | None = Field(default=None, min_length=1, max_length=100)
    max_output_tokens: int | None = Field(default=None, ge=1024, le=1_000_000)
    timeout_s: int | None = Field(default=None, ge=10, le=3600)


class ApiKeyBody(_Model):
    api_key: str = Field(min_length=10, max_length=500)


# --- design guide ----------------------------------------------------------------------------------

class GuideCreate(_Model):
    draft: GuideDraft
    origin: Literal["engineer", "review", "ai_review", "feedback", "check"] = "engineer"
    by: str = Field(default="", max_length=100)
    adopt: bool = Field(default=False, description="Adopt at once (only for text a person wrote).")
    ai_text: bool = Field(default=False, description="The text was drafted by the AI: saved as a candidate.")


class GuideUpdate(_Model):
    draft: GuideDraft
    by: str = Field(default="", max_length=100)


class GuideDecision(_Model):
    status: Literal["candidate", "adopted", "retired"]
    by: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class GuideDraftRequest(_Model):
    feedback: str = Field(min_length=3, max_length=4000, description="What the person said should be done next time.")
    proposal_id: str | None = Field(default=None, max_length=20)
    include_circuit: bool = Field(default=True, description="Send a summary of the circuit as context.")


class GuideImport(_Model):
    filename: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2_000_000)
    by: str = Field(default="", max_length=100)
    adopt: bool = Field(default=False, description="Adopt at once (a document people wrote); default: candidates.")
