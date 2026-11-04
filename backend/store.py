"""Project files, revisions and the SQLite index (Project Service + Revision Service).

Plan v1.2 rule 12: this module is the only one that writes a project's source
files (project.json, requirements.md, circuit.json, revisions/). Generated
files live under outputs/ and are written by backend.exports.

Files are the source of truth. The SQLite index (workspace/index.sqlite) is a
cache for listing, job history and app settings, rebuilt from the folders on
start-up, so a project survives a deleted or stale index.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from circuit_core import CircuitIR
from circuit_core.export import canonical_json

from .errors import ApiError, not_found
from .schemas import AppSettings, Job, ProjectFile, ProjectSummary, RevisionInfo, RevisionSource

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

REQUIREMENTS_TEMPLATE = """# 요구사항

<!-- 회로 요구사항을 자유롭게 적으세요. 예: 입력 전압 범위, 출력 전류, 사용 부품, 커넥터, 환경 조건 -->
"""

DESIGN_NOTES_TEMPLATE = """# 설계 노트

<!-- 설계 근거, 검토 메모, 미해결 항목 -->
"""


def now_iso() -> str:
    """ISO 8601 with the local UTC offset (plan 7.1)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:48].strip("-")
    return slug or "project"


class ProjectStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.projects_dir = self.workspace / "projects"
        self.trash_dir = self.workspace / ".trash"
        self.db_path = self.workspace / "index.sqlite"
        self._lock = threading.RLock()
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.rescan()
        self._fail_interrupted_jobs()

    # --- database ---------------------------------------------------------

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_opened_at TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_by_project ON jobs(project_id, created_at);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )

    def rescan(self) -> None:
        """Rebuild the project index from the folders on disk."""
        with self._lock, self._db() as db:
            found = {}
            for folder in sorted(self.projects_dir.iterdir()):
                pf = folder / "project.json"
                if folder.is_dir() and pf.is_file():
                    try:
                        meta = ProjectFile.model_validate_json(pf.read_text(encoding="utf-8"))
                    except (ValidationError, ValueError):
                        continue
                    if meta.id == folder.name:
                        found[meta.id] = meta
            known = {row["id"] for row in db.execute("SELECT id FROM projects")}
            for gone in known - set(found):
                db.execute("DELETE FROM projects WHERE id = ?", (gone,))
            for meta in found.values():
                db.execute(
                    "INSERT INTO projects (id, name, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
                    (meta.id, meta.name, meta.updated_at),
                )

    # --- projects -----------------------------------------------------------

    def project_dir(self, project_id: str) -> Path:
        if not _ID_RE.fullmatch(project_id):
            raise not_found("project", project_id)
        return self.projects_dir / project_id

    def _read_meta(self, project_id: str) -> ProjectFile:
        pf = self.project_dir(project_id) / "project.json"
        if not pf.is_file():
            raise not_found("project", project_id)
        return ProjectFile.model_validate_json(pf.read_text(encoding="utf-8"))

    def _write_meta(self, meta: ProjectFile) -> None:
        atomic_write(self.project_dir(meta.id) / "project.json", meta.model_dump_json(indent=2) + "\n")
        with self._db() as db:
            db.execute(
                "INSERT INTO projects (id, name, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
                (meta.id, meta.name, meta.updated_at),
            )

    def _summary(self, meta: ProjectFile, last_opened: str | None) -> ProjectSummary:
        return ProjectSummary(**meta.model_dump(), path=str(self.project_dir(meta.id)), last_opened_at=last_opened)

    def get_project(self, project_id: str) -> ProjectSummary:
        meta = self._read_meta(project_id)
        with self._db() as db:
            row = db.execute("SELECT last_opened_at FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._summary(meta, row["last_opened_at"] if row else None)

    def list_projects(self) -> list[ProjectSummary]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id, last_opened_at FROM projects "
                "ORDER BY COALESCE(last_opened_at, updated_at) DESC, id"
            ).fetchall()
        out = []
        for row in rows:
            try:
                out.append(self._summary(self._read_meta(row["id"]), row["last_opened_at"]))
            except ApiError:
                continue
        return out

    def create_project(self, name: str, description: str = "") -> ProjectSummary:
        with self._lock:
            base = slugify(name)
            project_id, n = base, 1
            while (self.projects_dir / project_id).exists():
                n += 1
                project_id = f"{base[:60]}-{n}"
            folder = self.projects_dir / project_id
            folder.mkdir(parents=True)
            for sub in ("datasheets", "revisions", "outputs"):
                (folder / sub).mkdir()
            atomic_write(folder / "requirements.md", REQUIREMENTS_TEMPLATE)
            atomic_write(folder / "design_notes.md", DESIGN_NOTES_TEMPLATE)
            ts = now_iso()
            meta = ProjectFile(id=project_id, name=name.strip(), description=description,
                               created_at=ts, updated_at=ts)
            self._write_meta(meta)
            return self.get_project(project_id)

    def update_project(self, project_id: str, *, name: str | None = None,
                       description: str | None = None) -> ProjectSummary:
        with self._lock:
            meta = self._read_meta(project_id)
            changes = {}
            if name is not None:
                changes["name"] = name.strip()
            if description is not None:
                changes["description"] = description
            if changes:
                self._write_meta(meta.model_copy(update={**changes, "updated_at": now_iso()}))
            return self.get_project(project_id)

    def delete_project(self, project_id: str) -> Path:
        """Move the folder into the workspace trash; nothing is erased."""
        with self._lock:
            self._read_meta(project_id)
            self.trash_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.trash_dir / f"{project_id}-{stamp}"
            shutil.move(str(self.project_dir(project_id)), str(target))
            with self._db() as db:
                db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            settings = self.get_settings()
            if settings.last_project_id == project_id:
                self.put_settings(settings.model_copy(update={"last_project_id": None}))
            return target

    def mark_opened(self, project_id: str) -> ProjectSummary:
        self._read_meta(project_id)
        with self._db() as db:
            db.execute("UPDATE projects SET last_opened_at = ? WHERE id = ?", (now_iso(), project_id))
        return self.get_project(project_id)

    # --- requirements ---------------------------------------------------------

    def get_requirements(self, project_id: str) -> str:
        path = self.project_dir(project_id) / "requirements.md"
        self._read_meta(project_id)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def put_requirements(self, project_id: str, text: str) -> None:
        with self._lock:
            meta = self._read_meta(project_id)
            atomic_write(self.project_dir(project_id) / "requirements.md", text)
            self._write_meta(meta.model_copy(update={"updated_at": now_iso()}))

    # --- circuit and revisions -----------------------------------------------

    def _revision_paths(self, project_id: str, n: int) -> tuple[Path, Path]:
        rev_dir = self.project_dir(project_id) / "revisions"
        return rev_dir / f"{n:04d}.circuit.json", rev_dir / f"{n:04d}.meta.json"

    def _latest_revision_number(self, project_id: str) -> int:
        rev_dir = self.project_dir(project_id) / "revisions"
        numbers = [int(p.name[:4]) for p in rev_dir.glob("[0-9][0-9][0-9][0-9].meta.json")]
        return max(numbers, default=0)

    def get_circuit(self, project_id: str) -> tuple[CircuitIR | None, int | None]:
        """Current IR and its revision. A damaged circuit.json is recovered from the revision snapshot."""
        meta = self._read_meta(project_id)
        path = self.project_dir(project_id) / "circuit.json"
        if path.is_file():
            try:
                return CircuitIR.model_validate_json(path.read_text(encoding="utf-8")), meta.current_revision
            except (ValidationError, ValueError):
                pass
        if meta.current_revision:
            snapshot, _ = self._revision_paths(project_id, meta.current_revision)
            if snapshot.is_file():
                text = snapshot.read_text(encoding="utf-8")
                with self._lock:
                    atomic_write(path, text)
                return CircuitIR.model_validate_json(text), meta.current_revision
        return None, None

    def save_circuit(self, project_id: str, circuit: CircuitIR, *, source: RevisionSource,
                     note: str | None = None, restored_from: int | None = None) -> tuple[RevisionInfo, bool]:
        """Store ``circuit`` as the current IR. Returns (revision, created); identical content creates nothing."""
        with self._lock:
            meta = self._read_meta(project_id)
            if circuit.project_id != project_id:
                circuit = circuit.model_copy(update={"project_id": project_id})
            text = canonical_json(circuit)
            checksum = sha256_text(text)
            if meta.current_revision:
                current = self.get_revision_info(project_id, meta.current_revision)
                if current.checksum == checksum:
                    return current, False
            n = self._latest_revision_number(project_id) + 1
            info = RevisionInfo(
                revision=n, created_at=now_iso(), source=source, checksum=checksum,
                parent=meta.current_revision, restored_from=restored_from, note=note,
                component_count=len(circuit.components), net_count=len(circuit.nets),
            )
            snapshot, meta_path = self._revision_paths(project_id, n)
            # Order matters for crash recovery: snapshot first, then the working copy, then the pointer.
            atomic_write(snapshot, text)
            atomic_write(meta_path, info.model_dump_json(indent=2) + "\n")
            atomic_write(self.project_dir(project_id) / "circuit.json", text)
            self._write_meta(meta.model_copy(update={"current_revision": n, "updated_at": info.created_at}))
            return info, True

    def get_revision_info(self, project_id: str, n: int) -> RevisionInfo:
        _, meta_path = self._revision_paths(project_id, n)
        if not meta_path.is_file():
            raise not_found("revision", n)
        return RevisionInfo.model_validate_json(meta_path.read_text(encoding="utf-8"))

    def list_revisions(self, project_id: str) -> list[RevisionInfo]:
        self._read_meta(project_id)
        latest = self._latest_revision_number(project_id)
        out = []
        for n in range(latest, 0, -1):
            try:
                out.append(self.get_revision_info(project_id, n))
            except ApiError:
                continue
        return out

    def get_revision(self, project_id: str, n: int) -> CircuitIR:
        snapshot, _ = self._revision_paths(project_id, n)
        if not snapshot.is_file():
            raise not_found("revision", n)
        return CircuitIR.model_validate_json(snapshot.read_text(encoding="utf-8"))

    def restore_revision(self, project_id: str, n: int) -> tuple[RevisionInfo, bool]:
        """Make revision ``n`` current again by recording it as a new revision."""
        circuit = self.get_revision(project_id, n)
        return self.save_circuit(project_id, circuit, source="restore", restored_from=n)

    # --- settings ---------------------------------------------------------------

    def get_settings(self) -> AppSettings:
        with self._db() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'app'").fetchone()
        if row is None:
            return AppSettings()
        try:
            return AppSettings.model_validate_json(row["value"])
        except (ValidationError, ValueError):
            return AppSettings()

    def put_settings(self, settings: AppSettings) -> AppSettings:
        with self._db() as db:
            db.execute(
                "INSERT INTO settings (key, value) VALUES ('app', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (settings.model_dump_json(),),
            )
        return settings

    # --- jobs -------------------------------------------------------------------

    def create_job(self, project_id: str, kind: str) -> Job:
        job = Job(id=uuid.uuid4().hex, project_id=project_id, kind=kind, status="queued", created_at=now_iso())
        with self._db() as db:
            db.execute(
                "INSERT INTO jobs (id, project_id, kind, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (job.id, job.project_id, job.kind, job.status, job.created_at),
            )
        return job

    def update_job(self, job_id: str, **fields) -> None:
        cols, values = [], []
        for key, value in fields.items():
            cols.append(f"{key} = ?")
            values.append(json.dumps(value, ensure_ascii=False) if key in ("result", "error") else value)
        with self._db() as db:
            db.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id = ?", (*values, job_id))

    def _job_from_row(self, row: sqlite3.Row) -> Job:
        data = dict(row)
        for key in ("result", "error"):
            data[key] = json.loads(data[key]) if data[key] else None
        return Job(**data)

    def get_job(self, job_id: str) -> Job:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise not_found("job", job_id)
        return self._job_from_row(row)

    def list_jobs(self, project_id: str, limit: int = 20) -> list[Job]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [self._job_from_row(r) for r in rows]

    def _fail_interrupted_jobs(self) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? "
                "WHERE status IN ('queued', 'running')",
                (now_iso(), json.dumps({"error_code": "INTERRUPTED",
                                        "message": "The app stopped before this job finished."})),
            )
