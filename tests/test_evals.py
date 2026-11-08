"""AI performance evaluation: the cases fit the reference designs, the grading is right, and a run end to end with a
fake AI produces the expected numbers. No real AI is called (real numbers come from the user's own runs)."""

import json
from pathlib import Path

import pytest

import backend.evals as evals_mod
from ai_engine import LlmResult
from ai_engine.design import DesignResult
from circuit_core import CircuitIR, validate_circuit
from circuit_core.evals import (
    DESIGN_CASES,
    REVIEW_CASES,
    compare,
    found_by_ai,
    found_by_code,
    plant,
    report_markdown,
    run_check,
    summarize,
    usage_tokens,
)

from .test_ai import ai_client, enable, wait

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def example(name: str) -> CircuitIR:
    return CircuitIR.model_validate_json((EXAMPLES / name / "circuit.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DESIGN_CASES, ids=lambda c: c.id)
def test_reference_designs_meet_their_own_case(case):
    """The example board each case comes from passes every requirement check (the checks are fair)."""
    c = example(case.example)
    assert [ch.label for ch in case.checks if not run_check(ch, c)] == []


def test_checks_catch_a_design_that_misses_the_requirement():
    i2c = next(c for c in DESIGN_CASES if c.id == "i2c-3v3")
    bad = plant(next(d for d in REVIEW_CASES if d.id == "no-sda-pullup"), example("i2c-sensor-3v3"))
    failed = [ch.label for ch in i2c.checks if not run_check(ch, bad)]
    assert failed == ["SDA 풀업이 3.3 V로"]


@pytest.mark.parametrize("defect", REVIEW_CASES, ids=lambda d: d.id)
def test_planted_defects_keep_the_ir_valid_and_are_real(defect):
    clean = example(defect.example)
    bad = plant(defect, clean)
    assert not [i for i in validate_circuit(bad).issues if i.severity.value == "error"]
    assert bad.model_dump() != clean.model_dump()
    # The app's calculated review catches each planted defect and stays quiet on the clean board.
    assert found_by_code(defect, bad) and not found_by_code(defect, clean)


def test_a_finding_counts_only_when_it_names_the_defect():
    led = next(d for d in REVIEW_CASES if d.id == "led-reversed")
    pull = next(d for d in REVIEW_CASES if d.id == "no-sda-pullup")
    hit = {"severity": "error", "title": "D2 극성이 반대", "detail": "", "references": ["D2"]}
    assert found_by_ai(led, [hit]) == hit
    assert found_by_ai(led, [{**hit, "severity": "info"}]) is None  # a note is not a finding
    assert found_by_ai(led, [{"severity": "warning", "title": "D1 저항값 확인", "detail": "", "references": ["D1"]}]) is None
    # The target (by word or listed part) and the problem words.
    assert found_by_ai(pull, [{"severity": "warning", "title": "SDA 라인 풀업 없음", "references": []}])
    assert found_by_ai(pull, [{"severity": "warning", "title": "풀업 없음", "references": ["J2"]}]) is None
    gnd = next(d for d in REVIEW_CASES if d.id == "ic-gnd-open")
    assert found_by_ai(gnd, [hit]) is None  # "D2 ... GND" is not about U1
    assert found_by_ai(gnd, [{"severity": "error", "title": "접지 핀 연결 안 됨", "detail": "", "references": ["U1"]}])
    assert usage_tokens({"prompt_token_count": 10, "candidates_token_count": 5}) == (10, 5)
    assert usage_tokens({"input_tokens": 7, "output_tokens": 3}) == (7, 3)


def test_summary_compare_and_report():
    results = [
        {"suite": "design", "case": "a", "repeat": 1, "completed": True, "requirements": {"rate": 1.0, "checks": []},
         "ir_errors": 0, "kicad": {"status": "passed"}, "calc": {"fail": 0}, "attempts": 1, "reasons": 0.5,
         "calls": 2, "input_tokens": 100, "output_tokens": 50, "seconds": 3.0},
        {"suite": "design", "case": "b", "repeat": 1, "completed": False, "error": "[AI_QUOTA] 한도", "calls": 1},
        {"suite": "review", "case": "x", "repeat": 1, "completed": True, "defect": "x", "found": True,
         "found_by_code": True, "errors": 1, "findings": 2, "calls": 1},
        {"suite": "review", "case": "clean:e", "repeat": 1, "completed": True, "defect": None, "errors": 2, "findings": 3},
    ]
    s = summarize(results)
    assert s["design"]["completed"] == 0.5 and s["design"]["requirements_met"] == 1.0
    assert s["design"]["all_requirements"] == 0.5 and s["design"]["kicad_passed"] == 1.0
    assert s["review"]["found"] == 1.0 and s["review"]["false_errors"] == 2.0
    assert s["cost"] == {"calls": 4, "input_tokens": 100, "output_tokens": 50, "seconds": 3.0, "failed": 1}
    worse = {**s, "design": {**s["design"], "completed": 0.25}, "review": {**s["review"], "false_errors": 3.0}}
    rows = {r["key"]: r for r in compare(s, worse)}
    assert rows["design.completed"]["better"] is True and rows["review.false_errors"]["better"] is True
    run = {"id": "r1", "created_at": "2026-11-06T00:00:00Z", "provider": "gemini", "model": "m", "repeats": 1,
           "suites": ["design", "review"], "results": results, "summary": s}
    md = report_markdown(run, {"summary": worse})
    assert "| 설계 완료율 | 50% | 25% | 좋아짐 |" in md and "## 설계" in md and "## 검토" in md
    assert "[AI_QUOTA] 한도" in md  # why a case did not complete


NOTE = {"title": "노트", "summary": "요약", "background": "배경", "parts": [], "topics": ["ldo"],
        "checks": [{"what": "드롭아웃", "how": "계산", "result": "여유 작음", "status": "issue", "evidence": ["E1"]}],
        "learnings": [{"text": "교훈", "evidence": ["E1", "E99"]}, {"text": "일반 지식", "evidence": []}],
        "apply_next": [{"text": "할 일", "why": "이유", "evidence": ["E1"]}], "open_questions": []}


def reply(prompt: str) -> str:
    if "연구노트로 남기세요" in prompt:
        return json.dumps(NOTE, ensure_ascii=False)
    # Review: the fake AI always says "D2 is reversed" - right for one defect, a false alarm elsewhere.
    return json.dumps({"summary": "검토", "findings": [
        {"severity": "error", "title": "D2 방향 반대", "detail": "애노드가 GND", "references": ["D2"], "suggestion": None,
         "guide": None}]}, ensure_ascii=False)


def test_a_run_end_to_end(tmp_path, monkeypatch):
    def fake_design(ai, inp, resolver, **kwargs):
        ai.generate(prompt="plan", json_schema=None)  # counted like a real call
        name = next(c.example for c in DESIGN_CASES if c.title == inp.board_name)
        return DesignResult(circuit=example(name), plan={}, attempts=1, calls=[])

    monkeypatch.setattr(evals_mod, "design_circuit", fake_design)
    client, created = ai_client(tmp_path, reply=reply)
    cases = client.get("/api/evals/cases").json()
    assert [c["id"] for c in cases["design"]] == [c.id for c in DESIGN_CASES] and not cases["kicad"]
    assert cases["calls_per_repeat"] == {"design": 30, "review": 20, "revise": 20, "notes": 2}
    assert [c["id"] for c in cases["revise"]][0] == "fix:led-reversed"
    assert client.post("/api/evals/runs", json={}).json()["error_code"] == "AI_DISABLED"
    enable(client)
    assert client.post("/api/evals/runs", json={"suites": ["nope"]}).json()["error_code"] == "UNKNOWN_SUITE"

    job = wait(client, client.post("/api/evals/runs", json={"repeats": 1}).json())
    assert job["status"] == "succeeded", job
    assert client.get(f"/api/evals/progress/{job['id']}").json()["done"] == 42  # 10 designs, 20 reviews, 10 fixes, 2 notes
    run = client.get(f"/api/evals/runs/{job['result']['run_id']}").json()
    s = run["run"]["summary"]
    assert s["design"]["completed"] == 1.0 and s["design"]["requirements_met"] == 1.0  # reference designs
    assert s["design"]["kicad_checked"] == 0  # no KiCad here: not counted as passed or failed
    assert s["review"]["found"] == 0.1 and s["review"]["found_by_code"] == 1.0  # only the LED defect
    assert s["review"]["false_errors"] == 1.0  # one error on every clean board
    assert s["review"]["unverified_rate"] == 1.0  # ... and none of them says what it rests on
    assert s["revise"]["completed"] == 0.0  # this fake AI answers edit requests with a review: no edits
    assert s["notes"]["bad_ids"] == 2 and s["notes"]["cited"] == pytest.approx(0.75)  # E99 twice; 3 of 4 lines cite
    assert s["cost"]["calls"] == 10 + 20 + 10 + 2 and s["cost"]["failed"] == 0
    assert run["previous_id"] is None and "## 연구노트" in run["markdown"]
    # Nothing in the user's projects: the run only reads the examples.
    assert client.get("/api/projects").json() == []

    # A second run is compared with the first.
    job2 = wait(client, client.post("/api/evals/runs", json={"suites": ["design", "review", "revise", "notes"],
                                                              "cases": ["led-reversed"]}).json())
    second = client.get(f"/api/evals/runs/{job2['result']['run_id']}").json()
    assert second["run"]["summary"]["review"]["found"] == 1.0
    assert second["previous_id"] == run["run"]["id"]
    found = next(r for r in second["changes"] if r["key"] == "review.found")
    assert found["better"] is True and "좋아짐" in second["markdown"]
    listed = client.get("/api/evals/runs").json()["runs"]
    assert [r["id"] for r in listed] == [second["run"]["id"], run["run"]["id"]]
    report = client.get(f"/api/evals/runs/{run['run']['id']}/report.md")
    assert report.headers["content-disposition"].endswith(f'ai-eval-{run["run"]["id"]}.md"')
    assert client.delete(f"/api/evals/runs/{run['run']['id']}").status_code == 204
    assert client.get(f"/api/evals/runs/{run['run']['id']}").json()["error_code"] == "EVAL_RUN_NOT_FOUND"
    assert client.get("/api/evals/runs/../../etc").status_code == 404


def test_recording_counts_calls_and_tokens():
    class P:
        name, model = "gemini", "m"

        def generate(self, **kw):
            return LlmResult(text="x", model="m", usage={"prompt_token_count": 3, "candidates_token_count": 2})

    r = evals_mod.Recording(P())
    r.generate(prompt="a")
    r.generate(prompt="b")
    assert r.cost() == {"calls": 2, "input_tokens": 6, "output_tokens": 4} and r.last == "x"


def test_the_revise_suite_judges_a_fix_with_the_apps_own_calculation(tmp_path):
    clean = example("ne555-led-board")
    d2 = next(c for c in clean.components if c.reference == "D2")
    net_of = {node.pin: n.name for n in clean.nets for node in n.nodes if node.component_id == d2.id}

    def fixer(prompt: str) -> str:
        if "회로를 새로 설계하지 마세요" in prompt:  # put D2 back the right way round
            return json.dumps({"summary": "D2 방향 바로잡음", "addressed": ["F1"], "edits": [
                {"action": "connect", "net": net_of[p], "pins": [f"D2.{p}"], "why": "F1"} for p in sorted(net_of)]},
                ensure_ascii=False)
        return reply(prompt)

    client, _ = ai_client(tmp_path, reply=fixer)
    enable(client)
    job = wait(client, client.post("/api/evals/runs", json={"suites": ["revise"], "cases": ["led-reversed"]}).json())
    assert job["status"] == "succeeded", job
    run = client.get(f"/api/evals/runs/{job['result']['run_id']}").json()["run"]
    [r] = run["results"]
    assert r["completed"] and r["judged"] and r["fixed"] and r["new_problems"] == 0 and r["untouched"] == 1.0
    assert run["summary"]["revise"] == {"runs": 1, "completed": 1.0, "fixed": 1.0, "new_problems": 0, "untouched": 1.0,
                                        "declined": 0}
    assert "심은 결함 고침률" in client.get(f"/api/evals/runs/{run['id']}").json()["markdown"]
