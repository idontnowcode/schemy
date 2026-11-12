"""AI tasks. Both produce proposals only; nothing here writes project files.

- ``transcribe_pdf``: PDF schematic -> "회로도 MD v0.1" text (then imported like any Markdown).
- ``suggest_substitutes``: for BOM lines without a stocked match, rank inventory parts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from circuit_core.inventory import Inventory, LineMatch, MatchStatus
from circuit_core.parts import CATEGORY_LABELS, category_of

from .providers import AiError, Attachment, LlmProvider, LlmResult

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MAX_CANDIDATES_PER_LINE = 40


def pdf_prompt() -> str:
    return (PROMPT_DIR / "pdf_schematic_to_md.txt").read_text(encoding="utf-8")


@dataclass
class Transcription:
    markdown: str
    result: LlmResult
    warnings: list[str] = field(default_factory=list)


def _markdown_from_pdf(provider: LlmProvider, pdf: bytes, filename: str, *, prompt: str, marker: str, what: str,
                       fallback: str, max_output_tokens: int) -> Transcription:
    if not pdf.startswith(b"%PDF"):
        raise AiError("NOT_A_PDF", f"'{filename}'은(는) PDF 파일이 아닙니다.")
    result = provider.generate(prompt=prompt, attachments=[Attachment(filename, "application/pdf", pdf)],
                               max_output_tokens=max_output_tokens, temperature=0.0)
    text = result.text.strip()
    fenced = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    warnings = []
    if result.finish_reason and result.finish_reason not in ("STOP", "FinishReason.STOP"):
        warnings.append(f"AI 응답이 끝까지 오지 않았을 수 있습니다 (finish_reason={result.finish_reason}). {fallback}")
    if marker not in text:
        raise AiError("AI_BAD_OUTPUT", f"AI 응답이 '{what}' 형식이 아닙니다.",
                      "다시 시도하거나, 코파일럿에서 같은 프롬프트로 만든 Markdown을 가져오세요.")
    return Transcription(markdown=text + "\n", result=result, warnings=warnings)


def transcribe_pdf(provider: LlmProvider, pdf: bytes, filename: str, *, max_output_tokens: int = 32768) -> Transcription:
    return _markdown_from_pdf(provider, pdf, filename, prompt=pdf_prompt(), marker="# 회로도", what="회로도 MD",
                              fallback="회로가 크면 페이지를 나눠 변환하세요.", max_output_tokens=max_output_tokens)


def datasheet_prompt() -> str:
    return (PROMPT_DIR / "datasheet_summary.txt").read_text(encoding="utf-8")


def summarize_datasheet(provider: LlmProvider, pdf: bytes, filename: str, *,
                        max_output_tokens: int = 32768, pages: list[int] | None = None,
                        total: int | None = None) -> Transcription:
    """Datasheet PDF -> "데이터시트 요약 MD". The datasheet is untrusted input; the reply is only ever
    parsed as tables by circuit_core.datasheet_md, never followed.

    ``pages``: the original page numbers when ``pdf`` holds only some pages of a long datasheet, so the summary
    cites pages a person can open in the full file."""
    prompt = datasheet_prompt()
    if pages:
        prompt += ("\n\n[쪽 안내]\n첨부 PDF는 원본" + (f" {total}쪽" if total else "") + " 중 다음 쪽만 순서대로 모은 것입니다: "
                   + ", ".join(str(n) for n in pages) + ".\n"
                   "- 첨부의 n번째 쪽은 위 목록의 n번째 번호입니다. \"페이지\" 칸에는 이 원본 쪽 번호를 쓰세요.\n"
                   "- 빠진 쪽에 있을 내용(다른 패키지의 핀 표 등)은 지어내지 말고 \"7. 불확실 항목\"에 적으세요.")
    return _markdown_from_pdf(provider, pdf, filename, prompt=prompt, marker="# 데이터시트",
                              what="데이터시트 요약 MD", fallback="데이터시트가 길면 필요한 쪽만 잘라서 올려 보세요.",
                              max_output_tokens=max_output_tokens)


SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer", "description": "Index of the BOM line in the request"},
                    "inventory_part_id": {"type": "string"},
                    "fit": {"type": "string", "enum": ["good", "acceptable", "poor"]},
                    "reason": {"type": "string", "description": "Short reason in Korean"},
                },
                "required": ["line", "inventory_part_id", "fit", "reason"],
            },
        }
    },
    "required": ["suggestions"],
}

_SUGGEST_PROMPT = """당신은 회로 설계 엔지니어입니다. 아래 BOM 줄마다, 함께 준 '보유 자재 후보' 중에서 대신 쓸 수 있는 자재를 고르세요.

규칙:
- 반드시 후보 목록에 있는 inventory_part_id만 쓰세요. 목록에 없는 부품을 지어내지 마세요.
- 값, 패키지, 정격(전압·전력), 허용오차, 극성을 고려하세요. 회로 동작이 바뀔 수 있으면 fit을 poor로 하고 이유를 쓰세요.
- 적당한 후보가 없으면 그 줄은 비워 두세요(제안하지 않기).
- 이유는 한국어 한 문장으로 짧게 쓰세요.
- 당신의 제안은 사람이 검토한 뒤에만 적용됩니다.

요청(JSON):
"""


@dataclass
class Suggestion:
    line: int
    references: list[str]
    inventory_part_id: str
    mpn: str | None
    fit: str
    reason: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def suggestion_request(lines: list[LineMatch], inventory: Inventory) -> dict:
    """The exact data sent to the provider (shown to the user before sending)."""
    items = []
    for index, line in enumerate(lines):
        if line.status is MatchStatus.MATCHED:
            continue
        category = line.category
        pool = [p for p in inventory.parts if (p.category or category_of(p.symbol_id)) == category] if category else []
        pool.sort(key=lambda p: (p.priority, -(p.stock or 0), p.source_row))
        items.append({
            "line": index,
            "references": line.references,
            "quantity": line.quantity,
            "category": CATEGORY_LABELS.get(category or "", category),
            "value": line.value,
            "footprint": line.footprint_id,
            "symbol": line.symbol_id,
            "candidates": [
                {k: v for k, v in {
                    "inventory_part_id": p.id, "mpn": p.mpn, "value": p.value, "package": p.package,
                    "footprint": p.footprint_id, "rating": p.rating, "tolerance": p.tolerance, "stock": p.stock,
                    "priority": p.priority, "notes": p.notes,
                }.items() if v not in (None, "")}
                for p in pool[:MAX_CANDIDATES_PER_LINE]
            ],
        })
    return {"lines": [i for i in items if i["candidates"]]}


def suggest_substitutes(provider: LlmProvider, lines: list[LineMatch], inventory: Inventory
                        ) -> tuple[list[Suggestion], list[str], LlmResult | None]:
    request = suggestion_request(lines, inventory)
    if not request["lines"]:
        return [], ["보유 자재 중에 같은 종류의 후보가 없어 AI에 보내지 않았습니다."], None
    result = provider.generate(prompt=_SUGGEST_PROMPT + json.dumps(request, ensure_ascii=False, indent=1),
                               json_schema=SUGGESTION_SCHEMA, max_output_tokens=8192, temperature=0.0)
    try:
        payload = json.loads(result.text)
    except ValueError as exc:
        raise AiError("AI_BAD_OUTPUT", "AI 응답을 JSON으로 읽지 못했습니다.") from exc
    allowed = {item["line"]: {c["inventory_part_id"] for c in item["candidates"]} for item in request["lines"]}
    by_id = {p.id: p for p in inventory.parts}
    out, dropped = [], []
    for s in payload.get("suggestions", []):
        line = s.get("line")
        part_id = s.get("inventory_part_id")
        if line not in allowed or part_id not in allowed[line]:
            dropped.append(f"목록에 없는 제안을 버렸습니다: line={line}, id={part_id}")
            continue
        out.append(Suggestion(line=line, references=lines[line].references, inventory_part_id=part_id,
                              mpn=by_id[part_id].mpn, fit=s.get("fit", "poor"), reason=str(s.get("reason", ""))[:300]))
    return out, dropped, result
