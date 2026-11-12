"""Datasheets as a step of the AI design: after the parts are chosen their datasheets are downloaded and
summarized by the AI, and the summaries reach the pin connection, the proposal's checks and the review.

Downloads use a fake fetcher and the AI is scripted (no internet, no key)."""

import io
import json

import pypdf

from backend.datasheet_flow import MAX_SUMMARY_PAGES, select_pages
from backend.datasheets import DatasheetError

from .backend_helpers import new_project
from .conftest import FIXTURES
from .pdfs import SN65HVD230_TEST_PDF_LINES, make_pdf
from .test_ai import ai_client, enable, wait
from .test_design import REQUIREMENTS, finding, reviews_in_turn, scripted

SUMMARY = (FIXTURES / "datasheets" / "sn65hvd230-test.md").read_text(encoding="utf-8")
PDF = make_pdf(SN65HVD230_TEST_PDF_LINES)


def pages_pdf(pages: list[list[str]]) -> bytes:
    writer = pypdf.PdfWriter()
    for lines in pages:
        writer.add_page(pypdf.PdfReader(io.BytesIO(make_pdf(lines))).pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def flow_client(tmp_path, fetcher=None, summary=SUMMARY, design=None, searchers=None):
    """A client whose AI designs the CAN node and, asked for a datasheet summary, answers ``summary``."""
    design, fetched = design or scripted(bad_first=False), []

    def reply(prompt):
        return summary if "데이터시트 요약 MD v0.1" in prompt else design(prompt)

    def fake(url):
        fetched.append(url)
        if fetcher:
            return fetcher(url)
        return PDF, url

    client, created = ai_client(tmp_path, reply=reply, fetcher=fake, searchers=searchers)
    enable(client)
    pid = new_project(client, "CAN Node")
    client.put(f"/api/projects/{pid}/requirements", json={"text": REQUIREMENTS})
    return client, created, fetched, f"/api/projects/{pid}/boards/a"


def prompts(created) -> list[dict]:
    return [c for p in created if not isinstance(p, dict) for c in p.calls]


def test_a_long_datasheet_is_cut_to_the_pages_the_checks_read():
    filler = ["Functional description, continued."]
    pages = [["SN65HVD230 CAN transceiver", "Features"], ["Description"], *[filler] * 38]
    pages[11] = ["6.1 Absolute Maximum Ratings", "VCC -0.3 6 V"]
    pages[20] = ["5 Pin Configuration and Functions", "1 D", "2 GND"]
    pages[30] = ["The absolute maximum ratings apply.", "9.2 Typical Application"]
    pages[34] = ["9.2.1.2 Detailed Design Procedure", "Inductor selection"]  # where the external parts are chosen
    cut, kept, total = select_pages(pages_pdf(pages))
    assert total == 40 and kept[:2] == [1, 2]
    # A table page brings the next page along (tables run on); a filler page is left out.
    assert {12, 13, 21, 22, 31, 35} <= set(kept) and 5 not in kept and len(kept) <= MAX_SUMMARY_PAGES
    assert len(pypdf.PdfReader(io.BytesIO(cut)).pages) == len(kept)
    # Short enough: sent whole. Unreadable: sent whole, and the AI call decides.
    assert select_pages(PDF) == (PDF, None, 1)
    assert select_pages(b"%PDF-1.4 broken") == (b"%PDF-1.4 broken", None, None)


def test_design_fetches_summarizes_and_uses_the_datasheet(tmp_path):
    client, created, fetched, board = flow_client(tmp_path)
    job = wait(client, client.post(board + "/ai/design", json={"datasheets": True}).json())
    assert job["status"] == "succeeded", job
    steps = [s["step"] for s in job["progress"]]
    assert steps.index("parts") < steps.index("datasheets") < steps.index("pins")  # before the pins are connected
    texts = [s["text"] for s in job["progress"] if s["step"] == "datasheets"]
    assert any(t.startswith("데이터시트 받는 중: SN65HVD230DR (www.ti.com)") for t in texts)
    assert any(t.startswith("AI 요약 중: SN65HVD230DR") for t in texts)
    assert texts[-1] == "데이터시트: 대상 1종 · 요약 있음 1 (새로 받음 1, 새로 요약 1)"
    assert len(fetched) == 1 and "sn65hvd230" in fetched[0].lower()

    calls = prompts(created)
    summary_call = next(c for c in calls if "데이터시트 요약 MD v0.1" in c["prompt"])
    assert [a.name for a in summary_call["attachments"]] == ["sn65hvd230.pdf"]
    assert "[쪽 안내]" not in summary_call["prompt"]
    # The pins are connected with what the datasheet says.
    connect = next(c for c in calls if "핀 표만 써서" in c["prompt"])
    assert "데이터시트 요약:" in connect["prompt"]

    p = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    row = p["ai"]["datasheets"]["parts"][0]
    assert (row["references"], row["download"], row["summary"]) == (["U1"], "downloaded", "made")
    assert p["ai"]["datasheets"]["ready"] == 1 and p["ai"]["datasheets"]["fetch"] is True
    # The proposal's own check compared U1 with the new summary, and the review sees it as evidence.
    checked = p["evidence"]["datasheets"]
    assert [d["reference"] for d in checked] == ["U1"] and checked[0]["reviewed"] is False
    review = next(c for c in calls if "회로 설계 검토자" in c["prompt"])
    assert "데이터시트 U1" in review["prompt"]

    # The library keeps it: the next design downloads nothing and asks for no summary.
    before = len([c for c in prompts(created) if "데이터시트 요약 MD v0.1" in c["prompt"]])
    again = wait(client, client.post(board + "/ai/design", json={"datasheets": True, "review": False}).json())
    assert again["status"] == "succeeded" and len(fetched) == 1
    assert len([c for c in prompts(created) if "데이터시트 요약 MD v0.1" in c["prompt"]]) == before
    last = [s["text"] for s in again["progress"] if s["step"] == "datasheets"][-1]
    assert last == "데이터시트: 대상 1종 · 요약 있음 1"


def test_without_the_option_nothing_is_downloaded_or_sent(tmp_path):
    client, created, fetched, board = flow_client(tmp_path)
    job = wait(client, client.post(board + "/ai/design", json={"review": False}).json())
    assert job["status"] == "succeeded" and fetched == []
    assert not any("데이터시트 요약 MD v0.1" in c["prompt"] for c in prompts(created))
    texts = [s["text"] for s in job["progress"] if s["step"] == "datasheets"]
    assert texts[-1] == "데이터시트: 대상 1종 · 요약 있음 0 · 없음 1 (U1 SN65HVD230DR) — 자동 받기가 꺼져 있습니다"
    p = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    assert p["ai"]["datasheets"]["parts"][0]["download"] == "off" and p["evidence"]["datasheets"] == []


def test_a_failed_download_or_summary_does_not_stop_the_design(tmp_path):
    def blocked(url):
        raise DatasheetError("DATASHEET_HTTP_ERROR", "서버가 403으로 답했습니다.", "브라우저에서 받아 올리세요.")

    client, _, fetched, board = flow_client(tmp_path, fetcher=blocked)
    job = wait(client, client.post(board + "/ai/design", json={"datasheets": True, "review": False}).json())
    assert job["status"] == "succeeded" and len(fetched) >= 1
    p = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()
    row = p["ai"]["datasheets"]["parts"][0]
    assert row["download"] == "failed" and "403" in row["message"] and row["summary"] is None
    assert [s["text"] for s in job["progress"] if s["step"] == "datasheets"][-1].endswith("없음 1 (U1 SN65HVD230DR)")

    # A summary the app cannot read is reported on the part; the PDF stays for a person to summarize.
    client, _, _, board = flow_client(tmp_path / "b", summary="# 데이터시트: 표가 없는 답\n")
    job = wait(client, client.post(board + "/ai/design", json={"datasheets": True, "review": False}).json())
    assert job["status"] == "succeeded"
    row = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()["ai"]["datasheets"]["parts"][0]
    assert row["download"] == "downloaded" and row["summary"] == "failed" and "읽지 못했습니다" in row["message"]


def test_a_long_datasheet_is_summarized_from_its_chosen_pages(tmp_path):
    pages = [["SN65HVD230 CAN transceiver"], ["Description"], *[["More text."]] * 30]
    pages[10] = ["Absolute Maximum Ratings"]
    long_pdf = pages_pdf(pages)
    client, created, _, board = flow_client(tmp_path, fetcher=lambda url: (long_pdf, url))
    job = wait(client, client.post(board + "/ai/design", json={"datasheets": True, "review": False}).json())
    assert job["status"] == "succeeded"
    call = next(c for c in prompts(created) if "데이터시트 요약 MD v0.1" in c["prompt"])
    assert "[쪽 안내]" in call["prompt"] and "원본 32쪽 중 다음 쪽만" in call["prompt"]
    assert "1, 2, 11, 12" in call["prompt"]
    assert len(pypdf.PdfReader(io.BytesIO(call["attachments"][0].data)).pages) == 4
    row = client.get(f"{board}/proposals/{job['result']['proposal_id']}").json()["ai"]["datasheets"]["parts"][0]
    assert row["pages_sent"] == [1, 2, 11, 12] and row["pages"] == 32
    assert any("32쪽 중 4쪽" in s["text"] for s in job["progress"])
    assert json.dumps(row, ensure_ascii=False)  # plain data, stored on the proposal


def test_a_revision_goes_through_the_same_step(tmp_path):
    """The automatic review's revisions carry the option: a revised proposal is checked against the same
    summaries (and a part a revision adds would be fetched), without downloading or summarizing twice."""
    design, _ = reviews_in_turn([finding("warning", "종단 저항 값")], [])
    client, created, fetched, board = flow_client(tmp_path, design=design)
    job = wait(client, client.post(board + "/ai/design", json={"datasheets": True}).json())
    auto = wait(client, client.post(board + "/ai/auto-review", json={
        "proposal_id": job["result"]["proposal_id"], "max_warnings": 0, "datasheets": True}).json())
    assert auto["status"] == "succeeded" and auto["result"]["stop"] == "met", auto
    revised = client.get(f"{board}/proposals/{auto['result']['proposal_id']}").json()
    assert revised["id"] != job["result"]["proposal_id"]
    assert [(r["references"], r["download"], r["summary"]) for r in revised["ai"]["datasheets"]["parts"]] == [
        (["U1"], "had", "had")]
    assert [d["reference"] for d in revised["evidence"]["datasheets"]] == ["U1"]
    assert len(fetched) == 1 and len([c for c in prompts(created) if "데이터시트 요약 MD v0.1" in c["prompt"]]) == 1


def test_only_ics_and_specific_parts_are_fetched():
    """Found in a real-flow run: a ferrite bead with a part number (generic Device symbol, no category) was listed
    as a part whose datasheet could not be got."""
    from backend.datasheet_flow import DatasheetPrep
    from circuit_core.models import Component

    def comp(ref, symbol, mpn):
        return Component(id=ref.lower(), reference=ref, value=mpn, symbol_id=symbol, manufacturer="Maker",
                         manufacturer_part_number=mpn, pins=[{"number": "1"}, {"number": "2"}])

    prep = DatasheetPrep(library=None, symbol_url=lambda s: None, fetcher=None, searchers=[], provider=None,
                         cfg=None, job_id="j", step=lambda t: None, fetch=False)
    groups = prep._groups([comp("FB1", "Device:FerriteBead_Small", "BLM18PG221SN1D"),
                           comp("R1", "Device:R", "RC0603FR-0710KL"),
                           comp("D1", "Diode:BAT54S", "BAT54S"),
                           comp("U1", "Interface_CAN_LIN:SN65HVD230", "SN65HVD230DR"),
                           comp("Q1", "Transistor_FET:BSS138", "BSS138")])
    assert sorted(c.reference for v in groups.values() for c in v) == ["Q1", "U1"]


def test_an_external_part_that_differs_says_what_the_circuit_has():
    """Found in a real-flow run: 「다이오드 1N5824 (Output ~ Ground)」 read as a missing diode; the board had a
    1N5822 there (the datasheet's figure names one example part)."""
    from backend.proposals import _external_text

    row = {"kind_label": "다이오드", "value": "1N5824", "endpoints": ["Output", "Ground"], "found": ["D7 1N5822"],
           "status": "value_differs", "reason": "값이 1N5824가 아닙니다."}
    assert _external_text(row) == "다이오드 1N5824 (Output ~ Ground): 회로에는 D7 1N5822 (값 다름)"
    assert _external_text({**row, "found": [], "status": "missing"}) == "다이오드 1N5824 (Output ~ Ground): 회로에 없음"
    assert _external_text({**row, "value": None, "status": "short", "reason": "2개 필요, 1개 있음"}) == (
        "다이오드 (Output ~ Ground): 2개 필요, 1개 있음")


def test_distributors_are_asked_only_with_the_option_and_their_answer_comes_first(tmp_path):
    """With distributor keys the step searches by part number (step 3 says so). Found in a real-flow run: a test
    launcher whose key store answered every name made the app search Mouser for real — so the option, not the
    presence of a key, is what this test pins down."""
    from circuit_core.datasheet_sources import make_candidate

    asked = []

    class Distributor:
        name = "Mouser"

        def search(self, term):
            asked.append(term)
            return [make_candidate("mouser", "Mouser", ["https://distributor.example/sn65hvd230dr.pdf"],
                                   part_number=term)]

    client, _, fetched, board = flow_client(tmp_path, searchers=lambda keys: [Distributor()])
    off = wait(client, client.post(board + "/ai/design", json={"review": False}).json())
    assert off["status"] == "succeeded" and asked == [] and fetched == []
    on = wait(client, client.post(board + "/ai/design", json={"review": False, "datasheets": True}).json())
    assert on["status"] == "succeeded" and asked == ["SN65HVD230DR"]
    assert fetched == ["https://distributor.example/sn65hvd230dr.pdf"]  # the exact part's document, tried first
