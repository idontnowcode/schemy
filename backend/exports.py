"""Generate every output of a board's current revision into its outputs/ (plan v1.2 sections 7 and 7.1).

The run happens in a staging folder; outputs/ is swapped only when everything
was written, so a failed run never leaves half-updated files. Before replacing
outputs/, files that were changed outside the app (checksum differs from the
manifest) block the run unless the caller passes ``force``.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from circuit_core.export import bom_csv, canonical_json, circuit_markdown
from kicad_adapter import (
    KiCadCli,
    KiCadCliError,
    SchematicGenerationError,
    SymbolLibrary,
    generate_schematic,
    run_checks,
)
from kicad_adapter.schematic import GENERATOR, GENERATOR_VERSION

from .config import APP_VERSION
from .errors import ApiError, issue_dict
from .report import rationale_report
from .schemas import Artifact, AutomatedCheck, Manifest, PageOutput
from .store import ProjectStore, atomic_write, kicad_project_name, now_iso, sha256_file

MANIFEST = "manifest.json"
_UUID_NS = uuid.UUID("9b8d6c1e-2f4a-4e58-a1b7-3c5d7e9f0a12")
_FIXED_TYPES = {
    "circuit.json": "json",
    "circuit.md": "markdown",
    "rationale.md": "rationale",
    "bom.csv": "bom",
    "symbol_sources.json": "symbol_sources",
    "erc.json": "erc_report",
    "check_report.json": "check_report",
}
_SUFFIX_TYPES = {".kicad_sch": "schematic", ".kicad_pro": "kicad_project", ".net": "netlist", ".svg": "svg"}
_FROM_KICAD = {"netlist", "svg", "erc_report", "check_report"}
# KiCad rewrites the project file when a user opens and closes it, so it is not
# used to detect external edits.
_NOT_WATCHED = {"kicad_project"}
_SAFE_NAME = re.compile(r"^[^/\\\x00]+$")


def artifact_type(name: str) -> str | None:
    if name in _FIXED_TYPES:
        return _FIXED_TYPES[name]
    return next((t for suffix, t in _SUFFIX_TYPES.items() if name.endswith(suffix)), None)


def read_manifest(outputs: Path) -> Manifest | None:
    path = outputs / MANIFEST
    if not path.is_file():
        return None
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def outputs_dir(store: ProjectStore, project_id: str, board_id: str) -> Path:
    return store.board_dir(project_id, board_id) / "outputs"


def output_path(store: ProjectStore, project_id: str, board_id: str, name: str) -> Path | None:
    """Path of a generated file listed in the manifest, or None (never escapes outputs/)."""
    if not _SAFE_NAME.fullmatch(name) or name in (".", ".."):
        return None
    outputs = outputs_dir(store, project_id, board_id)
    manifest = read_manifest(outputs)
    if manifest is None or name not in {a.path for a in manifest.artifacts}:
        return None
    path = outputs / name
    return path if path.is_file() else None


def outputs_status(store: ProjectStore, project_id: str, board_id: str) -> dict:
    board = store.get_board(project_id, board_id)
    outputs = outputs_dir(store, project_id, board_id)
    manifest = read_manifest(outputs)
    if manifest is None:
        return {"manifest": None, "stale": False, "externally_modified": [], "missing": [],
                "current_revision": board.current_revision, "path": str(outputs)}
    modified, missing = [], []
    for art in manifest.artifacts:
        f = outputs / art.path
        if not f.is_file():
            missing.append(art.path)
        elif art.artifact_type not in _NOT_WATCHED and sha256_file(f) != art.checksum:
            modified.append(art.path)
    return {
        "manifest": manifest.model_dump(),
        "stale": manifest.source_revision != board.current_revision,
        "externally_modified": modified,
        "missing": missing,
        "current_revision": board.current_revision,
        "path": str(outputs),
    }


def external_edit_error(paths: list[str]) -> ApiError:
    return ApiError(409, "EXTERNAL_MODIFICATION",
                    "Generated files were changed outside the app: " + ", ".join(paths),
                    recovery_hint="Copy your edits elsewhere if you need them, then regenerate with force.",
                    details={"paths": paths})


def run_export(store: ProjectStore, project_id: str, board_id: str, *, symbol_dirs: list[Path],
               kicad_factory: Callable[[], KiCadCli | None], force: bool = False,
               run_id: str | None = None) -> dict:
    circuit, revision = store.get_circuit(project_id, board_id)
    if circuit is None or revision is None:
        raise ApiError(409, "NO_CIRCUIT", "This board has no saved Circuit IR yet.",
                       recovery_hint="Save a Circuit IR first.")
    folder = store.board_dir(project_id, board_id)
    outputs = folder / "outputs"

    status = outputs_status(store, project_id, board_id)
    if status["externally_modified"] and not force:
        raise external_edit_error(status["externally_modified"])
    if not symbol_dirs:
        raise ApiError(409, "NO_SYMBOL_LIBRARY", "No KiCad symbol library folder is configured.",
                       recovery_hint="Install KiCad 10 or set AICS_SYMBOL_DIRS / KICAD10_SYMBOL_DIR.")

    kproj = kicad_project_name(project_id, board_id)
    run_id = run_id or uuid.uuid4().hex
    staging = folder / f".outputs-staging-{run_id}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        try:
            generated = generate_schematic(circuit, SymbolLibrary(symbol_dirs), project_name=kproj)
        except SchematicGenerationError as exc:
            raise ApiError(422, "GENERATION_FAILED", "The Circuit IR cannot be turned into a schematic.",
                           affected_component_ids=sorted({c for i in exc.issues for c in i.affected_component_ids}),
                           recovery_hint="Fix the listed issues in the Circuit IR.",
                           details={"issues": [issue_dict(i) for i in exc.issues]}) from exc
        sch = generated.write(staging)
        atomic_write(staging / "circuit.json", canonical_json(circuit))
        atomic_write(staging / "circuit.md", circuit_markdown(circuit, revision=revision))
        atomic_write(staging / "bom.csv", bom_csv(circuit))
        atomic_write(staging / "symbol_sources.json", json.dumps(generated.symbol_sources, indent=2) + "\n")

        check = _run_checks(circuit, generated, sch, kicad_factory)
        for leftover in staging.glob("*.kicad_prl"):
            leftover.unlink()
        report = rationale_report(store, project_id, board_id, automated_check=check.model_dump(), decided_only=True)
        atomic_write(staging / "rationale.md", report["markdown"] + "\n")

        pages = []
        for page in generated.pages:
            # kicad-cli names SVGs <project>.svg for the root and <project>-<sheet name>.svg for the others.
            svg_name = f"{kproj}.svg" if page.number == 1 else f"{kproj}-{page.name}.svg"
            pages.append(PageOutput(
                number=page.number, sheet_id=page.sheet_id, name=page.name, schematic=page.filename,
                svg=svg_name if (staging / svg_name).is_file() else None, paper=page.paper,
                bbox_mm=list(page.bbox) if page.bbox else None,
            ))

        created = now_iso()
        artifacts = []
        for f in sorted(staging.iterdir()):
            atype = artifact_type(f.name)
            if not f.is_file() or atype is None:
                continue
            from_kicad = atype in _FROM_KICAD
            tool = f"kicad-cli {check.kicad_version}" if from_kicad else f"{GENERATOR} {GENERATOR_VERSION}"
            kicad_related = from_kicad or atype in ("schematic", "kicad_project")
            artifacts.append(Artifact(
                artifact_id=str(uuid.uuid5(_UUID_NS, f"{project_id}/{board_id}/{revision}/{f.name}")),
                artifact_type=atype, path=f.name, source_revision=revision, tool_version=tool,
                checksum=sha256_file(f), validation_status=check.status if kicad_related else None,
                created_at=created,
            ))
        manifest = Manifest(
            project_id=project_id, board_id=board_id, kicad_project=kproj, source_revision=revision,
            generated_at=created, generator=f"AI Circuit Studio {APP_VERSION}", automated_check=check,
            warnings=[issue_dict(w) for w in generated.warnings], pages=pages, artifacts=artifacts,
        )
        atomic_write(staging / MANIFEST, manifest.model_dump_json(indent=2) + "\n")
        _swap(staging, outputs, run_id)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {"source_revision": revision, "automated_check": check.model_dump(),
            "artifacts": [a.path for a in artifacts], "pages": len(pages), "warnings": manifest.warnings}


def _run_checks(circuit, generated, sch: Path, kicad_factory) -> AutomatedCheck:
    cli = kicad_factory()
    if cli is None:
        return AutomatedCheck(status="not_checked",
                              reason="kicad-cli not found (install KiCad 10 or set AICS_KICAD_CLI).")
    try:
        report = run_checks(circuit, generated, sch, cli)
    except KiCadCliError as exc:
        return AutomatedCheck(status="not_checked", reason=f"kicad-cli failed: {exc}")
    erc = report.erc
    return AutomatedCheck(
        status="passed" if report.automated_checks_passed else "failed",
        kicad_version=erc.kicad_version or None,
        erc_errors=len(erc.errors), erc_warnings=len(erc.warnings),
        netlist_matches_ir=report.connectivity.ok,
        violations=erc.summary()["violations"],
        netlist_diff=None if report.connectivity.ok else report.connectivity.describe(),
    )


def _swap(staging: Path, outputs: Path, run_id: str) -> None:
    old = outputs.with_name(f".outputs-old-{run_id}")
    try:
        if outputs.exists():
            outputs.rename(old)
        staging.rename(outputs)
    except OSError as exc:
        if old.exists() and not outputs.exists():
            old.rename(outputs)
        raise ApiError(409, "OUTPUTS_LOCKED", f"Could not replace the outputs folder: {exc}",
                       recovery_hint="Close the generated project in KiCad (or any program using it) "
                                     "and retry.") from exc
    shutil.rmtree(old, ignore_errors=True)
