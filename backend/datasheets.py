"""Workspace-level datasheet library (<workspace>/datasheets/) and the PDF downloader.

One library per workspace, like the parts inventory: a datasheet describes a part, not a design,
so every project reuses the same file and the same summary.

    datasheets/
      index.json              entries: file, hash, source URL, linked part numbers/symbols, summary info
      ds-<sha12>.pdf          the PDF as received (never modified)
      ds-<sha12>.summary.md   the summary Markdown as imported (Copilot, person or AI)
      ds-<sha12>.facts.json   what the app parsed from it (used by the checks)
      candidates.json         links found for each part (distributor searches), see datasheet_sources
      ai_runs/                one record per AI call (no API keys)

Downloads only ever use links the app found itself (the KiCad symbol link, the maker's address
pattern, a distributor search answer), after the user confirmed them; they go over HTTPS, are
size-limited and must be a PDF. The file is checked for the part number before anything else uses it.
"""

from __future__ import annotations

import io
import json
import re
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Literal

from pydantic import BaseModel, Field

from circuit_core.datasheet_sources import DatasheetCandidate
from circuit_core.datasheets import DatasheetFacts, identity_check, normalize_part, symbol_part_name
from circuit_core.models import Component

from .store import atomic_write, atomic_write_bytes, now_iso, sha256_bytes
from .uploads import MB

MAX_DATASHEET_BYTES = 50 * MB
TEXT_PAGES = 6
"""Pages read for the part-number check (part numbers are on the first pages)."""

Fetcher = Callable[[str], tuple[bytes, str]]
"""url -> (pdf bytes, final url after redirects). Tests inject a fake."""


class DatasheetError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.hint, self.status = code, message, hint, status


EntrySource = Literal["upload", "kicad_symbol", "maker_site", "mouser", "digikey"]


class PartSearch(BaseModel):
    """What the distributor searches answered for one part (keyed by the normalised part number)."""
    term: str
    searched_at: str
    candidates: list[DatasheetCandidate] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


class CandidateBook(BaseModel):
    schema_version: int = 1
    parts: dict[str, PartSearch] = Field(default_factory=dict)


class SummaryInfo(BaseModel):
    origin: Literal["markdown", "ai"]
    source_name: str
    imported_at: str
    reviewed: bool = False
    warnings: int = 0
    model: str | None = None


class DatasheetEntry(BaseModel):
    id: str
    file: str
    original_name: str
    sha256: str
    bytes: int
    page_count: int | None = None
    title: str | None = None
    source: EntrySource
    urls: list[str] = Field(default_factory=list)
    final_url: str | None = None
    added_at: str
    manufacturer: str | None = None
    part_numbers: list[str] = Field(default_factory=list, description="MPNs linked to this file.")
    symbols: list[str] = Field(default_factory=list, description="KiCad symbols linked to this file.")
    identity: dict = Field(default_factory=dict, description="circuit_core.datasheets.identity_check result.")
    summary: SummaryInfo | None = None


class LibraryIndex(BaseModel):
    schema_version: int = 1
    entries: list[DatasheetEntry] = Field(default_factory=list)


def read_pdf_text(data: bytes, pages: int = TEXT_PAGES) -> tuple[str, int | None, str | None]:
    """(text of the first pages, page count, title). Text is "" for scanned or encrypted files."""
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - AES without a crypto backend, real passwords ...
                return "", None, None
        count = len(reader.pages)
        chunks = []
        for page in reader.pages[:pages]:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - one bad page must not hide the others
                chunks.append("")
        title = None
        try:
            title = (reader.metadata.title or None) if reader.metadata else None
        except Exception:  # noqa: BLE001
            title = None
        return "\n".join(chunks), count, title
    except Exception as exc:  # noqa: BLE001 - pypdf raises many types for broken files
        raise DatasheetError("DATASHEET_UNREADABLE", f"PDF를 읽지 못했습니다: {exc}",
                             "파일이 손상되었을 수 있습니다. 제조사 사이트에서 다시 받아 보세요.") from exc


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 4:
            return line[:120]
    return None


class DatasheetLibrary:
    def __init__(self, workspace: Path):
        self.dir = Path(workspace) / "datasheets"
        self.index_path = self.dir / "index.json"
        self._lock = threading.RLock()

    # --- index -------------------------------------------------------------------------------

    def _load(self) -> LibraryIndex:
        if not self.index_path.is_file():
            return LibraryIndex()
        try:
            return LibraryIndex.model_validate_json(self.index_path.read_text(encoding="utf-8"))
        except ValueError:
            return LibraryIndex()

    def _save(self, index: LibraryIndex) -> None:
        atomic_write(self.index_path, index.model_dump_json(indent=2) + "\n")

    def entries(self) -> list[DatasheetEntry]:
        return self._load().entries

    def get(self, entry_id: str) -> DatasheetEntry:
        for e in self.entries():
            if e.id == entry_id:
                return e
        raise DatasheetError("DATASHEET_NOT_FOUND", f"데이터시트 '{entry_id}'가 없습니다.", status=404)

    def pdf_path(self, entry: DatasheetEntry) -> Path:
        return self.dir / entry.file

    # --- adding ------------------------------------------------------------------------------

    def add(self, data: bytes, *, original_name: str, source: EntrySource,
            url: str | None = None, final_url: str | None = None, manufacturer: str | None = None,
            part_numbers: list[str] | tuple[str, ...] = (), symbols: list[str] | tuple[str, ...] = (),
            expect: list[str] | tuple[str, ...] = ()) -> tuple[DatasheetEntry, bool]:
        """Store a PDF (deduplicated by content) and link it to part numbers / symbols.
        With ``expect`` (a URL built from a part number), a readable PDF that names neither the part nor
        ``expect`` is refused (DATASHEET_WRONG_PART) and nothing is stored. Returns (entry, created)."""
        if len(data) > MAX_DATASHEET_BYTES:
            raise DatasheetError("DATASHEET_TOO_LARGE", f"PDF가 너무 큽니다 (최대 {MAX_DATASHEET_BYTES // MB} MB).",
                                 status=413)
        if b"%PDF" not in data[:1024]:
            raise DatasheetError("NOT_A_PDF", f"'{original_name}'은(는) PDF 파일이 아닙니다.")
        text, pages, title = read_pdf_text(data)
        family_name = None
        if expect:
            names = [*part_numbers, *(s.split(":", 1)[-1] for s in symbols)]
            haystack = normalize_part(text)
            if haystack and identity_check(text, names)["status"] == "not_found":
                named = [normalize_part(e) for e in expect if normalize_part(e) and normalize_part(e) in haystack]
                if not named:
                    raise DatasheetError("DATASHEET_WRONG_PART", f"받은 PDF에 {', '.join(names or list(expect))} 이름이 "
                                         "없어 다른 부품의 문서로 보고 저장하지 않았습니다.",
                                         "다른 받을 곳을 고르거나 제조사 사이트에서 받은 PDF를 올리세요.")
                # The document names the family the URL asked for (TPS7A02 for TPS7A0233): a family match.
                if any(normalize_part(n).startswith(named[0]) for n in names):
                    family_name = named[0]
        sha = sha256_bytes(data)
        with self._lock:
            index = self._load()
            entry = next((e for e in index.entries if e.sha256 == sha), None)
            created = entry is None
            if entry is None:
                entry_id = f"ds-{sha[:12]}"
                atomic_write_bytes(self.dir / f"{entry_id}.pdf", data)
                entry = DatasheetEntry(id=entry_id, file=f"{entry_id}.pdf", original_name=PurePath(original_name).name,
                                       sha256=sha, bytes=len(data), page_count=pages,
                                       title=title or _first_line(text), source=source, added_at=now_iso())
                index.entries.append(entry)
            if url and url not in entry.urls:
                entry.urls.append(url)
            if final_url:
                entry.final_url = final_url
            if manufacturer and not entry.manufacturer:
                entry.manufacturer = manufacturer
            self._claim(index, entry, part_numbers, symbols)
            entry.identity = identity_check(text, [*entry.part_numbers, *(s.split(":", 1)[-1] for s in entry.symbols)])
            if family_name and entry.identity.get("status") == "not_found":
                entry.identity = {**entry.identity, "status": "family", "found": [family_name]}
            self._save(index)
            return entry, created

    def link(self, entry_id: str, *, part_numbers: list[str] = (), symbols: list[str] = (),
             manufacturer: str | None = None) -> DatasheetEntry:
        with self._lock:
            index = self._load()
            entry = next((e for e in index.entries if e.id == entry_id), None)
            if entry is None:
                raise DatasheetError("DATASHEET_NOT_FOUND", f"데이터시트 '{entry_id}'가 없습니다.", status=404)
            if manufacturer and not entry.manufacturer:
                entry.manufacturer = manufacturer
            self._claim(index, entry, part_numbers, symbols)
            text, _, _ = read_pdf_text((self.dir / entry.file).read_bytes())
            entry.identity = identity_check(text, [*entry.part_numbers, *(s.split(":", 1)[-1] for s in entry.symbols)])
            self._save(index)
            return entry

    @staticmethod
    def _claim(index: LibraryIndex, entry: DatasheetEntry, part_numbers, symbols) -> None:
        """A part number or symbol points at exactly one datasheet: move it to ``entry``.
        Generic symbols (Device:R, connectors) are never linked: they stand for any maker's part."""
        mpns = {normalize_part(p): p for p in part_numbers if normalize_part(p)}
        syms = {s for s in symbols if s and symbol_part_name(s)}
        for other in index.entries:
            if other is entry:
                continue
            other.part_numbers = [p for p in other.part_numbers if normalize_part(p) not in mpns]
            other.symbols = [s for s in other.symbols if s not in syms]
        known = {normalize_part(p) for p in entry.part_numbers}
        entry.part_numbers += [p for key, p in mpns.items() if key not in known]
        entry.symbols += sorted(syms - set(entry.symbols))

    def delete(self, entry_id: str) -> None:
        with self._lock:
            index = self._load()
            entry = next((e for e in index.entries if e.id == entry_id), None)
            if entry is None:
                raise DatasheetError("DATASHEET_NOT_FOUND", f"데이터시트 '{entry_id}'가 없습니다.", status=404)
            index.entries = [e for e in index.entries if e.id != entry_id]
            self._save(index)
            for suffix in (".pdf", ".summary.md", ".facts.json"):
                path = self.dir / f"{entry_id}{suffix}"
                if path.exists():
                    path.unlink()

    # --- finding -----------------------------------------------------------------------------

    def find(self, component: Component, symbol_url: str | None = None) -> DatasheetEntry | None:
        """Datasheet for a component: by exact (normalised) part number, then by symbol, then by link."""
        entries = self.entries()
        mpn = normalize_part(component.manufacturer_part_number)
        if mpn:
            for e in entries:
                if mpn in {normalize_part(p) for p in e.part_numbers}:
                    return e
        for e in entries:
            if component.symbol_id in e.symbols:
                return e
        if symbol_url:
            for e in entries:
                if symbol_url in e.urls:
                    return e
        return None

    # --- summary -----------------------------------------------------------------------------

    def save_summary(self, entry_id: str, *, markdown: str, facts: DatasheetFacts, origin: Literal["markdown", "ai"],
                     source_name: str, warnings: int, model: str | None = None) -> DatasheetEntry:
        with self._lock:
            index = self._load()
            entry = next((e for e in index.entries if e.id == entry_id), None)
            if entry is None:
                raise DatasheetError("DATASHEET_NOT_FOUND", f"데이터시트 '{entry_id}'가 없습니다.", status=404)
            atomic_write(self.dir / f"{entry_id}.summary.md", markdown)
            atomic_write(self.dir / f"{entry_id}.facts.json", facts.model_dump_json(indent=2) + "\n")
            entry.summary = SummaryInfo(origin=origin, source_name=PurePath(source_name).name,
                                        imported_at=now_iso(), warnings=warnings, model=model)
            if facts.manufacturer and not entry.manufacturer:
                entry.manufacturer = facts.manufacturer
            self._save(index)
            return entry

    def set_reviewed(self, entry_id: str, reviewed: bool) -> DatasheetEntry:
        with self._lock:
            index = self._load()
            entry = next((e for e in index.entries if e.id == entry_id), None)
            if entry is None or entry.summary is None:
                raise DatasheetError("NO_SUMMARY", "요약이 없는 데이터시트입니다.", status=409)
            entry.summary.reviewed = reviewed
            self._save(index)
            return entry

    def facts(self, entry: DatasheetEntry) -> DatasheetFacts | None:
        path = self.dir / f"{entry.id}.facts.json"
        if entry.summary is None or not path.is_file():
            return None
        try:
            return DatasheetFacts.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def summary_markdown(self, entry: DatasheetEntry) -> str | None:
        path = self.dir / f"{entry.id}.summary.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    # --- found links ---------------------------------------------------------------------------

    def _book(self) -> CandidateBook:
        path = self.dir / "candidates.json"
        if not path.is_file():
            return CandidateBook()
        try:
            return CandidateBook.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            return CandidateBook()

    def searches(self) -> dict[str, PartSearch]:
        return self._book().parts

    def save_search(self, key: str, search: PartSearch) -> None:
        with self._lock:
            book = self._book()
            book.parts[key] = search
            atomic_write(self.dir / "candidates.json", book.model_dump_json(indent=2) + "\n")

    def save_ai_run(self, record: dict) -> Path:
        path = self.dir / "ai_runs" / f"{record['id']}.json"
        atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return path


# --- downloading -----------------------------------------------------------------------------------

class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def __init__(self, https_only: bool):
        super().__init__()
        self.https_only = https_only

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.https_only and newurl.startswith("http://"):
            newurl = "https://" + newurl[len("http://"):]
        if not re.match(r"^https?://", newurl):
            raise DatasheetError("DATASHEET_BAD_REDIRECT", f"허용하지 않는 주소로 이동하려 했습니다: {newurl[:80]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_pdf(url: str, *, max_bytes: int = MAX_DATASHEET_BYTES, timeout: float = 60.0, https_only: bool = True,
              user_agent: str = "AI-Circuit-Studio (datasheet download)") -> tuple[bytes, str]:
    """Download a datasheet PDF. ``http://`` links are fetched over HTTPS when ``https_only``."""
    url = url.strip()
    if not re.match(r"^https?://[^\s/]+", url):
        raise DatasheetError("DATASHEET_BAD_URL", f"웹 주소가 아닙니다: {url[:80]}")
    if https_only and url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    opener = urllib.request.build_opener(_Redirects(https_only))
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/pdf,*/*;q=0.5"})
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            data = response.read(max_bytes + 1)
    except DatasheetError:
        raise
    except urllib.error.HTTPError as exc:
        hint = ("링크가 바뀌었거나 사이트가 자동 다운로드를 막았을 수 있습니다. "
                "브라우저로 제조사 사이트에서 받은 뒤 'PDF 올리기'로 넣으세요.")
        raise DatasheetError("DATASHEET_HTTP_ERROR", f"다운로드 실패 (HTTP {exc.code}): {url}", hint) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise DatasheetError("DATASHEET_UNREACHABLE", f"사이트에 연결하지 못했습니다 ({reason}): {url}",
                             "인터넷 연결이나 회사 방화벽·프록시 설정을 확인하세요. 안 되면 PDF를 직접 올리세요.") from exc
    if len(data) > max_bytes:
        raise DatasheetError("DATASHEET_TOO_LARGE", f"PDF가 너무 큽니다 (최대 {max_bytes // MB} MB): {url}", status=413)
    if b"%PDF" not in data[:1024]:
        raise DatasheetError("DATASHEET_NOT_PDF", f"링크가 PDF가 아닙니다: {final_url}",
                             "제품 페이지로 연결되는 링크일 수 있습니다. 브라우저로 열어 PDF를 받은 뒤 'PDF 올리기'로 넣으세요.")
    return data, final_url
