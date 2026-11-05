"""Datasheets: part identity, "데이터시트 요약 MD" parsing, pin/rating/external-part checks,
the workspace library and the downloader (against a local HTTP server, never the internet)."""

import http.server
import re
import threading

import pytest

from backend.datasheets import DatasheetError, DatasheetLibrary, fetch_pdf, read_pdf_text
from circuit_core import CircuitIR
from circuit_core.datasheet_checks import check_component, choose_package
from circuit_core.datasheet_md import import_datasheet_markdown
from circuit_core.datasheets import (
    identity,
    identity_check,
    net_voltage,
    part_core,
    pin_names_match,
)

from .circuits import can_node
from .conftest import FIXTURES, ROOT
from .pdfs import SN65HVD230_TEST_PDF_LINES, make_pdf

SUMMARY = (FIXTURES / "datasheets" / "sn65hvd230-test.md").read_text(encoding="utf-8")
PDF = make_pdf(SN65HVD230_TEST_PDF_LINES)


def facts(text=SUMMARY):
    result = import_datasheet_markdown(text)
    assert result.ok, [i.to_dict() for i in result.issues]
    return result.facts


def report(circuit, library, text=SUMMARY):
    u1 = circuit.component("u1")
    pins = [(p.number, p.name) for p in library.get(u1.symbol_id).pins]
    return check_component(circuit, u1, facts(text), pins, [library.properties(u1.symbol_id).get("Description")])


# --- identity ---------------------------------------------------------------------------------

@pytest.mark.parametrize("mpn, core", [("STM32F103C8T6", "STM32F103"), ("LM1117MPX-3.3", "LM1117"),
                                       ("MCP2551-I/SN", "MCP2551"), ("SN65HVD230DR", "SN65HVD230"),
                                       ("ATMEGA328P-AU", "ATMEGA328"), ("STM32F103C8Tx", "STM32F103")])
def test_part_core(mpn, core):
    assert part_core(mpn) == core


def test_identity_check():
    st = "STM32F103x8 STM32F103xB Table 1. Device summary STM32F103C8, STM32F103R8"
    assert identity_check(st, ["STM32F103C8T6"]) == {"status": "family", "found": ["STM32F103C8"],
                                                     "checked": ["STM32F103C8T6"]}
    assert identity_check("SN65HVD230 3.3-V CAN", ["SN65HVD230DR", "SN65HVD230"])["status"] == "matched"
    assert identity_check("MCP2551 High-Speed CAN Transceiver", ["SN65HVD230"])["status"] == "not_found"
    assert identity_check("", ["X1"])["status"] == "no_text"


def test_identity_levels(led_circuit):
    can = can_node()
    assert identity(can.component("u1")) == "mpn"
    no_maker = can.component("u1").model_copy(update={"manufacturer": None, "manufacturer_part_number": None})
    assert identity(no_maker, "http://www.ti.com/lit/ds/symlink/sn65hvd230.pdf") == "symbol"
    assert identity(no_maker) == "generic"  # no link and no part number: nothing to look up
    assert {identity(c) for c in led_circuit.components} == {"generic"}


@pytest.mark.parametrize("name, volts", [("+3V3", 3.3), ("+5V", 5.0), ("VCC_12V", 12.0), ("-5V", -5.0),
                                         ("GND", 0.0), ("AGND", 0.0), ("+1V8", 1.8), ("VBUS_5V", 5.0),
                                         ("VCC", None), ("VBAT", None), ("CAN_V", None)])
def test_net_voltage(name, volts):
    assert net_voltage(name) == volts


def test_pin_names_match():
    assert pin_names_match("VDD_1", "VDD") and pin_names_match("~{RESET}", "RESET#")
    assert pin_names_match("PA0-WKUP/ADC12_IN0", "PA0") and pin_names_match("VSS", "GND")
    assert pin_names_match("Vref", "VREF") and pin_names_match("N.C.", "NC")
    assert not pin_names_match("CANH", "CANL") and not pin_names_match("", "VCC")


def test_symbol_datasheet_links(library):
    assert library.datasheet_url("Interface_CAN_LIN:SN65HVD230") == "http://www.ti.com/lit/ds/symlink/sn65hvd230.pdf"
    # Derived symbols cannot be generated yet, but their link is still readable.
    assert library.datasheet_url("Regulator_Linear:AMS1117-3.3") == "http://www.advanced-monolithic.com/pdf/ds1117.pdf"
    assert library.datasheet_url("Device:R") is None and library.datasheet_url("Nope:X") is None


# --- summary Markdown -------------------------------------------------------------------------

def test_summary_parses():
    result = import_datasheet_markdown(SUMMARY, source_name="t.md")
    assert result.ok and result.stats["warnings"] == 0
    f = result.facts
    assert f.manufacturer == "Texas Instruments" and f.part_numbers == ["SN65HVD230", "SN65HVD231", "SN65HVD232"]
    assert f.packages() == ["SOIC-8 (D)"] and [p.name for p in f.pins][:3] == ["D", "GND", "VCC"]
    assert (f.abs_max[0].min, f.abs_max[0].max, f.abs_max[0].page) == (-0.3, 6.0, 5)
    assert (f.recommended[0].min, f.recommended[0].typ, f.recommended[0].max) == (3.0, 3.3, 3.6)
    assert [(e.requirement, e.kind, e.endpoints) for e in f.external_parts] == [
        ("required", "capacitor", ["VCC", "GND"]), ("recommended", "resistor", ["CANH", "CANL"]),
        ("recommended", "resistor", ["Rs", "GND"])]
    assert f.notes[0].page == 15


def test_documented_prompt_and_example():
    from ai_engine.tasks import datasheet_prompt

    doc = (ROOT.parent / "docs" / "prompts" / "datasheet-summary.md").read_text(encoding="utf-8")
    block = re.search(r"## 프롬프트 본문\n.*?````text\n(.*?)````", doc, re.S).group(1)
    assert datasheet_prompt() == block
    example = re.search(r"## 예시.*?````markdown\n(.*?)````", doc, re.S).group(1)
    result = import_datasheet_markdown(example)
    assert result.ok and result.stats["warnings"] == 0 and result.stats["external_parts"] == 3


def test_summary_errors_and_warnings():
    assert "MISSING_SECTION" in {i.code for i in import_datasheet_markdown("# 데이터시트: x\n").issues}
    dup = SUMMARY.replace("| SOIC-8 (D) | 2 | GND |", "| SOIC-8 (D) | 1 | GND |")
    result = import_datasheet_markdown(dup)
    assert not result.ok and "PIN_DUPLICATE" in {i.code for i in result.issues}
    loose = (SUMMARY.replace("| 버스 전압 | CANH, CANL | -4 | 16 | V | | 5 |",
                             "| 버스 전압 | CANH, CANL | | ±14 | V | | |")
             .replace("| 6 | V |", "| six | V |"))
    result = import_datasheet_markdown(loose)
    assert result.ok
    codes = {i.code for i in result.issues}
    assert {"NUMBER_UNREADABLE", "PAGE_MISSING"} <= codes
    bus = next(r for r in result.facts.abs_max if r.parameter == "버스 전압")
    assert (bus.min, bus.max) == (-14.0, 14.0)


@pytest.mark.parametrize("text, endpoints", [("VCC ~ GND", ["VCC", "GND"]), ("VCC-GND", ["VCC", "GND"]),
                                             ("IN- ~ OUT", ["IN-", "OUT"]), ("IN- - OUT", ["IN-", "OUT"]),
                                             ("VCC", [])])
def test_external_part_endpoints(text, endpoints):
    md = SUMMARY.replace("| VCC ~ GND |", f"| {text} |")
    assert import_datasheet_markdown(md).facts.external_parts[0].endpoints == endpoints


# --- checks ------------------------------------------------------------------------------------

def test_checks_on_a_good_circuit(library):
    r = report(can_node(), library)
    assert r["package"]["chosen"] == "SOIC-8 (D)" and r["pin_names_from"] == "library"
    assert r["pin_map"]["status"] == "match" and r["pin_map"]["counts"] == {"match": 8}
    assert [(x["name"], x["net"], x["voltage"], x["status"]) for x in r["ratings"]["rows"]] == [
        ("VCC", "+3V3", 3.3, "ok")]
    statuses = [(x["kind_label"], x["status"], x["found"]) for x in r["external"]["rows"]]
    assert statuses == [("콘덴서", "present", ["C1 100nF"]), ("저항", "present", ["R1 120"]),
                        ("저항", "direct", [])]  # Rs is tied straight to GND (high-speed mode)
    assert r["external"]["status"] == "ok"


def test_supply_voltage_against_ratings(library):
    assert report(can_node(supply="+5V"), library)["ratings"]["rows"][0]["status"] == "outside_recommended"
    over = report(can_node(supply="+12V"), library)["ratings"]
    assert over["status"] == "error" and over["rows"][0]["status"] == "over_abs_max"
    unknown = report(can_node(supply="VCC"), library)["ratings"]
    assert unknown["rows"][0]["status"] == "unknown_voltage" and unknown["status"] == "unchecked"
    # A voltage written on the net (IR 0.8.0) is used when the name has none.
    data = can_node(supply="VCC").model_dump(mode="json")
    next(n for n in data["nets"] if n["name"] == "VCC")["voltage"] = 3.3
    assert report(CircuitIR.model_validate(data), library)["ratings"]["rows"][0]["status"] == "ok"


def test_missing_and_wrong_external_parts(library):
    r = report(can_node(decoupling="1uF", termination=None), library)["external"]
    assert [x["status"] for x in r["rows"]][:2] == ["value_differs", "missing"]
    assert r["status"] == "warning"  # a required part is wrong
    quantity = SUMMARY.replace("| 필수 | 콘덴서 | 0.1uF | 1 |", "| 필수 | 콘덴서 | 0.1uF | 2 |")
    assert report(can_node(), library, quantity)["external"]["rows"][0]["status"] == "short"


def test_pin_map_mismatch_is_reported(library):
    swapped = (SUMMARY.replace("| SOIC-8 (D) | 6 | CANL |", "| SOIC-8 (D) | 6 | CANH |")
               .replace("| SOIC-8 (D) | 7 | CANH |", "| SOIC-8 (D) | 7 | CANL |"))
    pin_map = report(can_node(), library, swapped)["pin_map"]
    assert pin_map["status"] == "mismatch" and pin_map["counts"] == {"match": 6, "mismatch": 2}
    bad = {r["number"]: (r["symbol_name"], r["datasheet_name"]) for r in pin_map["rows"] if r["status"] == "mismatch"}
    assert bad == {"6": ("CANL", "CANH"), "7": ("CANH", "CANL")}


def _with_package(name, numbers):
    rows = "".join(f"| {name} | {n} | {pin} | 기타 | | 4 |\n" for n, pin in numbers)
    return SUMMARY.replace("\n## 3. 절대", rows + "\n## 3. 절대")


def test_package_choice():
    u1 = can_node().component("u1")
    soic = [(p.number, p.name) for p in facts().pins]
    # 1) pin count: VSON has an exposed pad (9 pins), U1 has 8.
    nine = facts(_with_package("VSON-8 (DRB)", [*soic, ("9", "EP")]))
    assert choose_package(nine, u1, [])["chosen"] == "SOIC-8 (D)"
    # 2) same pin count: the footprint name decides.
    eight = facts(_with_package("VSON-8 (DRB)", soic))
    assert choose_package(eight, u1, [u1.footprint_id]) == {
        "chosen": "SOIC-8 (D)", "options": ["SOIC-8 (D)", "VSON-8 (DRB)"],
        "reason": "풋프린트·설명의 패키지 이름이 같음"}
    # 3) nothing to go on: no guess, the user is asked.
    undecided = choose_package(eight, u1, [])
    assert undecided["chosen"] is None and "고를 수 없습니다" in undecided["reason"]
    assert choose_package(eight, u1, [], override="VSON-8 (DRB)")["chosen"] == "VSON-8 (DRB)"


def test_exposed_pad_pairs_with_numbered_symbol_pin():
    from circuit_core.datasheet_checks import pin_map_check

    f = facts(_with_package("VSON-8 (DRB)", [*[(p.number, p.name) for p in facts().pins], ("EP", "Thermal Pad")]))
    pins = [(p.number, p.name) for p in f.pins_of("SOIC-8 (D)")] + [("9", "EP")]
    rows = pin_map_check(f, "VSON-8 (DRB)", pins)["rows"]
    assert {"number": "9", "datasheet_number": "EP", "symbol_name": "EP", "datasheet_name": "Thermal Pad",
            "kind": "기타", "page": 4, "status": "match"} in rows


# --- library -----------------------------------------------------------------------------------

def test_read_pdf_text():
    text, pages, _ = read_pdf_text(PDF)
    assert "SN65HVD230 3.3-V CAN Bus Transceiver" in text and pages == 1
    with pytest.raises(DatasheetError) as exc:
        read_pdf_text(b"%PDF-1.4\nbroken")
    assert exc.value.code == "DATASHEET_UNREADABLE"


def test_library_add_dedupe_link_find_delete(tmp_path):
    lib = DatasheetLibrary(tmp_path)
    entry, created = lib.add(PDF, original_name="sn.pdf", source="upload", part_numbers=["SN65HVD230DR"])
    assert created and entry.identity["status"] == "family" and entry.page_count == 1
    again, created = lib.add(PDF, original_name="copy.pdf", source="kicad_symbol", url="https://x/sn.pdf",
                             symbols=["Interface_CAN_LIN:SN65HVD230"])
    assert not created and again.id == entry.id and again.urls == ["https://x/sn.pdf"]
    assert again.identity["status"] == "matched"  # the symbol name is in the text
    u1 = can_node().component("u1")
    assert lib.find(u1).id == entry.id
    other, _ = lib.add(make_pdf(["MCP2551 CAN transceiver"]), original_name="mcp.pdf", source="upload",
                       part_numbers=["SN65HVD230DR"])
    # A part number points at one datasheet: the newest upload takes it over; the symbol still links the first.
    assert other.identity["status"] == "not_found"
    assert lib.find(u1).id == other.id
    assert lib.get(entry.id).part_numbers == [] and lib.get(entry.id).symbols == ["Interface_CAN_LIN:SN65HVD230"]
    lib.delete(other.id)
    assert lib.find(u1).id == entry.id and not (tmp_path / "datasheets" / f"{other.id}.pdf").exists()
    with pytest.raises(DatasheetError):
        lib.add(b"<html>not a pdf</html>", original_name="x.pdf", source="upload")


# --- downloader (local server only) ------------------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802
        status, headers, body = self.routes.get(self.path, (404, {}, b"not found"))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    _Handler.routes = {
        "/ds.pdf": (200, {"Content-Type": "application/pdf"}, PDF),
        "/old.pdf": (301, {"Location": "/ds.pdf"}, b""),
        "/product": (200, {"Content-Type": "text/html"}, b"<html>product page</html>"),
        "/huge.pdf": (200, {}, b"%PDF-1.4\n" + b"0" * 5000),
    }
    yield base
    httpd.shutdown()


def test_fetch_pdf(server):
    data, final = fetch_pdf(f"{server}/old.pdf", https_only=False)
    assert data == PDF and final.endswith("/ds.pdf")
    for path, code in (("/product", "DATASHEET_NOT_PDF"), ("/missing.pdf", "DATASHEET_HTTP_ERROR")):
        with pytest.raises(DatasheetError) as exc:
            fetch_pdf(server + path, https_only=False)
        assert exc.value.code == code and exc.value.hint
    with pytest.raises(DatasheetError) as exc:
        fetch_pdf(f"{server}/huge.pdf", https_only=False, max_bytes=1000)
    assert exc.value.code == "DATASHEET_TOO_LARGE"
    # By default http:// links are fetched over HTTPS; this plain-HTTP test server cannot answer that.
    with pytest.raises(DatasheetError) as exc:
        fetch_pdf(f"{server}/ds.pdf", timeout=5)
    assert exc.value.code == "DATASHEET_UNREACHABLE"
    with pytest.raises(DatasheetError) as exc:
        fetch_pdf("file:///etc/passwd")
    assert exc.value.code == "DATASHEET_BAD_URL"
