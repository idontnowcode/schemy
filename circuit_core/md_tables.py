"""Reading the numbered-section Markdown tables that AI assistants write for us.

Shared by "회로도 MD" (schematic transcription) and "데이터시트 요약 MD" (datasheet summary).
Both formats are a ``# <kind>: <title>`` heading followed by ``## <n>. <name>`` sections that
each hold one pipe table. Nothing here interprets the content; it only finds sections,
splits cells and maps header names to column positions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

EMPTY = {"", "—", "–", "n/a"}


@dataclass
class ImportIssue:
    """One finding while reading an AI-written Markdown file.

    severity: ``error`` (nothing is produced), ``warning`` (produced, but check before use),
    ``info`` (a recorded decision)."""

    severity: str
    code: str
    message: str
    references: list[str] = field(default_factory=list)
    line: int | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def clean(cell: str) -> str:
    text = unicodedata.normalize("NFKC", cell).replace("\\|", "/").strip()
    text = re.sub(r"^`(.*)`$", r"\1", text).strip()
    text = re.sub(r"^\*\*(.*)\*\*$", r"\1", text).strip()
    return "" if text.lower() in EMPTY else text


def split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [clean(c) for c in re.split(r"(?<!\\)\|", body)]


def read_sections(text: str, title_pattern: str) -> tuple[str | None, dict[int, tuple[int, list[tuple[int, str]]]]]:
    """Title (first group of ``title_pattern``) and, per numbered section,
    (heading line number, [(line number, text), ...])."""
    title = None
    sections: dict[int, tuple[int, list[tuple[int, str]]]] = {}
    current: int | None = None
    title_re = re.compile(title_pattern)
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if title is None and (m := title_re.match(line)):
            title = m.group(1).strip()
            continue
        if m := re.match(r"^##\s*(\d+)\s*[.)]", line):
            current = int(m.group(1))
            sections[current] = (number, [])
            continue
        if line.startswith("#"):
            current = None
            continue
        if current is not None:
            sections[current][1].append((number, line))
    return title, sections


@dataclass
class Table:
    columns: dict[str, int]
    rows: list[tuple[int, list[str]]]
    keep_dash: frozenset[str] = frozenset()
    """Columns where a lone "-" is a value (e.g. the minus terminal), not "empty"."""

    def get(self, row: list[str], column: str) -> str:
        index = self.columns.get(column)
        if index is None or index >= len(row):
            return ""
        value = row[index]
        return "" if value == "-" and column not in self.keep_dash else value


@dataclass
class HeaderProblems:
    line: int
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)


def parse_table(lines: list[tuple[int, str]], expected: tuple[str, ...], optional: tuple[str, ...] = (),
                keep_dash: tuple[str, ...] = ()) -> tuple[Table | None, HeaderProblems | None]:
    """The first pipe table in ``lines``. Missing expected columns and unknown columns are reported;
    optional columns are read when present and never reported."""
    table_lines = [(n, line) for n, line in lines if line.strip().startswith("|")]
    if not table_lines:
        return None, None
    header_line, header = table_lines[0][0], split_row(table_lines[0][1])
    known = (*expected, *optional)
    columns: dict[str, int] = {}
    for i, name in enumerate(header):
        if name in known and name not in columns:
            columns[name] = i
    problems = HeaderProblems(header_line,
                              missing=[c for c in expected if c not in columns],
                              extra=[h for h in header if h and h not in known])
    rows = []
    for n, line in table_lines[1:]:
        cells = split_row(line)
        if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) or c == "" for c in cells):
            continue  # separator or empty row
        rows.append((n, cells))
    has_problems = problems.missing or problems.extra
    return Table(columns, rows, frozenset(keep_dash)), (problems if has_problems else None)


def first_int(text: str) -> int | None:
    m = re.match(r"^\d+", text or "")
    return int(m.group()) if m else None
