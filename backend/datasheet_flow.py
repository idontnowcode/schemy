"""Datasheets as a step of the AI design flow.

Once the parts are chosen, each part that names a specific maker's part gets its datasheet: found (KiCad symbol
link, the maker's address rule, distributor answers), downloaded, checked to be the right document, and summarized
by the AI into the summary MD the datasheet checks read. The summaries then go into the pin-connection prompt, the
proposal's datasheet checks and the reviewer's evidence. Nothing here changes a circuit.

ADR-005 allowed downloads and AI summaries only after the person confirmed them. Here the confirmation is the
option on the design request (step 3 of the step screens says what is downloaded and what is sent); without it
only summaries already in the library are used.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from ai_engine import AiError
from ai_engine.tasks import summarize_datasheet
from circuit_core.datasheet_md import import_datasheet_markdown
from circuit_core.datasheet_sources import (
    DatasheetCandidate,
    maker_candidate,
    part_key,
    search_term,
    symbol_candidate,
    url_part_name,
)
from circuit_core.datasheets import identity, symbol_part_name
from circuit_core.models import Component
from circuit_core.parts import category_of

from .ai_access import run_record
from .datasheets import DatasheetEntry, DatasheetError, DatasheetLibrary, PartSearch
from .store import now_iso, sha256_bytes

AUTO_CATEGORIES = {None, "ic", "transistor"}
"""Parts whose datasheet is fetched without being asked: ICs, transistors and specific parts of no known category.
Generic passives and connectors are left to the datasheet tab."""
MAX_NEW_SUMMARIES = 6
"""AI summaries one design job may make (each is an AI call with a PDF)."""
MAX_SUMMARY_PAGES = 24
"""Pages of one datasheet sent to the AI. A longer one is cut to the pages the checks need."""

Step = Callable[[str], None]


def url_filename(url: str) -> str:
    name = PurePosixPath(unquote(urlparse(url).path)).name
    return name if name.lower().endswith(".pdf") else (name or "datasheet") + ".pdf"


def candidates_for(comp: Component, url: str | None, searches: dict) -> list[DatasheetCandidate]:
    """Where a part's datasheet may be: distributor answers (exact part number) first, then the KiCad link, then
    the maker's address rule."""
    out: list[DatasheetCandidate] = []
    key = part_key(comp)
    found = searches.get(key) if key else None
    if found:
        out += sorted(found.candidates, key=lambda c: c.source != "digikey")
    for extra in (symbol_candidate(comp, url), maker_candidate(comp, url)):
        if extra and all(extra.urls[0] not in c.urls for c in out):
            out.append(extra)
    return out


def download_candidate(cand: dict, comps: list[Component], *, library: DatasheetLibrary,
                       fetcher) -> tuple[dict | None, list[dict]]:
    """Try one candidate's addresses in order. (what was stored or None, the failed tries)."""
    tried = []
    for url in cand["urls"]:
        try:
            data, final_url = fetcher(url)
            entry, created = library.add(
                data, original_name=url_filename(final_url or url), source=cand["source"], url=url,
                final_url=final_url,
                manufacturer=next((c.manufacturer for c in comps if c.manufacturer), None) or cand.get("manufacturer"),
                part_numbers=[c.manufacturer_part_number for c in comps if c.manufacturer_part_number],
                symbols=sorted({c.symbol_id for c in comps}),
                expect=[url_part_name(url)] if cand["source"] == "maker_site" else ())
            return {"entry_id": entry.id, "created": created, "identity": entry.identity.get("status"),
                    "url": url}, tried
        except DatasheetError as exc:
            tried.append({"url": url, "error_code": exc.code, "message": exc.message, "hint": exc.hint})
    return None, tried


# --- which pages to send ---------------------------------------------------------------------------------------

_PAGE_HINTS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (6, ("absolute maximum ratings",)),
    (6, ("recommended operating conditions", "recommended operating")),
    (6, ("pin configuration", "pin functions", "pin description", "pinout", "terminal functions", "pin assignment",
         "pin definitions")),
    (4, ("typical application", "application circuit", "application information", "applications information",
         "typical operating circuit")),
    (2, ("electrical characteristics",)),
    (1, ("ordering information", "package option", "layout guidelines", "layout example")),
)


def select_pages(data: bytes, limit: int = MAX_SUMMARY_PAGES) -> tuple[bytes, list[int] | None, int | None]:
    """(the PDF to send, the original page numbers it holds when it was cut, the page count).

    A datasheet within ``limit`` pages goes whole. A longer one (an MCU's runs to hundreds) is cut to the first two
    pages and the pages that name what the checks read: pin tables, absolute maximum ratings, recommended operating
    conditions, the typical application. A page that opens with such a heading counts double, and the page after a
    table page comes along (tables run on)."""
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        count = len(reader.pages)
    except Exception:  # noqa: BLE001 - pypdf raises many types; the whole file is sent and the AI call decides
        return data, None, None
    if count <= limit:
        return data, None, count
    scores = []
    for page in reader.pages:
        try:
            text = (page.extract_text() or "").lower()
        except Exception:  # noqa: BLE001 - one bad page must not hide the others
            text = ""
        head = text[:600]
        scores.append(sum(w * (2 if any(k in head for k in keys) else 1)
                          for w, keys in _PAGE_HINTS if any(k in text for k in keys)))
    keep = {0, 1}
    for i in sorted((i for i in range(count) if scores[i] > 0), key=lambda i: (-scores[i], i)):
        if len(keep) >= limit:
            break
        keep.add(i)
        if scores[i] >= 6 and i + 1 < count and len(keep) < limit:
            keep.add(i + 1)
    if len(keep) <= 2:  # no readable text (a scan): the first pages are the best guess
        keep |= set(range(min(limit, count)))
    picked = sorted(keep)
    try:
        writer = pypdf.PdfWriter()
        for i in picked:
            writer.add_page(reader.pages[i])
        out = io.BytesIO()
        writer.write(out)
    except Exception:  # noqa: BLE001
        return data, None, count
    return out.getvalue(), [i + 1 for i in picked], count


# --- the step --------------------------------------------------------------------------------------------------

class DatasheetPrep:
    """One design job's datasheet step. ``run`` may be called more than once in a job (after the parts plan, again
    for a revision's new parts): a part is handled once, and the job's summary budget is shared."""

    def __init__(self, *, library: DatasheetLibrary, symbol_url: Callable[[str], str | None], fetcher,
                 searchers: list, provider, cfg, job_id: str, step: Step, fetch: bool):
        self.library, self.symbol_url, self.fetcher, self.searchers = library, symbol_url, fetcher, searchers
        self.provider, self.cfg, self.job_id, self.step, self.fetch = provider, cfg, job_id, step, fetch
        self.budget = MAX_NEW_SUMMARIES
        self.rows: dict[str, dict] = {}
        self.usage: list[dict] = []

    # what to look at

    def _groups(self, components: Iterable[Component]) -> dict[str, list[Component]]:
        groups: dict[str, list[Component]] = {}
        for comp in components:
            url = self.symbol_url(comp.symbol_id)
            if identity(comp, url) == "generic":
                continue
            if category_of(comp.symbol_id, comp.reference) not in AUTO_CATEGORIES:
                continue
            groups.setdefault(part_key(comp) or comp.symbol_id, []).append(comp)
        return groups

    # one part

    def _search(self, comp: Component) -> None:
        key, term = part_key(comp), search_term(comp)
        if not self.searchers or not key or not term or key in self.library.searches():
            return
        found, errors = [], []
        for client in self.searchers:
            try:
                found += client.search(term)
            except DatasheetError as exc:
                errors.append({"source": client.name, "error_code": exc.code, "message": exc.message, "hint": exc.hint})
        self.library.save_search(key, PartSearch(term=term, searched_at=now_iso(), candidates=found, errors=errors))

    def _download(self, row: dict, comps: list[Component], url: str | None) -> DatasheetEntry | None:
        self._search(comps[0])
        cands = candidates_for(comps[0], url, self.library.searches())
        if not cands:
            row.update(download="no_source",
                       message="받을 곳을 찾지 못했습니다(KiCad 심볼 링크·제조사 주소 규칙·유통사 검색 결과 없음). "
                               "데이터시트 탭에서 PDF를 올리세요.")
            return None
        last: dict = {}
        for cand in cands:
            c = cand.model_dump(mode="json")
            host = urlparse(c["urls"][0]).hostname or ""
            self.step(f"데이터시트 받는 중: {row['part']} ({host})")
            got, tried = download_candidate(c, comps, library=self.library, fetcher=self.fetcher)
            if got:
                row.update(download="downloaded", source=c["label"], url=got["url"])
                return self.library.get(got["entry_id"])
            last = {**(tried[-1] if tried else {}), "product_url": c.get("product_url")}
        row.update(download="failed", product_url=last.get("product_url") or last.get("url"),
                   message=" ".join(x for x in (last.get("message"), last.get("hint")) if x)
                   or "받지 못했습니다.")
        return None

    def _summarize(self, row: dict, entry: DatasheetEntry) -> None:
        if self.provider is None:
            row.update(summary="skipped", message="AI 연결이 없어 요약하지 않았습니다.")
            return
        if entry.identity.get("status") == "not_found":
            row.update(summary="skipped",
                       message="받은 문서에서 이 품번을 찾지 못해 요약하지 않았습니다. 맞는 문서인지 확인하세요.")
            return
        if self.budget <= 0:
            row.update(summary="skipped",
                       message=f"한 번에 {MAX_NEW_SUMMARIES}개까지만 요약합니다. 나머지는 데이터시트 탭에서 "
                               "요약하세요.")
            return
        self.budget -= 1
        pdf = self.library.pdf_path(entry).read_bytes()
        cut, pages, total = select_pages(pdf)
        self.step(f"AI 요약 중: {row['part']} ({entry.original_name}"
                  + (f", {total}쪽 중 {len(pages)}쪽" if pages else f", {total}쪽" if total else "") + ")")
        record = run_record(self.job_id, "datasheet_summary", self.cfg, {
            "datasheet": entry.id, "file": entry.original_name, "bytes": len(cut), "sha256": entry.sha256,
            "pages": total, "pages_sent": pages, "auto": True})
        try:
            summary = summarize_datasheet(self.provider, cut, entry.original_name,
                                          max_output_tokens=self.cfg.max_output_tokens, pages=pages, total=total)
        except AiError as exc:
            record.update(status="failed", error={"code": exc.code, "message": exc.message})
            self.library.save_ai_run(record)
            row.update(summary="failed", message=f"AI 요약 실패: {exc.message}")
            if exc.code in ("AI_AUTH_FAILED", "AI_BILLING", "AI_QUOTA", "AI_NO_KEY"):
                self.budget = 0  # the next one would fail the same way
            return
        self.usage.append(summary.result.usage)
        result = import_datasheet_markdown(summary.markdown, source_name=f"{entry.id}.ai.md")
        record.update(status="succeeded", usage=summary.result.usage, finish_reason=summary.result.finish_reason,
                      warnings=summary.warnings, output={
                          "markdown_sha256": sha256_bytes(summary.markdown.encode("utf-8")),
                          "import_ok": result.ok, "issues": len(result.issues)})
        self.library.save_ai_run(record)
        if not result.ok:
            first = next((i.message for i in result.issues if i.severity == "error"), "형식 오류")
            row.update(summary="failed", message=f"AI 요약을 읽지 못했습니다: {first}")
            return
        self.library.save_summary(entry.id, markdown=summary.markdown, facts=result.facts, origin="ai",
                                  source_name=f"{entry.id}.ai.md", warnings=result.stats.get("warnings", 0),
                                  model=summary.result.model)
        row.update(summary="made", pages_sent=pages, pages=total)

    # the step

    def run(self, components: Iterable[Component]) -> list[dict]:
        """Handle the parts not yet handled in this job. Returns their rows (empty when there was nothing new)."""
        new = {k: v for k, v in self._groups(components).items() if k not in self.rows}
        done = []
        for key, comps in new.items():
            rep, url = comps[0], self.symbol_url(comps[0].symbol_id)
            row = {"key": key, "part": rep.manufacturer_part_number or symbol_part_name(rep.symbol_id) or rep.value,
                   "references": sorted(c.reference for c in comps), "datasheet": None, "download": None,
                   "summary": None, "message": None}
            entry = self.library.find(rep, url)
            if entry is not None:
                row["download"] = "had"
            elif self.fetch:
                entry = self._download(row, comps, url)
            else:
                row.update(download="off", message="자동 받기가 꺼져 있어 받지 않았습니다.")
            if entry is not None:
                row.update(datasheet=entry.id, identity=entry.identity.get("status"))
                if self.library.facts(entry) is not None:
                    row["summary"] = "had"
                    row["reviewed"] = bool(entry.summary and entry.summary.reviewed)
                elif self.fetch:
                    self._summarize(row, entry)
                else:
                    row.update(summary="off", message="자동 요약이 꺼져 있어 요약하지 않았습니다.")
            self.rows[key] = row
            done.append(row)
        return done

    def report(self) -> dict:
        rows = list(self.rows.values())
        return {"fetch": self.fetch, "parts": rows,
                "ready": sum(r["summary"] in ("had", "made") for r in rows),
                "downloaded": sum(r["download"] == "downloaded" for r in rows),
                "summarized": sum(r["summary"] == "made" for r in rows),
                "missing": sum(r["summary"] not in ("had", "made") for r in rows)}


def report_text(report: dict) -> str:
    """One line for the progress list, e.g. 「데이터시트: 대상 4종 · 요약 있음 3 (새로 받음 2, 새로 요약 2) · 없음 1
    (U2 MAX485)」."""
    rows = report["parts"]
    if not rows:
        return "데이터시트: 받을 대상 부품이 없습니다 (IC·트랜지스터처럼 품번이 정해진 부품만 받습니다)"
    new = [x for x in (f"새로 받음 {report['downloaded']}" if report["downloaded"] else "",
                       f"새로 요약 {report['summarized']}" if report["summarized"] else "") if x]
    missing = [r for r in rows if r["summary"] not in ("had", "made")]
    return (f"데이터시트: 대상 {len(rows)}종 · 요약 있음 {report['ready']}" + (f" ({', '.join(new)})" if new else "")
            + (f" · 없음 {len(missing)} (" + ", ".join(f"{r['references'][0]} {r['part']}" for r in missing[:3])
               + (" …" if len(missing) > 3 else "") + ")" if missing else "")
            + (" — 자동 받기가 꺼져 있습니다" if missing and not report["fetch"] else ""))
