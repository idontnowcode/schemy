"""Deterministic documents derived from a Circuit IR: canonical JSON, Markdown, BOM CSV.

The same IR always produces the same bytes (FR-06). Nothing here reads the
clock or the environment; callers that want timestamps put them in manifests.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict

from .models import CircuitIR, VerificationStatus
from .validation import validate_circuit

_STATUS_LABEL = {
    VerificationStatus.UNVERIFIED: "미확인 (unverified)",
    VerificationStatus.NEEDS_REVIEW: "검토 필요 (needs_review)",
    VerificationStatus.VERIFIED: "Engineer reviewed (verified)",
}


def canonical_json(circuit: CircuitIR) -> str:
    """The IR as stable, human-diffable JSON (field order from the model, 2-space indent)."""
    return json.dumps(circuit.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def natural_key(reference: str) -> tuple:
    """Sort R2 before R10."""
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", reference))


def _md_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ") or "—"


def bom_rows(circuit: CircuitIR) -> list[dict[str, str]]:
    """One row per (value, symbol, footprint, manufacturer, MPN) group, references naturally sorted."""
    groups: dict[tuple, list] = defaultdict(list)
    for c in circuit.components:
        key = (c.value, c.symbol_id, c.footprint_id or "", c.manufacturer or "", c.manufacturer_part_number or "")
        groups[key].append(c)
    rows = []
    for (value, symbol, footprint, manufacturer, mpn), comps in groups.items():
        refs = sorted((c.reference for c in comps), key=natural_key)
        statuses = sorted({c.pin_map_evidence.status.value for c in comps})
        rows.append({
            "References": " ".join(refs),
            "Quantity": str(len(comps)),
            "Value": value,
            "Symbol": symbol,
            "Footprint": footprint,
            "Manufacturer": manufacturer,
            "MPN": mpn,
            "PinMapStatus": "/".join(statuses),
        })
    rows.sort(key=lambda r: natural_key(r["References"].split()[0]))
    return rows


BOM_COLUMNS = ("References", "Quantity", "Value", "Symbol", "Footprint", "Manufacturer", "MPN", "PinMapStatus")


def bom_csv(circuit: CircuitIR) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=BOM_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(bom_rows(circuit))
    return buf.getvalue()


def circuit_markdown(circuit: CircuitIR, *, revision: int | None = None) -> str:
    """Readable design summary. It is a derived document; circuit.json stays the source of truth."""
    refs = {c.id: c.reference for c in circuit.components}
    out: list[str] = []
    w = out.append

    w(f"# {circuit.title or circuit.project_id}")
    w("")
    w("> 이 문서는 Circuit IR에서 자동 생성되었습니다. 기준 원본은 `circuit.json`입니다.")
    w("> 자동 생성 문서는 전기적 설계 검증 결과가 아닙니다.")
    w("")
    w("| 항목 | 값 |")
    w("|---|---|")
    w(f"| 프로젝트 ID | `{circuit.project_id}` |")
    w(f"| IR 스키마 | {circuit.schema_version} |")
    if revision is not None:
        w(f"| Revision | {revision} |")
    w(f"| 엔지니어링 검토 상태 | {_STATUS_LABEL[circuit.validation.status]} |")
    if circuit.validation.notes:
        w(f"| 검토 메모 | {_md_cell(circuit.validation.notes)} |")
    w(f"| 부품 수 | {len(circuit.components)} |")
    w(f"| Net 수 | {len(circuit.nets)} |")
    w("")

    w("## 부품")
    w("")
    w("| Ref | 값 | 심볼 | 풋프린트 | MPN | 핀맵 근거 상태 | 설명 |")
    w("|---|---|---|---|---|---|---|")
    for c in sorted(circuit.components, key=lambda c: natural_key(c.reference)):
        w(f"| {c.reference} | {_md_cell(c.value)} | `{c.symbol_id}` | {_md_cell(c.footprint_id)} | "
          f"{_md_cell(c.manufacturer_part_number)} | {c.pin_map_evidence.status.value} | {_md_cell(c.description)} |")
    w("")

    w("## Net")
    w("")
    w("| Net | 연결 (Ref.핀번호) |")
    w("|---|---|")
    for net in sorted(circuit.nets, key=lambda n: n.name):
        nodes = sorted((f"{refs.get(n.component_id, n.component_id)}.{n.pin}" for n in net.nodes), key=natural_key)
        w(f"| {net.name} | {', '.join(nodes)} |")
    w("")

    if circuit.notes:
        w("## 회로도 주석")
        w("")
        w("| 위치 | 주석 (회로도에 영문으로 표시) |")
        w("|---|---|")
        for note in circuit.notes:
            where = (f"Net {note.net}" if note.net else
                     ", ".join(sorted((refs.get(c, c) for c in note.components), key=natural_key)) or "페이지")
            w(f"| {_md_cell(where)} | {_md_cell(note.text)} |")
        w("")

    w("## 핀 정의")
    w("")
    w("연결은 KiCad 심볼의 **핀 번호** 기준입니다. 이름은 라이브러리 대조용 근거입니다.")
    w("")
    w("| Ref | 핀 번호 | 핀 이름 | Net |")
    w("|---|---|---|---|")
    net_of = {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}
    for c in sorted(circuit.components, key=lambda c: natural_key(c.reference)):
        for p in sorted(c.pins, key=lambda p: natural_key(p.number)):
            net = net_of.get((c.id, p.number), "(미연결)")
            w(f"| {c.reference} | {p.number} | {_md_cell(p.name)} | {_md_cell(net)} |")
    w("")

    result = validate_circuit(circuit)
    w("## IR 규칙 검사 (L0/L1)")
    w("")
    if not result.issues:
        w("문제 없음.")
    else:
        for issue in result.issues:
            w(f"- **{issue.severity.value}** `{issue.error_code}`: {issue.message}")
    w("")

    w("## BOM")
    w("")
    w("| " + " | ".join(BOM_COLUMNS) + " |")
    w("|" + "---|" * len(BOM_COLUMNS))
    for row in bom_rows(circuit):
        w("| " + " | ".join(_md_cell(row[col]) for col in BOM_COLUMNS) + " |")
    w("")
    return "\n".join(out)
