"""Research notes (연구노트): the AI writes a draft from the app's evidence, people edit and review it, and an
"apply next time" item becomes a design-guide candidate. No real AI is called."""

import json

from circuit_core.notes import Evidence, NoteApply, NoteDraft, NoteLine, ResearchNote, keep_known, to_markdown

from .backend_helpers import new_project, save_ir
from .conftest import I2C_DIR
from .test_ai import FakeProvider, ai_client, enable, wait

NOTE = {
    "title": "5V→3.3V LDO 보드: 드롭아웃 여유와 역극성 보호",
    "summary": "LM1117-3.3 보드를 검토했다. 최저 입력에서 드롭아웃 여유가 0.2 V뿐이다.",
    "background": "호스트에서 5V를 받아 LDO U1(LM1117-3.3)로 3.3V I2C 센서에 공급한다.",
    "parts": ["LM1117-3.3", "PTC 0.2A"],
    "topics": ["ldo", "nonsense"],
    "checks": [{"what": "최저 입력에서 드롭아웃", "how": "4.7 V − 3.3 V와 데이터시트 1.2 V 비교",
                "result": "여유 0.2 V", "status": "issue", "evidence": ["E1", "E99", "e2"]}],
    "learnings": [{"text": "LDO 앞의 직렬 쇼트키는 드롭아웃 여유를 0.3~0.4 V 줄인다.", "evidence": ["E1"]},
                  {"text": "출력 콘덴서 ESR 조건은 p.99에 있다.", "evidence": ["E1"]},
                  {"text": "PTC는 차가울 때와 뜨거울 때 저항이 다르다.", "evidence": []}],
    "apply_next": [{"text": "LDO 입력 최저 전압을 전원 조건에 적고 드롭아웃을 최저 입력으로 계산한다.",
                    "why": "정격 입력만 보면 여유를 과대평가한다.", "evidence": ["E1"]}],
    "open_questions": [{"text": "호스트 5V의 실제 최저 전압을 측정해야 한다.", "evidence": []}],
}
REVIEW = {"summary": "전원 쪽 드롭아웃 여유가 작습니다.",
          "findings": [{"severity": "warning", "title": "드롭아웃 여유 부족", "detail": "4.7 V 입력에서 0.2 V",
                        "references": ["U1"], "suggestion": None, "guide": None}]}


def reply(prompt: str) -> str:
    return json.dumps(NOTE if "연구노트" in prompt else REVIEW, ensure_ascii=False)


def i2c_board(tmp_path):
    client, created = ai_client(tmp_path, reply=reply)
    pid = new_project(client, "I2C Sensor")
    save_ir(client, pid, json.loads((I2C_DIR / "circuit.json").read_text(encoding="utf-8")))
    client.put(f"/api/projects/{pid}/requirements", json={"text": "호스트 5V에서 3.3V I2C 센서 보드"})
    return client, created, pid, f"/api/projects/{pid}/boards/a"


def test_note_markdown_marks_where_each_line_comes_from():
    ev = [Evidence(id="E1", kind="calc", text="최저 입력 4.7 V에서 입출력 전압 차 1.4 V")]
    draft = NoteDraft(title="LDO 노트", summary="요약", learnings=[NoteLine(text="교훈", evidence=["E1"]),
                                                                    NoteLine(text="일반 지식")],
                      apply_next=[NoteApply(text="할 일", why="이유", evidence=["E1"], guide_id="G-0003")])
    note = ResearchNote(**draft.model_dump(), id="N-0001", author_kind="ai", written_by="AI (fake)",
                        created_at="2026-11-06T00:00:00Z", updated_at="2026-11-06T00:00:00Z", evidence=ev)
    md = to_markdown(note)
    assert md.startswith("# N-0001 LDO 노트") and "AI가 쓴 초안입니다" in md
    assert "- 교훈 (근거: E1 계산)" in md and "- 일반 지식 (근거 없음: AI의 일반 지식, 확인 필요)" in md
    assert "→ 설계 지침 G-0003" in md and "- E1 [계산] 최저 입력 4.7 V" in md
    assert keep_known(["e1", "[E1]", "E7", None], {"E1"}) == ["E1"]


def test_ai_writes_a_draft_note_from_the_apps_evidence(tmp_path):
    client, created, pid, board = i2c_board(tmp_path)
    assert client.post(board + "/notes/ai", json={}).json()["error_code"] == "AI_DISABLED"
    enable(client)
    preview = client.get(board + "/notes/preview").json()
    assert preview["evidence"] > 5 and {"calc", "decision", "requirement"} <= set(preview["evidence_kinds"])

    job = wait(client, client.post(board + "/notes/ai", json={"focus": "드롭아웃"}).json())
    assert job["status"] == "succeeded", job
    page = client.get(f"/api/notes/{job['result']['note_id']}").json()
    note = page["note"]
    assert note["status"] == "draft" and note["author_kind"] == "ai"
    assert note["written_by"] == "AI (gemini-flash-latest)"
    assert note["context"]["project_name"] == "I2C Sensor" and note["context"]["revision"] == 1
    assert note["topics"] == ["ldo"]  # an unknown topic is dropped
    assert note["checks"][0]["evidence"] == ["E1", "E2"]  # E99 does not exist; e2 is read as E2
    assert note["learnings"][1]["text"].endswith("[쪽 번호 근거 없음: 원문 확인 필요]")  # E1 has no page 99
    assert not note["learnings"][0]["text"].endswith("확인 필요]")
    ev = {e["id"]: e for e in note["evidence"]}
    assert ev["E1"]["kind"] == "calc" and "[규칙 " in ev["E1"]["text"]  # the most severe calculated check first
    md = page["markdown"]
    for section in ("## 요약", "## 배경", "## 확인한 것", "## 배운 점·깨달은 점", "## 다음 설계에 적용할 것",
                    "## 열린 질문 (확인할 것)", "## 근거"):
        assert section in md, section
    assert "(근거 없음: AI의 일반 지식, 확인 필요)" in md

    fake = next(c for c in created if isinstance(c, FakeProvider))
    prompt = fake.calls[-1]["prompt"]
    assert "[근거]" in prompt and "E1 [calc]" in prompt and "[이번 노트에서 특히 다룰 것]\n드롭아웃" in prompt
    assert "dec-norev" in prompt  # the design decision is evidence
    assert fake.calls[-1]["schema"]["required"][0] == "title"
    runs = list((tmp_path / "notes" / "ai_runs").glob("*.json"))
    assert len(runs) == 1 and "AIza" not in runs[0].read_text(encoding="utf-8")


def test_people_edit_review_and_turn_a_lesson_into_a_guide(tmp_path):
    client, _, pid, board = i2c_board(tmp_path)
    enable(client)
    note_id = wait(client, client.post(board + "/notes/ai", json={}).json())["result"]["note_id"]
    note = client.get(f"/api/notes/{note_id}").json()["note"]

    # Edit one lesson: that line becomes the person's; untouched lines stay the AI's with their evidence.
    draft = {k: note[k] for k in ("title", "summary", "background", "parts", "topics", "checks", "learnings",
                                  "apply_next", "open_questions")}
    draft["learnings"][2] = {"text": "PTC 저항은 온도에 따라 바뀌어 드롭아웃 계산에 넣어야 한다.", "evidence": ["E1"],
                             "by": "ai"}
    edited = client.put(f"/api/notes/{note_id}", json={"draft": draft, "by": "김엔지니어"}).json()["note"]
    assert edited["version"] == 2 and edited["edited_by"] == ["김엔지니어"]
    assert [x["by"] for x in edited["learnings"]] == ["ai", "ai", "person"]
    assert edited["learnings"][0]["evidence"] == ["E1"] and edited["learnings"][2]["evidence"] == ["E1"]
    history = client.get(f"/api/notes/{note_id}").json()["history"]
    assert [h["event"] for h in history] == ["created", "edited"] and history[1]["before"]["learnings"][2]["text"] \
        .startswith("PTC는")

    # Drafts can be deleted; a reviewed note cannot.
    assert client.post(f"/api/notes/{note_id}/review", json={"by": ""}).json()["error_code"] == "NAME_REQUIRED"
    reviewed = client.post(f"/api/notes/{note_id}/review", json={"by": "김엔지니어"}).json()["note"]
    assert reviewed["status"] == "reviewed" and reviewed["reviewed_by"] == "김엔지니어"
    assert client.delete(f"/api/notes/{note_id}").json()["error_code"] == "NOTE_REVIEWED"

    # "Apply next time" -> a design-guide candidate (AI text is never adopted directly), linked back.
    r = client.post(f"/api/notes/{note_id}/apply/0/guide", json={"by": "김엔지니어"}).json()
    guide = r["guide"]
    assert guide["status"] == "candidate" and guide["rule"].startswith("LDO 입력 최저 전압")
    assert guide["sources"][0]["text"].startswith(f"연구노트 {note_id}") and guide["topics"] == ["ldo"]
    assert r["note"]["apply_next"][0]["guide_id"] == guide["id"] and f"→ 설계 지침 {guide['id']}" in r["markdown"]
    assert client.post(f"/api/notes/{note_id}/apply/0/guide", json={}).json()["error_code"] == "ALREADY_GUIDE"
    missing = client.post(f"/api/notes/{note_id}/apply/5/guide", json={}).json()
    assert missing["error_code"] == "NOTE_ITEM_NOT_FOUND"

    # A person's own note is theirs and counts as reviewed.
    own = {"title": "I2C 풀업은 버스에 하나만", "learnings": [{"text": "센서 모듈에도 풀업이 있으면 병렬이 된다."}]}
    mine = client.post("/api/notes", json={"by": "박팀장", "project_id": pid, "board_id": "a", "revision": 1,
                                           "draft": own}).json()["note"]
    assert mine["status"] == "reviewed" and mine["author_kind"] == "person" and mine["learnings"][0]["by"] == "person"
    assert mine["context"]["board_name"]

    listed = client.get("/api/notes", params={"q": "풀업 병렬"}).json()["notes"]
    assert [n["id"] for n in listed] == [mine["id"]]
    assert len(client.get(board + "/notes").json()["notes"]) == 2
    assert [n["id"] for n in client.get("/api/notes", params={"status": "reviewed"}).json()["notes"]] == \
        [mine["id"], note_id]
    export = client.get("/api/notes-export.md")
    assert export.headers["content-disposition"].endswith('"research-notes.md"')
    assert "## " + note_id in export.text and "## " + mine["id"] in export.text
    assert client.get(f"/api/notes/{mine['id']}/markdown").text.startswith(f"# {mine['id']} I2C 풀업은 버스에 하나만")


def test_ai_review_can_write_the_note_too(tmp_path):
    client, created, _, board = i2c_board(tmp_path)
    enable(client)
    job = wait(client, client.post(board + "/ai/review", json={"write_note": True}).json())
    assert job["status"] == "succeeded", job
    assert job["result"]["review"]["findings"][0]["title"] == "드롭아웃 여유 부족"
    note = client.get(f"/api/notes/{job['result']['note']['id']}").json()["note"]
    assert any(e["kind"] == "ai_review" and "드롭아웃 여유 부족" in e["text"] for e in note["evidence"])
    fake = next(c for c in created if isinstance(c, FakeProvider))
    assert len(fake.calls) == 2 and "AI 검토 warning · 드롭아웃 여유 부족" in fake.calls[1]["prompt"]
    # Without the option, a review writes no note.
    wait(client, client.post(board + "/ai/review", json={}).json())
    assert len(client.get(board + "/notes").json()["notes"]) == 1
