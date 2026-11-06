"""AI performance evaluation: fixed test cases, scored by code (no AI grades the AI).

Three suites, built from the repository's example boards:

* **design** - the example's requirements go to the AI designer; the circuit it returns is scored on whether it
  completed, IR rule errors, KiCad ERC/netlist (when KiCad is available), calculated review failures, how many
  parts carry a recorded reason, and requirement checks written for each case (a 3.3 V rail exists, SCL and SDA
  have pull-ups to it, ...).
* **review** - a known defect is planted in an example circuit (LED reversed, a pull-up removed, a capacitor
  rated below its rail, decoupling removed, an IC ground left open). The defect counts as found when a warning or
  error names it. The same circuits without a defect measure false alarms. The app's own calculated review is run
  on the same circuits for comparison.
* **notes** - research notes on example circuits: how many statements cite evidence, how many cited numbers did
  not exist, how many page numbers had no source, whether every section was filled.

Everything here is pure: the runner (``backend.evals``) calls the AI and KiCad and hands the results back here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .engineering import engineering_review
from .models import CircuitIR
from .parts import category_of, parse_value

# --- requirement checks on a designed circuit ---------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One thing the requirements ask for, checked on the circuit the AI designed."""

    label: str
    kind: str  # part | category | rail | pullup | cap_on | drives
    args: dict = field(default_factory=dict)


def _text(c) -> str:
    return " ".join(x for x in (c.symbol_id, c.value, c.manufacturer_part_number or "") if x)


def _nets_of(circuit: CircuitIR) -> dict[str, set[str]]:
    """component id -> names of the nets on its pins."""
    out: dict[str, set[str]] = {c.id: set() for c in circuit.components}
    for net in circuit.nets:
        for n in net.nodes:
            out.setdefault(n.component_id, set()).add(net.name)
    return out


def _is_ground(name: str) -> bool:
    return bool(re.fullmatch(r"(?i)(A|D|P)?GND\w*|VSS|0V", name))


def run_check(check: Check, circuit: CircuitIR) -> bool:
    a = check.args
    nets = _nets_of(circuit)
    comps = circuit.components
    if check.kind == "part":
        rx = re.compile(a["match"], re.I)
        return sum(1 for c in comps if rx.search(_text(c))) >= a.get("min", 1)
    if check.kind == "category":
        cats = set(a["category"]) if isinstance(a["category"], (list, tuple)) else {a["category"]}
        rx = re.compile(a["match"], re.I) if a.get("match") else None
        hits = [c for c in comps if category_of(c.symbol_id, c.reference) in cats and (rx is None or rx.search(_text(c)))]
        return len(hits) >= a.get("min", 1)
    if check.kind == "rail":
        rx = re.compile(a["net"], re.I)
        return any(rx.search(n.name) for n in circuit.nets)
    if check.kind == "pullup":
        sig, rail = re.compile(a["signal"], re.I), re.compile(a["rail"], re.I)
        for c in comps:
            if category_of(c.symbol_id, c.reference) != "resistor":
                continue
            on = nets.get(c.id, set())
            if any(sig.search(n) for n in on) and any(rail.search(n) for n in on):
                return True
        return False
    if check.kind == "cap_on":
        rail = re.compile(a["rail"], re.I)
        need = a.get("min_uf", 0.0) * 1e-6
        for c in comps:
            if category_of(c.symbol_id, c.reference) not in ("capacitor", "capacitor_polarized"):
                continue
            on = nets.get(c.id, set())
            farad = parse_value(c.value) or 0.0
            if any(rail.search(n) for n in on) and any(_is_ground(n) for n in on) and farad >= need - 1e-12:
                return True
        return False
    if check.kind == "drives":  # a part (by symbol/value) with a pin on a net (e.g. a regulator on +3V3)
        rx, net = re.compile(a["match"], re.I), re.compile(a["net"], re.I)
        return any(rx.search(_text(c)) and any(net.search(n) for n in nets.get(c.id, set())) for c in comps)
    raise ValueError(f"unknown check kind {check.kind}")


@dataclass(frozen=True)
class DesignCase:
    id: str
    example: str
    """Folder under examples/ whose requirements.md is sent to the AI."""
    title: str
    checks: tuple[Check, ...]


V33 = r"3V3|3\.3V"
V5 = r"(^|[^0-9.])5V|5V0|\+5"
DESIGN_CASES: tuple[DesignCase, ...] = (
    DesignCase("i2c-3v3", "i2c-sensor-3v3", "5V→3.3V I2C 센서 보드", (
        Check("3.3 V 레일이 있다", "rail", {"net": V33}),
        Check("3.3 V 레귤레이터가 3.3 V 레일에 있다", "drives", {"match": r"regulator|ldo|1117|ap2112|mcp1700|xc6206", "net": V33}),
        Check("SCL 풀업이 3.3 V로", "pullup", {"signal": r"SCL", "rail": V33}),
        Check("SDA 풀업이 3.3 V로", "pullup", {"signal": r"SDA", "rail": V33}),
        Check("전원 표시 LED", "category", {"category": "led"}),
        Check("커넥터 2개(호스트·센서)", "category", {"category": "connector", "min": 2}),
    )),
    DesignCase("ne555-blink", "ne555-led-board", "NE555 LED 점멸 보드", (
        Check("555 타이머", "part", {"match": r"555"}),
        Check("LED 2개(전원·상태)", "category", {"category": "led", "min": 2}),
        Check("5 V 입력 벌크 콘덴서 ≥ 10 µF", "cap_on", {"rail": V5, "min_uf": 10}),
        Check("5 V 입력 커넥터", "category", {"category": "connector"}),
    )),
    DesignCase("lm7805-24v", "lm7805-24v-to-5v", "24V→5V 7805 레귤레이터", (
        Check("7805 레귤레이터", "part", {"match": r"7805"}),
        Check("5 V 레일", "rail", {"net": V5}),
        Check("입력 역전압 보호 다이오드", "category", {"category": "diode"}),
        Check("입력·출력 콘덴서 2개 이상", "category", {"category": ["capacitor", "capacitor_polarized"], "min": 2}),
        Check("5 V 전원 LED", "category", {"category": "led"}),
    )),
    DesignCase("drv8871-motor", "dc-motor-drv8871", "12V DC 모터 드라이버", (
        Check("DRV8871", "part", {"match": r"DRV8871"}),
        Check("12 V 레일 벌크 콘덴서 ≥ 47 µF", "cap_on", {"rail": r"12", "min_uf": 47}),
        Check("역전압·서지 보호 다이오드", "category", {"category": "diode"}),
        Check("커넥터 3개(전원·제어·모터)", "category", {"category": "connector", "min": 3}),
        Check("전원 LED", "category", {"category": "led"}),
    )),
    DesignCase("atmega-led", "atmega328p-led-board", "ATmega328P 상태 LED 보드", (
        Check("ATmega328P", "part", {"match": r"ATmega328"}),
        Check("16 MHz 크리스탈", "category", {"category": "crystal", "match": r"16"}),
        Check("RESET 풀업", "pullup", {"signal": r"RESET|RST", "rail": V5}),
        Check("5 V 벌크 콘덴서 ≥ 10 µF", "cap_on", {"rail": V5, "min_uf": 10}),
        Check("LED 2개", "category", {"category": "led", "min": 2}),
        Check("리셋 버튼", "category", {"category": "switch"}),
    )),
)


# --- planted defects for the review suite ------------------------------------------------------------


@dataclass(frozen=True)
class Defect:
    id: str
    example: str
    title: str
    """What is wrong, in words (shown in the report, never sent to the AI)."""
    refs: tuple[str, ...]
    keywords: tuple[tuple[str, ...], ...]
    """Word groups. The first names the target (a part or net) - the finding's listed parts may stand in for it;
    every other group names the problem. A finding names the defect when every group matches."""


_DIRECTION = ("방향", "극성", "거꾸로", "반대", "역방향", "anode", "cathode", "애노드", "캐소드", "reverse")
REVIEW_CASES: tuple[Defect, ...] = (
    Defect("led-reversed", "ne555-led-board", "상태 LED D2의 방향을 거꾸로 연결", ("D2",),
           (("D2",), _DIRECTION)),
    Defect("no-sda-pullup", "i2c-sensor-3v3", "SDA 풀업 저항 R3를 뺌", ("R3",),
           (("SDA",), ("풀업", "풀 업", "pull-up", "pullup", "pull up"))),
    Defect("cap-underrated", "lm7805-24v-to-5v", "24 V 입력 콘덴서 C1 정격을 16 V로", ("C1",),
           (("C1",), ("정격", "내압", "16V", "16 V", "rating"))),
    Defect("no-decoupling", "atmega328p-led-board", "MCU 디커플링 콘덴서 100 nF 3개를 모두 뺌", ("U1",),
           (("U1", "MCU", "ATmega"), ("디커플링", "바이패스", "decoupl", "bypass", "100nF", "100 nF"))),
    Defect("ic-gnd-open", "dc-motor-drv8871", "모터 드라이버 U1의 GND 핀을 GND에서 뺌", ("U1",),
           (("U1", "DRV8871"), ("GND", "접지", "그라운드", "ground"),
            ("연결", "미접속", "open", "떠 ", "floating", "빠져", "없"))),
)


def plant(defect: Defect, circuit: CircuitIR) -> CircuitIR:
    """The example circuit with the defect planted (the IR stays valid; only the design is wrong)."""
    data = circuit.model_dump(mode="json")
    ids = {c["reference"]: c["id"] for c in data["components"]}

    def drop_component(ref: str) -> None:
        cid = ids[ref]
        data["components"] = [c for c in data["components"] if c["id"] != cid]
        for net in data["nets"]:
            net["nodes"] = [n for n in net["nodes"] if n["component_id"] != cid]
        data["nets"] = [n for n in data["nets"] if len(n["nodes"]) >= 1]
        for d in data.get("decisions", []):
            d["components"] = [x for x in d.get("components", []) if x != cid]
        for note in data.get("notes", []):
            note["components"] = [x for x in note.get("components") or [] if x != cid]
        data["layout"] = {k: v for k, v in (data.get("layout") or {}).items() if k != cid}

    if defect.id == "led-reversed":
        cid = ids["D2"]
        for net in data["nets"]:
            for n in net["nodes"]:
                if n["component_id"] == cid:
                    n["pin"] = {"1": "2", "2": "1"}[n["pin"]]
    elif defect.id == "no-sda-pullup":
        drop_component("R3")
    elif defect.id == "cap-underrated":
        for c in data["components"]:
            if c["reference"] == "C1":
                c["value"] = "100uF/16V"
                c["specs"] = None
    elif defect.id == "no-decoupling":
        for ref in [c["reference"] for c in data["components"]
                    if c["reference"].startswith("C") and abs((parse_value(c["value"]) or 0) - 1e-7) < 1e-12]:
            drop_component(ref)
    elif defect.id == "ic-gnd-open":
        cid = ids["U1"]
        gnd_pins = {p["number"] for c in data["components"] if c["id"] == cid for p in c["pins"]
                    if re.fullmatch(r"(?i)GND|PAD|EP", p.get("name") or "")}
        for net in data["nets"]:
            if _is_ground(net["name"]):
                net["nodes"] = [n for n in net["nodes"] if not (n["component_id"] == cid and n["pin"] in gnd_pins)]
    else:
        raise ValueError(defect.id)
    return CircuitIR.model_validate(data)


def _names_defect(defect: Defect, refs: list[str], text: str) -> bool:
    low = text.lower()

    def group(words: tuple[str, ...]) -> bool:
        return any(w.lower() in low for w in words)

    target = group(defect.keywords[0]) or bool(set(refs) & set(defect.refs))
    return target and all(group(g) for g in defect.keywords[1:])


def found_by_ai(defect: Defect, findings: list[dict]) -> dict | None:
    """The first warning/error finding that names the defect, or None."""
    for f in findings:
        if f.get("severity") not in ("error", "warning"):
            continue
        text = " ".join(str(f.get(k) or "") for k in ("title", "detail", "suggestion"))
        if _names_defect(defect, list(f.get("references") or []), text):
            return f
    return None


def found_by_code(defect: Defect, circuit: CircuitIR) -> bool:
    """Whether the app's calculated review flags the defect (for comparison with the AI)."""
    refs = {c.id: c.reference for c in circuit.components}
    for f in engineering_review(circuit):
        if f.severity not in ("fail", "warn"):
            continue
        if _names_defect(defect, [refs.get(c, c) for c in f.components], f"{f.title} {f.detail}"):
            return True
    return False


NOTE_EXAMPLES: tuple[str, ...] = ("i2c-sensor-3v3", "lm7805-24v-to-5v")


# --- summary --------------------------------------------------------------------------------------


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def usage_tokens(usage: dict | None) -> tuple[int, int]:
    """(input, output) tokens from any provider's usage dict (Gemini, Claude, ChatGPT)."""
    u = usage or {}
    inp = u.get("prompt_token_count") or u.get("input_tokens") or 0
    out = u.get("candidates_token_count") or u.get("output_tokens") or 0
    if not inp and not out and u.get("total_token_count"):
        inp = u["total_token_count"]
    return int(inp or 0), int(out or 0)


def summarize(results: list[dict]) -> dict:
    """Headline numbers for a run (each a rate 0-1, an average, or a count; None when nothing was measured)."""
    design = [r for r in results if r["suite"] == "design"]
    review = [r for r in results if r["suite"] == "review"]
    defects = [r for r in review if r.get("defect")]
    clean = [r for r in review if not r.get("defect")]
    notes = [r for r in results if r["suite"] == "notes"]
    done = [r for r in design if r.get("completed")]
    kicad = [r for r in done if r.get("kicad", {}).get("status") in ("passed", "failed")]
    ok_notes = [r for r in notes if r.get("completed")]
    return {
        "design": {
            "runs": len(design),
            "completed": _rate(len(done), len(design)),
            "requirements_met": _avg([r["requirements"]["rate"] for r in done]),
            "all_requirements": _rate(sum(1 for r in done if r["requirements"]["rate"] == 1), len(design)),
            "ir_clean": _rate(sum(1 for r in done if r["ir_errors"] == 0), len(design)),
            "kicad_passed": _rate(sum(1 for r in kicad if r["kicad"]["status"] == "passed"), len(kicad)),
            "kicad_checked": len(kicad),
            "no_calc_fail": _rate(sum(1 for r in done if r["calc"]["fail"] == 0), len(design)),
            "avg_attempts": _avg([r["attempts"] for r in done]),
            "reasons": _avg([r["reasons"] for r in done]),
        },
        "review": {
            "defects": len(defects),
            "found": _rate(sum(1 for r in defects if r.get("found")), len(defects)),
            "found_by_code": _rate(sum(1 for r in defects if r.get("found_by_code")), len(defects)),
            "clean_runs": len(clean),
            "false_errors": _avg([r["errors"] for r in clean if r.get("completed")]),
            "avg_findings": _avg([r["findings"] for r in review if r.get("completed")]),
        },
        "notes": {
            "runs": len(notes),
            "completed": _rate(len(ok_notes), len(notes)),
            "cited": _avg([r["cited_rate"] for r in ok_notes if r["cited_rate"] is not None]),
            "bad_ids": sum(r["bad_ids"] for r in ok_notes),
            "page_flags": sum(r["page_flags"] for r in ok_notes),
            "sections": _avg([r["sections"] for r in ok_notes]),
        },
        "cost": {
            "calls": sum(r.get("calls", 0) for r in results),
            "input_tokens": sum(r.get("input_tokens", 0) for r in results),
            "output_tokens": sum(r.get("output_tokens", 0) for r in results),
            "seconds": round(sum(r.get("seconds", 0.0) for r in results), 1),
            "failed": sum(1 for r in results if r.get("error")),
        },
    }


METRIC_LABELS: dict[str, tuple[str, str]] = {
    "design.completed": ("설계 완료율", "rate"),
    "design.requirements_met": ("요구 충족률(평균)", "rate"),
    "design.all_requirements": ("요구 전부 충족", "rate"),
    "design.ir_clean": ("IR 규칙 오류 0", "rate"),
    "design.kicad_passed": ("KiCad 검사 통과", "rate"),
    "design.no_calc_fail": ("기술 검토 실패 0", "rate"),
    "design.avg_attempts": ("연결 시도 횟수(평균)", "number"),
    "design.reasons": ("근거가 기록된 부품 비율", "rate"),
    "review.found": ("심은 결함 탐지율 (AI)", "rate"),
    "review.found_by_code": ("같은 결함 탐지율 (앱 계산 검토)", "rate"),
    "review.false_errors": ("깨끗한 회로의 오류 지적(평균)", "number"),
    "review.avg_findings": ("검토 의견 수(평균)", "number"),
    "notes.cited": ("노트 근거 인용률", "rate"),
    "notes.bad_ids": ("없는 근거 번호", "count"),
    "notes.page_flags": ("근거 없는 쪽 번호", "count"),
    "notes.sections": ("노트 절 채움률", "rate"),
    "cost.calls": ("AI 호출 수", "count"),
    "cost.input_tokens": ("입력 토큰", "count"),
    "cost.output_tokens": ("출력 토큰", "count"),
    "cost.seconds": ("걸린 시간(초)", "number"),
}
"""Metric key -> (label, kind). Higher is better except the counts, false errors, attempts and cost."""
LOWER_IS_BETTER = {"design.avg_attempts", "review.false_errors", "notes.bad_ids", "notes.page_flags", "cost.calls",
                   "cost.input_tokens", "cost.output_tokens", "cost.seconds"}


def metric(summary: dict, key: str):
    group, name = key.split(".")
    return (summary.get(group) or {}).get(name)


def fmt(value, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "rate":
        return f"{value * 100:.0f}%"
    if kind == "count":
        return f"{int(value):,}"
    return f"{value:g}"


def compare(new: dict, old: dict) -> list[dict]:
    """Each metric's change from an older run's summary; ``better`` is True/False/None."""
    out = []
    for key, (label, kind) in METRIC_LABELS.items():
        a, b = metric(new, key), metric(old, key)
        better = None
        if a is not None and b is not None and a != b:
            better = (a < b) if key in LOWER_IS_BETTER else (a > b)
        out.append({"key": key, "label": label, "kind": kind, "new": a, "old": b, "better": better})
    return out


def report_markdown(run: dict, previous: dict | None = None) -> str:
    """The run as a document: settings, headline metrics (with the change from ``previous``), each case."""
    s = run["summary"]
    lines = [f"# AI 성능 평가 {run['id']}", "",
             f"- 공급자·모델: {run['provider']} · {run['model']}",
             f"- 실행: {run['created_at'][:19]} · 반복 {run['repeats']}회 · 묶음 {', '.join(run['suites'])}",
             f"- 앱 버전: {run.get('app_version', '—')} · KiCad 검사: {'함' if run.get('kicad') else '안 함(KiCad 없음)'}",
             "", "> 채점은 모두 코드가 합니다(AI가 AI를 채점하지 않음). 예제 회로만 보내며 사용자 프로젝트는 보내지 않습니다.", "",
             "## 지표", "", "| 지표 | 값 |" + (" 이전 | 변화 |" if previous else ""),
             "|---|---|" + ("---|---|" if previous else "")]
    rows = compare(s, previous["summary"]) if previous else [
        {"label": label, "kind": kind, "new": metric(s, key)} for key, (label, kind) in METRIC_LABELS.items()]
    for r in rows:
        if r["new"] is None and (not previous or r.get("old") is None):
            continue
        line = f"| {r['label']} | {fmt(r['new'], r['kind'])} |"
        if previous:
            mark = "" if r["better"] is None else (" 좋아짐" if r["better"] else " 나빠짐")
            line += f" {fmt(r['old'], r['kind'])} |{mark} |"
        lines.append(line)
    design = [r for r in run["results"] if r["suite"] == "design"]
    if design:
        lines += ["", "## 설계", "", "| 사례 | 회차 | 완료 | 요구 충족 | IR 오류 | KiCad | 기술 검토 실패 | 시도 | 못 맞춘 요구 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in design:
            missed = ", ".join(c["label"] for c in r.get("requirements", {}).get("checks", []) if not c["ok"]) or "—"
            if not r.get("completed"):
                missed = r.get("error") or "; ".join(r.get("problems") or [])[:200] or "미완성"
            lines.append(f"| {r['case']} | {r['repeat']} | {'예' if r.get('completed') else '아니오'} | "
                         f"{fmt(r.get('requirements', {}).get('rate'), 'rate')} | {r.get('ir_errors', '—')} | "
                         f"{r.get('kicad', {}).get('status', '—')} | {r.get('calc', {}).get('fail', '—')} | "
                         f"{r.get('attempts', '—')} | {missed} |")
    review = [r for r in run["results"] if r["suite"] == "review"]
    if review:
        lines += ["", "## 검토", "", "| 사례 | 회차 | 심은 결함 | AI가 찾음 | 앱 계산 검토가 찾음 | 오류 지적 | 의견 수 |",
                  "|---|---|---|---|---|---|---|"]
        for r in review:
            found = "—" if not r.get("defect") else ("예" if r.get("found") else "아니오")
            code = "—" if not r.get("defect") else ("예" if r.get("found_by_code") else "아니오")
            lines.append(f"| {r['case']} | {r['repeat']} | {r.get('defect_title') or '없음(깨끗한 회로)'} | {found} | {code} | "
                         f"{r.get('errors', '—')} | {r.get('findings', '—')} |")
    notes = [r for r in run["results"] if r["suite"] == "notes"]
    if notes:
        lines += ["", "## 연구노트", "", "| 사례 | 회차 | 근거 인용률 | 없는 근거 번호 | 근거 없는 쪽 번호 | 절 채움 |",
                  "|---|---|---|---|---|---|"]
        for r in notes:
            lines.append(f"| {r['case']} | {r['repeat']} | {fmt(r.get('cited_rate'), 'rate')} | {r.get('bad_ids', '—')} | "
                         f"{r.get('page_flags', '—')} | {fmt(r.get('sections'), 'rate')} |")
    return "\n".join(lines) + "\n"
