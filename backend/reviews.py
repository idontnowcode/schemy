"""Engineering review records (plan 10.1 third axis, Phase 3).

An engineer records what they checked on a board revision: who, which datasheets (and revisions, pages),
which checklist items, and the outcome. These records are the only thing that changes the "engineering
review" status; automated checks and AI never set ``verified``.

Status of each part on the current revision:
- ``verified``: covered by a ``verified`` review of this revision (and no later ``needs_changes`` for it);
- ``needs_review``: not verified, and the part came from a template or an AI design, or a review of an
  older revision covered it (it may have changed since), or the latest review asked for changes;
- ``unverified``: everything else (entered by hand and never reviewed).

Records are JSON files in ``boards/<b>/reviews/`` (one per review, never edited).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from circuit_core import CircuitIR
from circuit_core.templates import TEMPLATES

from .errors import ApiError
from .store import ProjectStore, atomic_write, now_iso

ReviewStatus = Literal["unverified", "needs_review", "verified"]


class DatasheetRef(BaseModel):
    part: str = Field(min_length=1, max_length=120)
    document: str = Field(default="", max_length=300)
    revision: str | None = Field(default=None, max_length=60, description="Datasheet revision/date the engineer read.")
    pages: str | None = Field(default=None, max_length=120, description="Pages checked, e.g. '12, 15-16'.")


class ReviewCheck(BaseModel):
    item: str = Field(min_length=1, max_length=500)
    source: str = Field(default="custom", max_length=200, description="'template:<ref>#<id>', 'part:<id>' or 'custom'.")
    result: Literal["ok", "issue", "na"]
    note: str | None = Field(default=None, max_length=1000)


class ReviewCreate(BaseModel):
    revision: int = Field(ge=1, description="Board revision that was reviewed; must be the current one.")
    reviewer: str = Field(min_length=1, max_length=100)
    scope: Literal["board", "components"] = "board"
    component_ids: list[str] = Field(default_factory=list)
    datasheets: list[DatasheetRef] = Field(default_factory=list)
    checks: list[ReviewCheck] = Field(min_length=1)
    result: Literal["verified", "needs_changes"]
    notes: str | None = Field(default=None, max_length=5000)


class EngineeringReview(ReviewCreate):
    id: str
    project_id: str
    board_id: str
    reviewed_at: str
    covered: list[str] = Field(default_factory=list, description="Component ids the review covers.")


def checklist_for(circuit: CircuitIR | None) -> list[dict]:
    """What an engineer should check on this circuit: each template's checklist, and per IC the datasheet
    comparison. Suggestions only; the engineer records what they actually checked."""
    if circuit is None:
        return []
    items: list[dict] = []
    seen_templates: set[str] = set()
    for c in circuit.components:
        if c.origin and c.origin.startswith("template:") and c.origin not in seen_templates:
            seen_templates.add(c.origin)
            ref = c.origin.split(":", 1)[1]
            t = TEMPLATES.get(ref.split("@")[0])
            if t is None:
                continue
            parts = ", ".join(x.reference for x in circuit.components if x.origin == c.origin)
            for check in t.checklist:
                items.append({"source": f"template:{ref}#{check.id}", "item": check.text, "basis": check.basis,
                              "group": f"{t.title} ({parts})"})
    for c in sorted(circuit.components, key=lambda c: c.reference):
        if c.reference.startswith(("U", "Q")) and not (c.origin or "").startswith("template:"):
            name = f"{c.reference} {c.value}"
            items.append({"source": f"part:{c.id}#pins", "item": f"{name}: 핀 번호·이름이 데이터시트 핀 배치와 같은가",
                          "basis": "데이터시트 Pin Configuration", "group": name})
            items.append({"source": f"part:{c.id}#ratings", "item": f"{name}: 연결된 전원 전압이 권장 동작 조건 안인가",
                          "basis": "데이터시트 Recommended Operating Conditions", "group": name})
            items.append({"source": f"part:{c.id}#external", "item": f"{name}: 데이터시트가 요구하는 외부 부품이 모두 있는가",
                          "basis": "데이터시트 Typical Application", "group": name})
    items.append({"source": "board#checks", "item": "자동 검사(ERC·Netlist) 결과와 경고를 확인했는가",
                  "basis": "「회로도 · 검증」 탭", "group": "보드"})
    return items


class ReviewStore:
    def __init__(self, store: ProjectStore):
        self.store = store
        self._lock = threading.Lock()

    def _dir(self, project_id: str, board_id: str) -> Path:
        return self.store.board_dir(project_id, board_id) / "reviews"

    def list(self, project_id: str, board_id: str) -> list[EngineeringReview]:
        self.store.get_board(project_id, board_id)
        folder = self._dir(project_id, board_id)
        if not folder.is_dir():
            return []
        out = [EngineeringReview.model_validate_json(f.read_text(encoding="utf-8"))
               for f in sorted(folder.glob("*.json"))]
        return sorted(out, key=lambda r: r.id)

    def add(self, project_id: str, board_id: str, body: ReviewCreate) -> EngineeringReview:
        circuit, revision = self.store.get_circuit(project_id, board_id)
        if circuit is None or revision is None:
            raise ApiError(409, "NO_CIRCUIT", "검토할 회로가 없습니다.")
        if body.revision != revision:
            raise ApiError(409, "STALE_REVIEW", f"현재 revision은 {revision}입니다 (검토한 revision {body.revision}).",
                           recovery_hint="현재 revision을 다시 보고 기록하세요.")
        ids = {c.id for c in circuit.components}
        if body.scope == "components":
            unknown = [c for c in body.component_ids if c not in ids]
            if not body.component_ids or unknown:
                raise ApiError(422, "REVIEW_SCOPE", "검토한 부품을 고르세요." if not unknown
                               else f"회로에 없는 부품: {', '.join(unknown)}")
            covered = list(dict.fromkeys(body.component_ids))
        else:
            covered = sorted(ids)
        if body.result == "verified":
            issues = [c.item for c in body.checks if c.result == "issue"]
            if issues:
                raise ApiError(422, "REVIEW_HAS_ISSUES", "문제가 있는 항목이 있으면 결과를 '수정 필요'로 기록하세요.",
                               details={"issues": issues})
            missing = [i for i in checklist_for(circuit)
                       if i["source"].startswith("template:") and _covers(i, circuit, covered)
                       and i["source"] not in {c.source for c in body.checks}]
            if missing:
                raise ApiError(422, "REVIEW_INCOMPLETE",
                               f"템플릿 체크리스트 {len(missing)}개가 기록되지 않았습니다. 확인했거나 해당 없음(na)으로 기록하세요.",
                               details={"missing": [m["item"] for m in missing]})
        with self._lock:
            folder = self._dir(project_id, board_id)
            folder.mkdir(parents=True, exist_ok=True)
            numbers = [int(f.stem) for f in folder.glob("*.json") if f.stem.isdigit()]
            rid = f"{max(numbers, default=0) + 1:04d}"
            record = EngineeringReview(id=rid, project_id=project_id, board_id=board_id, reviewed_at=now_iso(),
                                       covered=covered, **body.model_dump())
            atomic_write(folder / f"{rid}.json", json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return record

    def status(self, project_id: str, board_id: str) -> dict:
        circuit, revision = self.store.get_circuit(project_id, board_id)
        reviews = self.list(project_id, board_id)
        return board_status(circuit, revision, reviews)


def _covers(item: dict, circuit: CircuitIR, covered: list[str]) -> bool:
    ref = item["source"].split(":", 1)[1].split("#")[0]
    return any(c.id in covered for c in circuit.components if c.origin == f"template:{ref}")


def board_status(circuit: CircuitIR | None, revision: int | None, reviews: list[EngineeringReview]) -> dict:
    if circuit is None:
        return {"board": None, "components": {}, "revision": revision, "latest": None}
    parts: dict[str, ReviewStatus] = {}
    for c in circuit.components:
        state: ReviewStatus = "needs_review" if c.origin else "unverified"
        for r in reviews:  # oldest first (reviews are only taken on the current revision): the latest decides
            if c.id not in r.covered:
                continue
            if r.revision == revision:
                state = "verified" if r.result == "verified" else "needs_review"
            else:  # reviewed on an older revision: it may have changed since
                state = "needs_review"
        parts[c.id] = state
    values = set(parts.values())
    board: ReviewStatus = ("verified" if values == {"verified"} else
                           "needs_review" if "needs_review" in values or "verified" in values else "unverified")
    latest = reviews[-1].model_dump(mode="json") if reviews else None
    return {"board": board, "components": parts, "revision": revision, "latest": latest}
