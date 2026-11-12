"""Review by perspective: the passes that apply to a circuit, their checklists, what a pass returns, and how the
passes join a review and the automatic review loop. The AI is scripted."""

import json

from ai_engine import AiError
from ai_engine.review_passes import PERSPECTIVES, review_perspective, select_perspectives
from backend.routes_design import merge_pass_findings

from .circuits import can_node
from .test_ai import wait
from .test_design import REQUIREMENTS, Scripted, design_client, finding, scripted

PASS_MARK = "관점 하나로만 깊게 검토하세요"


def label_of(prompt: str) -> str:
    return prompt.split("「", 1)[1].split("」", 1)[0]


def passes(answers: dict | None = None, design=None):
    """A scripted AI that also answers perspective passes: ``answers`` maps a perspective label to its reply (a dict
    or an exception to raise); the others answer every item as checked and fine."""
    base, seen = design or scripted(bad_first=False), []

    def reply(prompt):
        if PASS_MARK not in prompt:
            return base(prompt)
        label = label_of(prompt)
        seen.append(label)
        answer = (answers or {}).get(label)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            count = prompt.split("[답하는 법]")[0].count("\n- (")
            answer = {"summary": f"{label} 문제없음", "findings": [],
                      "checks": [{"item": f"({i + 1}) 확인", "result": "ok", "note": "계산함"} for i in range(count)]}
        return json.dumps(answer, ensure_ascii=False)
    return reply, seen


def test_perspectives_follow_the_circuit():
    chosen = {p.key: items for p, items in select_perspectives(can_node(), REQUIREMENTS)}
    # A CAN transceiver board: requirements, power and protection, and the interface half of signals. No MCU.
    assert list(chosen) == ["requirements", "power", "signals"]
    assert any("종단" in t for t in chosen["signals"]) and not any("오차 예산" in t for t in chosen["signals"])
    # Naming an analog measurement in the requirements brings the analog items (the error budget, in numbers).
    analog = {p.key: items for p, items in select_perspectives(can_node(), REQUIREMENTS + " 0~10V 아날로그 입력 2채널")}
    assert any("오차 예산" in t and "숫자로" in t for t in analog["signals"])
    assert [p.key for p in PERSPECTIVES] == ["requirements", "power", "signals", "control"]


def test_a_pass_returns_what_it_checked_and_what_it_skipped():
    circuit = can_node()
    perspective, items = next(x for x in select_perspectives(circuit, REQUIREMENTS) if x[0].key == "signals")
    answer = {"summary": "종단 값 확인 필요", "checks": [
        {"item": "(1) 종단 저항 R1", "result": "issue", "note": "R1 120 Ω이 CANH–CANL에 있음",
         "references": ["R1", "X9"]},
        {"item": "(2) 전압 레벨", "result": "ok", "note": "3.3 V 트랜시버와 3.3 V MCU"},
        {"item": "설명만 있는 줄", "result": "모름", "note": ""},
        {"item": "", "result": "ok", "note": "버림"}],
        "findings": [finding("warning", "종단 저항 값 확인"),
                     {"severity": "error", "title": "근거 없는 지적", "detail": "x", "basis": "circuit",
                      "evidence": "U9.9"}]}
    provider = Scripted(lambda prompt: json.dumps(answer, ensure_ascii=False))
    done, _ = review_perspective(provider, perspective, items, requirements=REQUIREMENTS, spec=None, circuit=circuit,
                                 evidence="(문제 없음)", changes=["R1 값 120 → 121"])
    prompt = provider.calls[0]["prompt"]
    assert "「인터페이스·아날로그」 관점 하나로만" in prompt and "- (1) 버스마다 종단" in prompt
    assert "[이번 수정안에서 바뀐 것" in prompt and "- R1 값 120 → 121" in prompt
    assert "[근거 원칙" in prompt and "[회로]" in prompt  # the same rules and material as any review
    assert (done["key"], done["label"], done["asked"]) == ("signals", "인터페이스·아날로그", len(items))
    assert [(c["item"], c["result"], c["references"]) for c in done["checks"]] == [
        ("(1) 종단 저항 R1", "issue", ["R1"]), ("(2) 전압 레벨", "ok", []), ("설명만 있는 줄", "unknown", [])]
    # Items (3)… were asked and not answered: said, so a short answer does not read as full cover.
    assert len(done["skipped"]) == len(items) - 2 and done["skipped"][0].startswith("(3) ")
    assert [(f["title"], f["verified"], f["perspective"]) for f in done["findings"]] == [
        ("근거 없는 지적", False, "signals"), ("종단 저항 값 확인", True, "signals")]


def test_the_same_point_from_two_passes_is_kept_once():
    general = [{**finding("warning", "ESD 보호 없음"), "verified": True, "perspective": "general"},
               {**finding("info", "분압 저항 오차 확인"), "verified": None, "perspective": "general"}]
    extra = [{**finding("warning", "ESD 보호가 없음"), "verified": True, "perspective": "power"},
             {**finding("warning", "분압 저항 오차 확인"), "verified": True, "perspective": "signals"},
             {**finding("error", "새 결함"), "verified": True, "perspective": "signals"}]
    merged = merge_pass_findings(general, extra)
    assert [(f["title"], f["severity"]) for f in merged] == [
        ("새 결함", "error"), ("ESD 보호 없음", "warning"), ("분압 저항 오차 확인", "warning")]
    assert merged[1]["perspectives"] == ["general", "power"]
    # The pass's checked warning replaces the general pass's passing remark about the same thing.
    assert merged[2]["verified"] is True and merged[2]["perspectives"] == ["general", "signals"]


def test_a_deep_review_adds_the_passes_to_the_general_review(tmp_path):
    answers = {"인터페이스·아날로그": {"summary": "종단 값", "checks": [
        {"item": "(1) 종단", "result": "issue", "note": "R1"},
        {"item": "(2) 레벨", "result": "unknown", "note": "문턱 자료 없음"}],
        "findings": [finding("warning", "종단 저항 값 확인")]},
        "전원·보호": AiError("AI_UNAVAILABLE", "upstream broke")}
    reply, seen = passes(answers)
    client, created, pid = design_client(tmp_path, reply)
    board = f"/api/projects/{pid}/boards/a"
    job = wait(client, client.post(board + "/ai/design", json={"review": False}).json())
    proposal = job["result"]["proposal_id"]
    done = wait(client, client.post(board + "/ai/review", json={"proposal_id": proposal, "deep": True}).json())
    assert done["status"] == "succeeded", done
    review = done["result"]["review"]
    assert review["depth"] == "deep" and seen == ["요구사항 대조", "전원·보호", "인터페이스·아날로그"]
    by_key = {x["key"]: x for x in review["passes"]}
    assert by_key["power"]["error"] and by_key["power"]["checks"] == []  # one failed pass does not lose the rest
    assert by_key["signals"]["found"] == ["종단 저항 값 확인"] and len(by_key["requirements"]["checks"]) == 5
    titles = {f["title"]: f for f in review["findings"]}
    assert titles["종단 저항 값 확인"]["perspective"] == "signals" and titles["종단 저항 값 확인"]["verified"] is True
    assert titles["ESD 보호 없음"]["perspective"] == "general"  # the general review's own finding is still there
    steps = [s["text"] for s in done["progress"] if s["step"] == "review"]
    assert steps[0].startswith("관점별 검토 1/3: 요구사항 대조 (확인 항목 5개)")
    # Without the option a review is the one general call it always was.
    quick = wait(client, client.post(board + "/ai/review", json={"proposal_id": proposal}).json())
    assert "passes" not in quick["result"]["review"] and len(seen) == 3


def test_the_loop_adds_passes_to_an_existing_review_and_reviews_revisions_deep(tmp_path):
    state = {"general": 0}
    base = scripted(bad_first=False)

    def design(prompt):
        if "아래 회로가 요구사항을 만족하는지" in prompt:  # the general review
            state["general"] += 1
            return json.dumps({"summary": f"일반 검토 {state['general']}", "findings": []}, ensure_ascii=False)
        return base(prompt)

    found = {"n": 0}

    def signals(prompt):
        found["n"] += 1
        first = found["n"] == 1
        return {"summary": "x", "checks": [{"item": "(1) 종단", "result": "issue" if first else "ok", "note": "R1"},
                                           {"item": "(2) 레벨", "result": "unknown", "note": "자료 없음"}],
                "findings": [finding("warning", "종단 저항 값 확인")] if first else []}

    answers = {}
    reply, seen = passes(answers, design=design)

    def routed(prompt):
        if PASS_MARK in prompt and label_of(prompt) == "인터페이스·아날로그":
            seen.append("인터페이스·아날로그")
            state.setdefault("signal_prompts", []).append(prompt)
            return json.dumps(signals(prompt), ensure_ascii=False)
        return reply(prompt)

    client, _, pid = design_client(tmp_path, routed)
    board = f"/api/projects/{pid}/boards/a"
    job = wait(client, client.post(board + "/ai/design", json={}).json())  # with its own general review
    assert state["general"] == 1 and seen == []
    auto = wait(client, client.post(board + "/ai/auto-review", json={
        "proposal_id": job["result"]["proposal_id"], "max_warnings": 0, "deep": True}).json())
    r = auto["result"]
    assert auto["status"] == "succeeded" and r["stop"] == "met", r
    first, second = r["rounds"]
    # Round 1 kept the design job's general review and only added the three passes; a pass found what it missed.
    assert seen[:3] == ["요구사항 대조", "전원·보호", "인터페이스·아날로그"]
    assert (first["warnings"], first["said"], first["passes"], first["unknown"]) == (1, ["종단 저항 값 확인"], 3, 1)
    assert first["unknown_said"] == ["(2) 레벨"] and first["checks"] > 10
    # The revision is reviewed by every pass again, told what the revision changed.
    assert (second["warnings"], second["passes"]) == (0, 3) and len(seen) == 6
    assert "[이번 수정안에서 바뀐 것" in state["signal_prompts"][1] and "R1 값" in state["signal_prompts"][1]
    assert "[이전 회차 의견" in state["signal_prompts"][1]
    assert state["general"] == 2  # the design's review, then one general review of the revision — not three
    steps = [s["text"] for s in auto["progress"] if s["step"] == "review"]
    assert steps[0] == f"1회차: 제안 #{int(job['result']['proposal_id'])} 관점별 검토를 더합니다"
    assert any(s.startswith("1회차: 관점별 검토 3/3: 인터페이스·아날로그") for s in steps)
