"""The rationale report: why the circuit is the way it is, for a person to review.

It puts together what the circuit records (design decisions with who made them and why, the parts, the English
schematic notes), the calculated technical review, and - when the caller has them - the requirements, the
proposal history (what was asked, what the AI assumed and cited, what was approved) and the review status.
Nothing in it is a verification: it shows what to check.
"""

from __future__ import annotations

from .engineering import Finding, engineering_review, review_summary, supply_nets
from .export import natural_key
from .models import CircuitIR

BY_TITLE = {
    "requirement": "사용자 요구를 그대로 반영한 것",
    "changed": "사용자 요구를 바꾼 것",
    "ai": "AI(설계 도우미)가 정한 것",
    "template": "검토된 템플릿이 정한 것",
    "engineer": "엔지니어가 정한 것",
}
BY_SHORT = {"requirement": "요구 반영", "changed": "요구 수정", "ai": "AI 선정", "template": "템플릿", "engineer": "엔지니어"}
SEVERITY = {"fail": "실패", "warn": "주의", "info": "참고", "ok": "통과"}
SOURCE = {"user": "직접 제안", "ai_design": "AI 설계", "ai_revision": "AI 수정안", "ai_pdf_import": "PDF 가져오기 (AI)",
          "template": "템플릿", "agent": "AI Agent", "kicad_layout": "KiCad 배치"}
STATUS = {"approved": "승인", "rejected": "거부", "ready": "승인 대기", "check_failed": "검사 실패", "invalid": "규칙 오류",
          "stale": "기준 회로 바뀜", "checking": "검사 중"}
REVIEW = {"verified": "검토 완료", "needs_review": "검토 필요", "unverified": "미검증"}


def _cell(text: object) -> str:
    return str(text if text not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _origin(origin: str | None) -> str:
    if not origin:
        return "직접 입력"
    if origin == "ai":
        return "AI 설계"
    return "템플릿 " + origin.removeprefix("template:")


def rationale_markdown(circuit: CircuitIR, *, revision: int | None = None, requirements: str | None = None,
                       proposals: list[dict] | None = None, review: dict | None = None,
                       automated_check: dict | None = None, findings: list[Finding] | None = None,
                       generated_at: str | None = None) -> str:
    findings = engineering_review(circuit) if findings is None else findings
    refs = {c.id: c.reference for c in circuit.components}
    by_part: dict[str, list] = {c.id: [] for c in circuit.components}
    for d in circuit.decisions:
        for cid in d.components:
            by_part.setdefault(cid, []).append(d)
    counts = {k: sum(1 for d in circuit.decisions if d.by == k) for k in BY_TITLE}
    summary = review_summary(findings)
    without = [c for c in circuit.components if not by_part.get(c.id)]
    lines: list[str] = []
    w = lines.append

    w(f"# 근거 리포트: {circuit.title or circuit.project_id}")
    w("")
    meta = [f"프로젝트 `{circuit.project_id}`"]
    if revision is not None:
        meta.append(f"revision {revision}")
    if generated_at:
        meta.append(f"작성 {generated_at}")
    w(" · ".join(meta))
    w("")
    w("> 이 리포트는 회로의 선택과 그 근거를 검토하기 위한 것입니다. 자동 계산과 KiCad 검사는 설계 검증이 아니며, "
      "`verified`는 엔지니어 검토 기록으로만 정해집니다.")
    w("")

    w("## 1. 요약")
    w("")
    w("| 항목 | 내용 |")
    w("|---|---|")
    w(f"| 부품 | {len(circuit.components)}개 (근거 기록 있음 {len(circuit.components) - len(without)}, 없음 {len(without)}) |")
    w("| 설계 결정 | " + (", ".join(f"{BY_SHORT[k]} {v}" for k, v in counts.items() if v) or "기록 없음") + " |")
    w("| 기술 검토 (자동 계산) | " + ", ".join(f"{SEVERITY[k]} {v}" for k, v in summary.items()) + " |")
    if automated_check:
        erc = automated_check.get("erc_errors")
        w(f"| KiCad 자동 검사 | {_cell(automated_check.get('status'))}"
          + (f" (ERC 오류 {erc}, Netlist = IR {'예' if automated_check.get('netlist_matches_ir') else '아니오'})"
             if erc is not None else "") + " |")
    if review:
        w(f"| 엔지니어 검토 | {REVIEW.get(review.get('board') or '', '회로 없음')} |")
    w("")

    if requirements and requirements.strip():
        w("## 2. 요구사항 (사용자 원문)")
        w("")
        for line in requirements.strip().splitlines():
            w(f"> {line}" if line.strip() else ">")
        w("")

    w("## 3. 설계 결정과 근거")
    w("")
    if not circuit.decisions:
        w("기록된 결정이 없습니다. 제안을 만들 때 결정(`set_decision`)을 함께 남기면 여기에 나옵니다.")
        w("")
    for kind, title in BY_TITLE.items():
        items = [d for d in circuit.decisions if d.by == kind]
        if not items:
            continue
        w(f"### {title} ({len(items)})")
        w("")
        for d in items:
            parts = ", ".join(sorted((refs.get(c, c) for c in d.components), key=natural_key))
            w(f"**{d.topic}** — {d.choice}")
            w("")
            if parts:
                w(f"- 부품: {parts}")
            if d.request:
                w(f"- 요청: {d.request}")
            w(f"- 근거: {d.reason}")
            if d.calc:
                w(f"- 계산: {d.calc}")
            if d.source:
                w(f"- 출처: {d.source}")
            w("")

    w("## 4. 부품별 근거")
    w("")
    status = (review or {}).get("components") or {}
    w("| Ref | 값 | 역할 | 들어온 경로 | 관련 결정 | 검토 상태 |")
    w("|---|---|---|---|---|---|")
    for c in sorted(circuit.components, key=lambda c: natural_key(c.reference)):
        ds = by_part.get(c.id) or []
        dec = "<br>".join(f"{BY_SHORT[d.by]}: {_cell(d.topic)}" for d in ds) or "**근거 기록 없음**"
        w(f"| {c.reference} | {_cell(c.value)} | {_cell(c.description)} | {_origin(c.origin)} | {dec} | "
          f"{REVIEW.get(status.get(c.id, ''), '—')} |")
    w("")
    if without:
        w(f"근거 기록이 없는 부품: {', '.join(sorted((c.reference for c in without), key=natural_key))}. "
          "역할만 있고 값·부품 선정의 근거가 남아 있지 않으니 검토 때 확인하세요.")
        w("")

    w("## 5. 기술 검토 (자동 계산)")
    w("")
    if not findings:
        w("계산할 수 있는 항목이 없었습니다.")
    else:
        w("| 결과 | 항목 | 내용 | 계산 |")
        w("|---|---|---|---|")
        for f in findings:
            w(f"| {SEVERITY[f.severity]} | {_cell(f.title)} | {_cell(f.detail)} | {_cell(f.calc)} |")
    w("")
    rails = supply_nets(circuit)
    if rails:
        w("계산에 쓴 전원 조건:")
        w("")
        w("| Net | 전압 | 부하 전류 |")
        w("|---|---|---|")
        for r in rails:
            volts = (f"{r['voltage']:g} V (회로에 적은 값)" if r["voltage"] is not None else
                     f"{r['voltage_from_name']:g} V (Net 이름)" if r["voltage_from_name"] is not None else "모름")
            amps = (f"{r['load_current'] * 1000:g} mA (회로에 적은 값)" if r["load_current"] is not None else
                    f"{r['derived_current'] * 1000:g} mA (뒤쪽 Net에서 계산)" if r["derived_current"] is not None else "모름")
            w(f"| {r['net']} | {volts} | {amps} |")
        w("")
    w("검토 규칙: 콘덴서 정격 전압(1.5배 여유)과 극성, LED 방향·직렬 저항·전류(20 mA 이하), 저항 소비 전력(크기별 일반 정격의 50%), "
      "리니어 레귤레이터 드롭아웃·발열·최대 전류(부하 전류를 모르면 100 mA 기준), 다이오드 전류·역전압, IC 전원 핀의 디커플링, "
      "I2C 풀업, 크리스탈 부하 콘덴서. 전압은 회로에 적은 값, 없으면 Net 이름(+5V, +3V3)에서 읽습니다.")
    w("")

    w("## 6. 변경 이력 (제안과 승인)")
    w("")
    if proposals is None:
        w("이 리포트는 회로 파일만으로 만들어 앱의 제안 이력이 들어 있지 않습니다. 앱의 「문서 · BOM → 근거 리포트」나 생성 결과물 "
          "`rationale.md`에는 제안 이력(제안 근거, AI 가정·질문·데이터시트 인용, 승인·거부)이 함께 나옵니다.")
        w("")
    elif not proposals:
        w("제안 이력이 없습니다(회로를 직접 저장함).")
        w("")
    if proposals:
        for p in proposals:
            head = f"### #{int(p['id'])} {p['title']}"
            w(head)
            w("")
            info = [SOURCE.get(p.get("source", ""), p.get("source", "")), STATUS.get(p.get("status", ""), p.get("status", ""))]
            if p.get("result_revision"):
                info.append(f"→ revision {p['result_revision']}")
            w(" · ".join(info))
            w("")
            if p.get("rationale"):
                w(f"- 제안 근거: {p['rationale']}")
            if p.get("decision_note"):
                w(f"- 결정 메모: {p['decision_note']}")
            ai = p.get("ai") or {}
            if ai.get("model"):
                w(f"- AI 모델: {ai['model']}")
            for a in ai.get("assumptions") or []:
                w(f"- AI 가정: {a}")
            for q in ai.get("questions") or []:
                w(f"- AI가 남긴 질문: {q}")
            for c in ai.get("citations") or []:
                page = f" p.{c['page']}" if c.get("page") else ""
                w(f"- 데이터시트 인용 ({c.get('reference', '')}{page}): {c.get('text', '')}")
            tpl = p.get("template") or {}
            if tpl.get("title"):
                params = ", ".join(f"{k}={v}" for k, v in (tpl.get("params") or {}).items())
                w(f"- 템플릿: {tpl['title']}" + (f" ({params})" if params else ""))
            d = p.get("diff") or {}
            changes = [f"{len(d.get(k) or [])} {label}" for k, label in
                       (("components_added", "부품 추가"), ("components_removed", "부품 삭제"),
                        ("properties_changed", "속성 변경"), ("connections", "연결 변경"),
                        ("decisions", "결정 기록"), ("notes", "주석")) if d.get(k)]
            if changes:
                w(f"- 바뀐 것: {', '.join(changes)}")
            w("")

    if circuit.notes:
        w("## 7. 회로도 주석 (영문)")
        w("")
        w("| 위치 | 주석 |")
        w("|---|---|")
        for n in circuit.notes:
            where = f"Net {n.net}" if n.net else ", ".join(sorted((refs.get(c, c) for c in n.components), key=natural_key)) or "페이지"
            w(f"| {_cell(where)} | {_cell(n.text)} |")
        w("")

    w("## 8. 리뷰할 때 확인할 것")
    w("")
    todo = [f"{SEVERITY[f.severity]}: {f.title}" for f in findings if f.severity in ("fail", "warn")]
    todo += [f"근거 없음: {c.reference} ({c.value})" for c in sorted(without, key=lambda c: natural_key(c.reference))]
    todo += [f"요구 수정 확인: {d.topic} — {d.choice}" for d in circuit.decisions if d.by == "changed"]
    if review and review.get("board") != "verified":
        todo.append("엔지니어 검토 기록이 아직 없거나 끝나지 않았습니다 (「검토」 탭).")
    for t in todo or ["자동으로 찾은 확인 항목은 없습니다. 그래도 데이터시트와 대조한 엔지니어 검토가 필요합니다."]:
        w(f"- [ ] {t}")
    w("")
    return "\n".join(lines)
