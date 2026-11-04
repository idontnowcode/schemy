"""Read "데이터시트 요약 MD v0.1" (docs/prompts/datasheet-summary.md) into :class:`DatasheetFacts`.

The Markdown is written by a person, Copilot or the optional AI from a datasheet PDF. It is data,
never instructions: every row is checked, numbers are parsed strictly, rows without a page number
are flagged, and anything that cannot be read is reported instead of guessed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .datasheets import DatasheetFacts, DsExternalPart, DsNote, DsPin, DsRating
from .md_tables import ImportIssue, Table, first_int, parse_table, read_sections
from .parts import category_from_text

FORMAT_NAME = "데이터시트 요약 MD v0.1"

SECTION_COLUMNS: dict[int, tuple[str, ...]] = {
    1: ("항목", "값"),
    2: ("패키지", "핀 번호", "핀 이름", "종류", "설명", "페이지"),
    3: ("항목", "관련 핀", "최소", "최대", "단위", "조건", "페이지"),
    4: ("항목", "관련 핀", "최소", "일반", "최대", "단위", "조건", "페이지"),
    5: ("구분", "부품", "값", "수량", "연결", "설명", "페이지"),
    6: ("번호", "내용", "페이지"),
    7: ("번호", "페이지", "내용"),
}
REQUIRED_SECTIONS = (2,)
SECTION_NAMES = {1: "정보", 2: "핀 표", 3: "절대 최대 정격", 4: "권장 동작 조건", 5: "외부 부품", 6: "응용 회로 메모",
                 7: "불확실 항목"}

_NUMBER = re.compile(r"^[+\-]?\d+(?:\.\d+)?$")


@dataclass
class DatasheetMdResult:
    facts: DatasheetFacts | None
    issues: list[ImportIssue]
    stats: dict

    @property
    def ok(self) -> bool:
        return self.facts is not None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "facts": self.facts.model_dump(mode="json") if self.facts else None,
                "issues": [i.to_dict() for i in self.issues], "stats": self.stats}


def _number(text: str) -> float | None:
    s = unicodedata.normalize("NFKC", text or "").replace("−", "-").replace("–", "-").replace(",", "").strip()
    return float(s) if _NUMBER.match(s) else None


def _page(table: Table, row: list[str]) -> int | None:
    m = re.search(r"\d+", table.get(row, "페이지"))
    return int(m.group()) if m else None


def _pins_list(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,，;、]", text or "") if p.strip()]


def _endpoints(text: str) -> list[str]:
    """'VCC ~ GND' (the format's separator), 'VCC ↔ GND', 'VCC - GND' or 'VCC-GND' -> ['VCC', 'GND'].
    Pin names may contain '-' (e.g. 'IN-'), so a bare '-' only splits when it is the only one."""
    text = (text or "").strip()
    if re.search(r"[~↔]", text):
        parts = re.split(r"\s*[~↔]\s*", text)
    elif " - " in text:
        parts = text.split(" - ")
    elif text.count("-") == 1 and not text.startswith("-") and not text.endswith("-"):
        parts = text.split("-")
    else:
        parts = [text]
    return [p.strip() for p in parts if p.strip()]


def import_datasheet_markdown(text: str, *, source_name: str = "datasheet.md") -> DatasheetMdResult:
    issues: list[ImportIssue] = []
    title, raw = read_sections(text, r"^#\s+데이터시트\s*[:：]\s*(.+)$")
    tables: dict[int, Table | None] = {}
    for section, columns in SECTION_COLUMNS.items():
        if section not in raw:
            continue
        table, problems = parse_table(raw[section][1], columns)
        if problems and problems.missing:
            issues.append(ImportIssue("error" if section in REQUIRED_SECTIONS else "warning", "MISSING_COLUMNS",
                                      f"{section}장({SECTION_NAMES[section]}) 표에 열이 없습니다: "
                                      f"{', '.join(problems.missing)}", line=problems.line))
        if problems and problems.extra:
            issues.append(ImportIssue("warning", "EXTRA_COLUMNS", f"{section}장 표의 알 수 없는 열은 무시합니다: "
                                      f"{', '.join(problems.extra)}", line=problems.line))
        tables[section] = table
    for section in REQUIRED_SECTIONS:
        if tables.get(section) is None:
            issues.append(ImportIssue("error", "MISSING_SECTION", f"{section}장({SECTION_NAMES[section]}) 표를 찾지 "
                                      f"못했습니다. '## {section}. {SECTION_NAMES[section]}' 아래에 표가 있어야 합니다."))
    if any(i.severity == "error" for i in issues):
        return DatasheetMdResult(None, issues, {})

    info: dict[str, str] = {}
    if tables.get(1):
        info = {tables[1].get(r, "항목"): tables[1].get(r, "값") for _, r in tables[1].rows}
    else:
        issues.append(ImportIssue("warning", "NO_INFO", "1장(정보) 표가 없어 제조사·품번을 알 수 없습니다."))
    if info.get("형식") and FORMAT_NAME not in info["형식"]:
        issues.append(ImportIssue("warning", "FORMAT_VERSION", f"형식이 '{info['형식']}'입니다. '{FORMAT_NAME}'로 읽습니다."))
    part_numbers = _pins_list(info.get("품번", ""))
    if not part_numbers and title:
        part_numbers = [title.split()[-1]]

    missing_pages = 0

    # 2. pins
    pins: list[DsPin] = []
    seen: dict[tuple[str, str], tuple[str, int]] = {}
    t2 = tables[2]
    for line, row in t2.rows:
        package = t2.get(row, "패키지") or "?"
        number, name = t2.get(row, "핀 번호"), t2.get(row, "핀 이름")
        if not number and not name:
            continue
        if number in ("", "?"):
            issues.append(ImportIssue("warning", "PIN_NUMBER_UNKNOWN",
                                      f"핀 '{name or '?'}'({package})의 번호가 없어 건너뜁니다.", line=line))
            continue
        key = (package, number)
        if key in seen:
            if seen[key][0] != name:
                issues.append(ImportIssue("error", "PIN_DUPLICATE",
                                          f"{package} 핀 {number}이(가) '{seen[key][0]}'(행 {seen[key][1]})와 "
                                          f"'{name}'로 두 번 나옵니다.", line=line))
            continue
        seen[key] = (name, line)
        page = _page(t2, row)
        missing_pages += page is None
        pins.append(DsPin(package=package, number=number, name=name or "", kind=t2.get(row, "종류") or None,
                          description=t2.get(row, "설명") or None, page=page))
    if not pins:
        issues.append(ImportIssue("error", "NO_PINS", "2장(핀 표)에서 핀을 하나도 읽지 못했습니다."))

    # 3, 4. ratings
    def ratings(section: int) -> tuple[list[DsRating], int]:
        table = tables.get(section)
        if table is None:
            return [], 0
        out, no_page = [], 0
        for line, row in table.rows:
            parameter = table.get(row, "항목")
            if not parameter:
                continue
            raw_min, raw_max = table.get(row, "최소"), table.get(row, "최대")
            lo, hi = _number(raw_min), _number(raw_max)
            if raw_max.startswith("±") and _number(raw_max[1:]) is not None:
                hi = _number(raw_max[1:])
                lo = -hi if hi is not None else None
            typ = _number(table.get(row, "일반")) if section == 4 else None
            for label, raw_value, value in (("최소", raw_min, lo), ("최대", raw_max, hi)):
                if raw_value and raw_value not in ("?",) and value is None and not raw_value.startswith("±"):
                    issues.append(ImportIssue("warning", "NUMBER_UNREADABLE",
                                              f"{section}장 '{parameter}'의 {label} '{raw_value}'을(를) 숫자로 읽지 "
                                              "못했습니다.", line=line))
            if lo is None and hi is None and typ is None:
                continue
            page = _page(table, row)
            no_page += page is None
            out.append(DsRating(parameter=parameter, pins=_pins_list(table.get(row, "관련 핀")), min=lo, typ=typ,
                                max=hi, unit=table.get(row, "단위"), condition=table.get(row, "조건") or None,
                                page=page))
        return out, no_page

    (abs_max, n3), (recommended, n4) = ratings(3), ratings(4)
    missing_pages += n3 + n4

    # 5. external parts
    external: list[DsExternalPart] = []
    if (t5 := tables.get(5)) is not None:
        for line, row in t5.rows:
            label = t5.get(row, "부품")
            if not label:
                continue
            req_text = t5.get(row, "구분")
            requirement = "required" if req_text.startswith(("필수", "required", "Required")) else "recommended"
            if not req_text.startswith(("필수", "권장", "required", "recommended", "Required", "Recommended")):
                issues.append(ImportIssue("warning", "REQUIREMENT_UNKNOWN",
                                          f"5장 '{label}'의 구분 '{req_text}'을(를) '권장'으로 읽습니다.", line=line))
            endpoints = _endpoints(t5.get(row, "연결"))
            if len(endpoints) != 2:
                issues.append(ImportIssue("warning", "ENDPOINTS_UNREADABLE",
                                          f"5장 '{label}'의 연결 '{t5.get(row, '연결')}'은(는) '핀-핀' 형식이 아니어서 "
                                          "자동 확인을 하지 않습니다.", line=line))
            page = _page(t5, row)
            missing_pages += page is None
            external.append(DsExternalPart(
                requirement=requirement, kind=category_from_text(label), kind_label=label,
                value=t5.get(row, "값") or None, quantity=first_int(t5.get(row, "수량")),
                endpoints=endpoints if len(endpoints) == 2 else [], description=t5.get(row, "설명") or None, page=page))

    # 6. notes, 7. uncertain
    notes: list[DsNote] = []
    if (t6 := tables.get(6)) is not None:
        for _, row in t6.rows:
            if content := t6.get(row, "내용"):
                notes.append(DsNote(text=content, page=_page(t6, row)))
    uncertain: list[dict] = []
    if (t7 := tables.get(7)) is not None:
        for line, row in t7.rows:
            item = {c: t7.get(row, c) for c in SECTION_COLUMNS[7]}
            if item["내용"]:
                uncertain.append({**item, "line": line})

    if missing_pages:
        issues.append(ImportIssue("warning", "PAGE_MISSING",
                                  f"페이지 번호가 없는 행이 {missing_pages}개 있습니다. 원문과 대조하기 어렵습니다."))
    if uncertain:
        issues.append(ImportIssue("warning", "UNCERTAIN_ITEMS", f"불확실 항목이 {len(uncertain)}개 있습니다."))
    if any(i.severity == "error" for i in issues):
        return DatasheetMdResult(None, issues, {})

    facts = DatasheetFacts(
        format=FORMAT_NAME, manufacturer=info.get("제조사") or None, part_numbers=part_numbers,
        document=info.get("문서") or None, source_file=info.get("원본 파일") or source_name,
        page_count=first_int(info.get("페이지 수", "")), pins=pins, abs_max=abs_max, recommended=recommended,
        external_parts=external, notes=notes, uncertain=uncertain)
    stats = {"packages": len(facts.packages()), "pins": len(pins), "abs_max": len(abs_max),
             "recommended": len(recommended), "external_parts": len(external), "notes": len(notes),
             "uncertain": len(uncertain), "warnings": sum(1 for i in issues if i.severity == "warning")}
    return DatasheetMdResult(facts, issues, stats)
