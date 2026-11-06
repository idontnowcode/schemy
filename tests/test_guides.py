"""Design guide: built-in baseline, which entries apply to a circuit, the automatic checklist, the workspace
library and its history, the API, and the guide in AI design / review prompts (scripted fake AI)."""

import json

import pytest

from backend.errors import ApiError
from backend.guides import GuideLibrary
from circuit_core import CircuitIR
from circuit_core.guides import CHECKS, SEED, TOPICS, GuideDraft, GuideEntry, checklist, relevant, to_markdown

from .backend_helpers import make_client, new_project, save_ir
from .conftest import MOTOR_DIR
from .test_ai import wait
from .test_design import design_client, scripted
from .test_quick_fixes import motor_ir


def entries():
    return [GuideEntry(**s, status="adopted", origin="seed", created_at="t", updated_at="t") for s in SEED]


def test_seed_is_well_formed():
    ids = [s["id"] for s in SEED]
    assert len(ids) == len(set(ids)) >= 25
    for s in SEED:
        assert set(s["topics"]) <= set(TOPICS) and (s["check"] is None or s["check"] in CHECKS), s["id"]
        assert s["sources"][0]["kind"] == "practice" and s["rule"] and s["why"]
        assert not any("쪽" in src["text"] or "p." in src["text"] for src in s["sources"])  # no invented pages


def test_which_guides_apply_and_the_checklist():
    motor = CircuitIR.model_validate(motor_ir(drop_header=True))
    ids = [e.id for e in relevant(entries(), circuit=motor)]
    assert {"G-MOT-01", "G-MOT-03", "G-MOT-05", "G-PWR-05", "G-PRT-01"} <= set(ids)
    assert "G-IF-02" not in ids and "G-LED-01" in ids and "G-CLK-01" not in ids  # no CAN part, an LED, no crystal
    assert ids.index("G-MOT-01") < ids.index("G-MOT-02")  # must before should
    # Part-specific entries need their part (or the requirements naming it); keywords pull in others.
    assert "G-IF-02" in [e.id for e in relevant(entries(), text="CAN 노드, SN65HVD230 사용")]
    assert "G-DCDC-01" in [e.id for e in relevant(entries(), text="24V 입력 벅 컨버터")]

    floating = [{"reference": "U1", "pin": "3", "pin_name": "IN1", "problem": "입력 핀이 연결되지 않았습니다"}]
    findings = [{"rule": "motor_vm", "severity": "ok", "title": "U1 VM 12 V: 범위 안"},
                {"rule": "decoupling", "severity": "warn", "title": "U1 VM: 콘덴서 없음"}]
    rows = {r["id"]: r for r in checklist(relevant(entries(), circuit=motor), findings, floating)}
    assert rows["G-MOT-05"]["status"] == "warn" and "IN1" in rows["G-MOT-05"]["notes"][0]
    assert rows["G-MOT-01"]["status"] == "ok" and rows["G-PWR-05"]["status"] == "warn"
    assert rows["G-PRT-02"]["status"] == "manual"

    doc = to_markdown(entries(), generated_at="2026-11-05")
    assert doc.startswith("# 회로 설계 가이드") and "## 모터 구동" in doc and "### G-MOT-01" in doc


def test_library_seeds_once_and_keeps_history(tmp_path):
    lib = GuideLibrary(tmp_path)
    assert len(lib.entries()) == len(SEED) and len(lib.entries()) == len(SEED)  # seeded once
    assert [ev["event"] for ev in lib.history()] == ["seeded"]

    draft = GuideDraft(title="모터 전원 입력에 TVS", rule="12 V 모터 전원 입력에 SMAJ15A급 TVS를 둔다.",
                       why="모터 서지",
                       strength="should", topics=["motor", "protection"])
    mine = lib.create(draft, origin="review", by="김엔지니어", adopt=True)
    assert mine.id == "G-0001" and mine.status == "adopted" and mine.decided_by == "김엔지니어"
    ai = lib.create(draft, origin="ai_review", by="사용자", adopt=True)
    assert ai.status == "candidate"  # text the AI wrote is never adopted at creation
    drafted = lib.create(draft, origin="feedback", by="사용자", adopt=True, ai_text=True)
    assert drafted.status == "candidate" and drafted.id == "G-0003"

    edited = lib.update(mine.id, draft.model_copy(update={"strength": "must"}), by="박팀장")
    assert edited.version == 2 and edited.strength == "must"
    change = [ev for ev in lib.history(mine.id) if ev["event"] == "edited"][0]
    assert change["changes"] == {"strength": {"before": "should", "after": "must"}} and change["by"] == "박팀장"

    assert lib.decide(ai.id, "adopted", by="박팀장", note="검토함").status == "adopted"
    assert lib.decide("G-MOT-04", "retired", by="박팀장").status == "retired"
    with pytest.raises(ApiError):
        lib.delete("G-MOT-04", by="x")  # adopted/seed entries are retired, not deleted
    lib.delete(drafted.id, by="x")
    assert drafted.id not in {e.id for e in lib.entries()}

    lib.record("cited", ["G-MOT-01", "G-NOPE"], by="AI 설계", project_id="p")
    lib.record("issue", ["G-MOT-01"], by="김엔지니어")
    assert lib.stats() == {"G-MOT-01": {"cited": 1, "flagged": 0, "issue": 1}}
    # A new app version with the same seed version does not touch a person's edits.
    assert next(e for e in GuideLibrary(tmp_path).entries() if e.id == "G-MOT-04").status == "retired"


def test_guide_api_board_checklist_and_review(tmp_path):
    client = make_client(tmp_path)
    listing = client.get("/api/guides").json()
    assert len(listing["entries"]) == len(SEED) and listing["topics"]["power"] == "전원"

    body = {"draft": {"title": "모터 IN 핀 풀다운 10k", "rule": "IN1/IN2에 10 kΩ 풀다운을 둔다.", "why": "리셋 중 정지",
                      "strength": "should", "topics": ["motor"], "check": "floating_input",
                      "sources": [{"kind": "review", "text": "보드 A 검토"}]},
            "origin": "engineer", "by": "김엔지니어", "adopt": True}
    created = client.post("/api/guides", json=body)
    assert created.status_code == 201 and created.json()["status"] == "adopted"
    bad = client.post("/api/guides", json={**body, "draft": {**body["draft"], "topics": ["nope"]}})
    assert bad.json()["error_code"] == "INVALID_GUIDE"
    candidate = client.post("/api/guides", json={**body, "origin": "ai_review"}).json()
    assert candidate["status"] == "candidate"
    decided = client.post(f"/api/guides/{candidate['id']}/decision", json={"status": "retired", "by": "박팀장"})
    assert decided.json()["status"] == "retired"
    history = client.get(f"/api/guides/{candidate['id']}/history").json()["events"]
    assert [e["event"] for e in history] == ["created", "retired"]
    doc = client.get("/api/guides-export.md")
    assert doc.headers["content-type"].startswith("text/markdown") and "모터 IN 핀 풀다운 10k" in doc.text

    pid = new_project(client)
    data = json.loads((MOTOR_DIR / "circuit.json").read_text(encoding="utf-8"))
    save_ir(client, pid, {**data, "project_id": pid})
    board = f"/api/projects/{pid}/boards/a"
    items = {i["id"]: i for i in client.get(board + "/guides").json()["items"]}
    assert items["G-MOT-05"]["status"] == "ok" and created.json()["id"] in items
    assert items["G-PRT-02"]["status"] == "ok" and items["G-PRT-03"]["status"] == "manual"  # fuse rule; TVS is manual

    # The engineer's checklist offers the guide; an "issue" on it is counted against the guide.
    review_items = client.get(board + "/reviews/checklist").json()["items"]
    guide_item = next(i for i in review_items if i["source"] == "guide:G-MOT-01")
    rev = client.get(board + "/reviews").json()["status"]["revision"]
    r = client.post(board + "/reviews", json={"revision": rev, "reviewer": "김엔지니어", "result": "needs_changes",
                                               "checks": [{**{k: guide_item[k] for k in ("item", "source")},
                                                           "result": "issue", "note": "어댑터 허용 오차 미고려"}]})
    assert r.status_code == 201, r.text
    stats = {e["id"]: e["stats"] for e in client.get("/api/guides").json()["entries"]}
    assert stats["G-MOT-01"] == {"cited": 0, "flagged": 0, "issue": 1}


GUIDE_DRAFT = {"title": "모터 전원에 TVS", "rule": "모터 전원 입력에 TVS 다이오드를 둔다.",
               "why": "모터 서지로 VM이 튄다.",
               "strength": "should", "topics": ["motor", "protection", "nope"], "parts": [], "keywords": ["모터"],
               "check": "made_up"}


def guided_reply():
    base = scripted(bad_first=False)

    def reply(prompt):
        if "설계 가이드를 정리하는" in prompt:
            return json.dumps(GUIDE_DRAFT, ensure_ascii=False)
        text = base(prompt)
        if "부품 목록을 정하세요" in prompt:
            return json.dumps({**json.loads(text), "guides_applied": ["G-PWR-05", "G-NOT-OFFERED"]}, ensure_ascii=False)
        if "꼼꼼한 회로 설계 검토자" in prompt:
            data = json.loads(text)
            data["findings"][0]["guide"] = "G-PRT-03"
            return json.dumps(data, ensure_ascii=False)
        return text
    return reply


def test_ai_design_gets_the_guide_and_says_what_it_followed(tmp_path):
    client, created, pid = design_client(tmp_path, reply=guided_reply())
    board = f"/api/projects/{pid}/boards/a"
    client.post("/api/guides/G-PWR-02/decision", json={"status": "retired", "by": "박팀장"})
    job = wait(client, client.post(board + "/ai/design", json={}).json())
    assert job["status"] == "succeeded", job
    provider = created[-1]
    plan_prompt = provider.calls[0]["prompt"]
    assert "[설계 지침" in plan_prompt and "[G-PWR-05] (필수)" in plan_prompt and "G-PWR-02" not in plan_prompt
    assert "G-IF-02" in plan_prompt  # the requirements mention CAN
    p = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    assert p["ai"]["guides"]["applied"] == ["G-PWR-05"] and "G-PWR-05" in p["ai"]["guides"]["offered"]
    review_prompt = next(c["prompt"] for c in provider.calls if "꼼꼼한 회로 설계 검토자" in c["prompt"])
    assert "[설계 지침" in review_prompt
    assert p["ai"]["review"]["findings"][0]["guide"] == "G-PRT-03" and p["ai"]["review"]["guides_checked"]
    items = {i["id"]: i for i in client.get(board + "/guides", params={"proposal_id": p["id"]}).json()["items"]}
    assert items["G-PWR-05"]["ai_applied"] is True
    stats = {e["id"]: e["stats"] for e in client.get("/api/guides").json()["entries"]}
    assert stats["G-PWR-05"]["cited"] == 1 and stats["G-PRT-03"] == {"cited": 0, "flagged": 1, "issue": 0}

    # Feedback -> AI draft (not saved) -> saved as a candidate.
    job = wait(client, client.post(board + "/guides/draft", json={"feedback": "모터 전원에 TVS를 꼭 넣어 주세요",
                                                                  "proposal_id": p["id"]}).json())
    assert job["status"] == "succeeded", job
    draft = job["result"]["draft"]
    assert draft["topics"] == ["motor", "protection"] and draft["check"] is None  # unknown values dropped
    draft_prompt = created[-1].calls[-1]["prompt"]
    assert "모터 전원에 TVS를 꼭 넣어 주세요" in draft_prompt and "G-MOT-01" in draft_prompt
    body = {"draft": {**draft, "sources": [job["result"]["source"]]}, "origin": "feedback", "by": "사용자",
            "adopt": True, "ai_text": True}
    saved = client.post("/api/guides", json=body).json()
    assert saved["status"] == "candidate" and saved["sources"][0]["ref"]["proposal_id"] == p["id"]
    assert list((tmp_path / "guides" / "ai_runs").glob("*.json"))


def test_agent_plan_gets_the_guide(tmp_path):
    from .backend_helpers import led_ir
    from .test_agent import GOOD_STEPS, run_until, scripted, start
    from .test_ai import ai_client, enable

    client, created = ai_client(tmp_path, reply=scripted(GOOD_STEPS))
    enable(client)
    pid = new_project(client)
    save_ir(client, pid, led_ir())
    rid = start(client, pid)
    run_until(client, pid, rid, ["PROPOSED_PLAN"])
    prompts = [c["prompt"] for p in created if hasattr(p, "calls") for c in p.calls]
    plan = next(p for p in prompts if "회로 변경을 계획하는 EDA 에이전트" in p)
    assert "[설계 지침" in plan and "G-LED-01" in plan


def test_a_simple_power_board_gets_only_its_guides():
    """Found by running the I2C sensor example: motor/MCU/crystal/DC-DC guides showed up on a board with none of
    them (any tag matched, and "호스트 MCU" in the requirements counted as an MCU)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "examples" / "i2c-sensor-3v3"
    board = CircuitIR.model_validate(json.loads((root / "circuit.json").read_text(encoding="utf-8")))
    text = (root / "requirements.md").read_text(encoding="utf-8")
    ids = {e.id for e in relevant(entries(), circuit=board, text=text, text_topics=False)}
    assert {"G-PWR-01", "G-PWR-02", "G-PWR-03", "G-PWR-05", "G-PRT-01", "G-PRT-02", "G-IF-01", "G-LED-01"} <= ids
    assert not ids & {"G-MCU-01", "G-MCU-03", "G-MCU-05", "G-MOT-02", "G-MOT-05", "G-CLK-01", "G-DCDC-01"}
    # Before a circuit exists (a new AI design), the requirements' words still pick topics.
    assert "G-MCU-01" in {e.id for e in relevant(entries(), text=text)}


def test_untouched_builtin_guides_take_app_updates(tmp_path):
    lib = GuideLibrary(tmp_path)
    mine = next(e for e in lib.entries() if e.id == "G-PWR-03")
    old_topics = ["power", "ldo"]  # as version 1 shipped G-PWR-02/03
    draft = GuideDraft(**{**{k: getattr(mine, k) for k in GuideDraft.model_fields}, "topics": old_topics})
    lib.update("G-PWR-03", draft, by="박팀장")  # a person's edit
    raw = json.loads(lib.path.read_text(encoding="utf-8"))
    raw["seed_version"] = 1
    for e in raw["entries"]:
        if e["id"] == "G-PWR-02":
            e["topics"] = old_topics
    lib.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    after = {e.id: e for e in GuideLibrary(tmp_path).entries()}
    assert after["G-PWR-02"].topics == ["ldo", "power"]  # nobody touched it: updated
    assert after["G-PWR-03"].topics == old_topics and after["G-PWR-03"].version == 2  # a person's edit stays
    updates = [ev for ev in lib.history() if ev["event"] == "seed_updated"]
    assert len(updates) == 1 and "G-PWR-02" in updates[0]["ids"] and "G-LED-01" not in updates[0]["ids"]


def test_keywords_match_whole_latin_words():
    from circuit_core.guides import has_word, topics_of_text

    assert has_word("Status LED 하나", "led") and not has_word("PWM controlled fan", "led")
    assert has_word("모터를 돌린다", "모터") and not has_word("scan chain", "can")
    assert topics_of_text("CAN 통신") >= {"interface"} and "interface" not in topics_of_text("we can do it")
