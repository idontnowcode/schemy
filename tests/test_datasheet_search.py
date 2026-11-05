"""Finding datasheets: maker address rules, Mouser / DigiKey search (fake servers), fetch with fallback,
and the datasheet check of a proposal before it is approved."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from backend import Settings, create_app
from backend.datasheet_search import DigiKeySearch, MouserSearch, searchers
from backend.datasheets import DatasheetError, DatasheetLibrary
from circuit_core.datasheet_sources import (
    is_public_url,
    maker_candidate,
    maker_of,
    part_ladder,
    url_part_name,
    without_voltage_code,
)
from circuit_core.models import Component, Pin

from .backend_helpers import TOKEN, new_project, save_ir
from .circuits import can_node
from .conftest import SYMBOL_DIR
from .pdfs import SN65HVD230_TEST_PDF_LINES, make_pdf
from .test_ai import MemoryKeyStore, wait

PDF = make_pdf(SN65HVD230_TEST_PDF_LINES)
DK_URL = "https://www.ti.com/lit/ds/symlink/sn65hvd230.pdf?ts=1"
MOUSER_URL = "https://www.mouser.com/datasheet/2/405/sn65hvd230-1.pdf"
PIN = (Pin(number="1", name="A"),)


# --- address rules ---------------------------------------------------------------------------------

def test_part_ladder_and_makers():
    assert part_ladder("DRV8871DDAR") == ["DRV8871DDAR", "DRV8871DDA", "DRV8871DD", "DRV8871"]
    assert part_ladder("DRV8871DDAR", core_first=True)[0] == "DRV8871"
    assert part_ladder("STM32F103C8Tx") == ["STM32F103C8T", "STM32F103C8", "STM32F103C", "STM32F103"]
    assert part_ladder("LM1117MPX-3.3")[0] == "LM1117MPX" and part_ladder("ab") == []
    assert maker_of("Texas Instruments Incorporated").key == "ti"
    assert maker_of("STMicroelectronics N.V.").key == "st" and maker_of("ON Semiconductor").key == "onsemi"
    assert maker_of("Nexperia USA Inc.").key == "nexperia" and maker_of("Diodes Inc").key == "diodes"
    assert maker_of(None, "http://www.ti.com/lit/ds/symlink/lm1117.pdf").key == "ti"
    assert maker_of("Murata", "https://www.murata.com/x.pdf") is None

    drv = Component(id="u1", reference="U1", value="DRV8871", symbol_id="Driver_Motor:DRV8871DDA", pins=PIN,
                    manufacturer="Texas Instruments", manufacturer_part_number="DRV8871DDAR")
    cand = maker_candidate(drv)
    assert cand.source == "maker_site" and cand.urls[0] == "https://www.ti.com/lit/gpn/drv8871"
    assert len(cand.urls) <= 6 and all(u.startswith("https://www.ti.com/lit/gpn/") for u in cand.urls)
    st = Component(id="u2", reference="U2", value="STM32", symbol_id="MCU_ST_STM32F1:STM32F103C8Tx", pins=PIN,
                   manufacturer="STMicroelectronics", manufacturer_part_number="STM32F103C8T6")
    assert "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf" in maker_candidate(st).urls
    generic = Component(id="r1", reference="R1", value="10k", symbol_id="Device:R", pins=PIN)
    assert maker_candidate(generic) is None
    # TI files fixed-voltage parts under the family (checked on ti.com: tps7a02.pdf, tlv700.pdf).
    ldo = Component(id="u3", reference="U3", value="TPS7A0233", symbol_id="Regulator_Linear:TPS7A0233PDBV", pins=PIN,
                    manufacturer="Texas Instruments", manufacturer_part_number="TPS7A0233PDBVR")
    assert maker_candidate(ldo).urls[:2] == ["https://www.ti.com/lit/gpn/tps7a0233", "https://www.ti.com/lit/gpn/tps7a02"]
    assert without_voltage_code("TLV70033") == "TLV700" and without_voltage_code("DRV8871") is None
    assert url_part_name("https://www.onsemi.com/pdf/datasheet/mc7805-d.pdf") == "MC7805"


def test_rule_built_links_must_name_the_part(tmp_path):
    lib = DatasheetLibrary(tmp_path)
    other = make_pdf(["LM317 3-Terminal Adjustable Regulator", "Pin 1 ADJ"])
    with pytest.raises(DatasheetError) as exc:
        lib.add(other, original_name="x.pdf", source="maker_site", part_numbers=["TPS7A0233PDBVR"], expect=["TPS7A02"])
    assert exc.value.code == "DATASHEET_WRONG_PART" and lib.entries() == []
    family = make_pdf(["TPS7A02 Nanopower IQ, 200-mA, Low-Dropout Regulator", "Device options"])
    entry, _ = lib.add(family, original_name="tps7a02.pdf", source="maker_site", part_numbers=["TPS7A0233PDBVR"],
                       expect=["TPS7A02"])
    assert entry.identity["status"] == "family" and entry.identity["found"] == ["TPS7A02"]


def test_only_public_links_from_outside():
    assert is_public_url("https://www.ti.com/lit/gpn/drv8871")
    for bad in ("file:///etc/passwd", "https://127.0.0.1/x.pdf", "https://localhost/x.pdf", "https://intranet/x.pdf",
                "https://user:pw@www.ti.com/x.pdf", "ftp://www.ti.com/x.pdf", "https://[::1]/x.pdf",
                "https://nas.local/x.pdf"):
        assert not is_public_url(bad), bad


# --- fake distributors -----------------------------------------------------------------------------

class FakeDistributors:
    """api.mouser.com and api.digikey.com, just the two calls the app makes."""

    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.mouser_key = "m-key"
        self.dk = ("dk-id", "dk-secret")
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                url = urlparse(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                if url.path == "/api/v1/search/partnumber":
                    body = json.loads(raw)
                    fake.requests.append(("mouser", {"query": parse_qs(url.query), "body": body}))
                    if parse_qs(url.query).get("apiKey") != [fake.mouser_key]:
                        error = {"Code": "Invalid", "Message": "Invalid unique identifier."}
                        return self.reply(200, {"Errors": [error], "SearchResults": None})
                    part = body["SearchByPartRequest"]["mouserPartNumber"]
                    return self.reply(200, {"Errors": [], "SearchResults": {"NumberOfResult": 2, "Parts": [
                        {"ManufacturerPartNumber": "SN65HVD231DR", "Manufacturer": "Texas Instruments",
                         "DataSheetUrl": "https://www.mouser.com/datasheet/2/405/other.pdf"},
                        {"ManufacturerPartNumber": part, "Manufacturer": "Texas Instruments",
                         "DataSheetUrl": MOUSER_URL, "Description": "CAN Interface IC 3.3V CAN Transceiver",
                         "ProductDetailUrl": "https://www.mouser.com/ProductDetail/595-SN65HVD230DR",
                         "LifecycleStatus": None}]}})
                if url.path == "/v1/oauth2/token":
                    form = {k: v[0] for k, v in parse_qs(raw).items()}
                    fake.requests.append(("dk-token", form))
                    if (form.get("client_id"), form.get("client_secret")) != fake.dk:
                        return self.reply(401, {"error": "invalid_client"})
                    return self.reply(200, {"access_token": "dk-at", "expires_in": 599, "token_type": "Bearer"})
                if url.path == "/products/v4/search/keyword":
                    fake.requests.append(("dk-search", {"headers": dict(self.headers), "body": json.loads(raw)}))
                    if self.headers.get("Authorization") != "Bearer dk-at" or \
                            self.headers.get("X-DIGIKEY-Client-Id") != fake.dk[0]:
                        return self.reply(401, {"title": "Unauthorized", "status": 401, "detail": "Bad token"})
                    return self.reply(200, {"ProductsCount": 1, "ExactMatches": [
                        {"ManufacturerProductNumber": "SN65HVD230DR",
                         "Manufacturer": {"Id": 296, "Name": "Texas Instruments"},
                         "DatasheetUrl": DK_URL, "ProductUrl": "https://www.digikey.com/en/products/detail/x",
                         "Description": {"ProductDescription": "IC TRANSCEIVER HALF 1/1 8SOIC"},
                         "ProductStatus": {"Id": 0, "Status": "Active"}}], "Products": [
                        {"ManufacturerProductNumber": "SN65HVD230DR", "DatasheetUrl": "https://127.0.0.1/x.pdf"}]})
                return self.reply(404, {"error": "nope"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture()
def fake():
    f = FakeDistributors()
    yield f
    f.server.shutdown()


def test_mouser_and_digikey_answers(fake):
    mouser = MouserSearch("m-key", base=fake.base).search("SN65HVD230DR")
    assert [c.urls for c in mouser] == [[MOUSER_URL]] and mouser[0].product_url.endswith("595-SN65HVD230DR")
    assert fake.requests[0][1]["body"] == {"SearchByPartRequest": {"mouserPartNumber": "SN65HVD230DR",
                                                                   "partSearchOptions": "None"}}
    with pytest.raises(DatasheetError) as exc:
        MouserSearch("wrong", base=fake.base).search("SN65HVD230DR")
    assert exc.value.code == "SEARCH_AUTH_FAILED"

    dk = DigiKeySearch("dk-id", "dk-secret", base=fake.base)
    found = dk.search("SN65HVD230DR")
    assert [c.urls for c in found] == [[DK_URL]] and found[0].status == "Active"  # the 127.0.0.1 link is dropped
    dk.search("SN65HVD230DR")
    assert [k for k, _ in fake.requests].count("dk-token") == 1  # the token is reused
    search = [r for k, r in fake.requests if k == "dk-search"][0]
    assert search["body"] == {"Keywords": "SN65HVD230DR", "Limit": 10}
    with pytest.raises(DatasheetError) as exc:
        DigiKeySearch("dk-id", "nope", base=fake.base).search("SN65HVD230DR")
    assert exc.value.code == "SEARCH_AUTH_FAILED"


# --- backend ---------------------------------------------------------------------------------------

def client_with(tmp_path, fake, fetcher):
    keys = MemoryKeyStore()
    settings = Settings(workspace=tmp_path, token=TOKEN, symbol_dirs=[SYMBOL_DIR], kicad_factory=lambda: None,
                        keystore=keys, datasheet_fetcher=fetcher,
                        datasheet_searchers=lambda ks: searchers(ks, mouser_base=fake.base, digikey_base=fake.base))
    client = TestClient(create_app(settings))
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client, keys


def test_search_then_fetch_with_fallback_and_proposal_recheck(tmp_path, fake):
    calls = []

    def fetcher(url):
        calls.append(url)
        if url == DK_URL:  # the distributor link works; nothing else is tried
            return PDF, url
        raise DatasheetError("DATASHEET_HTTP_ERROR", f"404: {url}")

    client, keys = client_with(tmp_path, fake, fetcher)
    pid = new_project(client, "CAN")
    save_ir(client, pid, can_node().model_dump(mode="json"))
    board = f"/api/projects/{pid}/boards/a"

    assert client.post(board + "/datasheets/search", json={}).json()["error_code"] == "NO_SEARCH_KEYS"
    assert client.put("/api/datasheet-search/keys/digikey", json={"client_id": "dk-id"}).json()["error_code"] \
        == "KEY_REQUIRED"
    status = client.put("/api/datasheet-search/keys/digikey",
                        json={"client_id": "dk-id", "client_secret": "dk-secret"}).json()
    assert status["digikey"] == {"configured": True, "source": "keyring"} and not status["mouser"]["configured"]
    client.put("/api/datasheet-search/keys/mouser", json={"api_key": "m-key"})
    assert "dk-secret" not in json.dumps(client.get("/api/datasheet-search/keys").json())

    job = wait(client, client.post(board + "/datasheets/search", json={}).json())
    assert job["status"] == "succeeded", job
    assert job["result"]["results"] == [{"term": "SN65HVD230DR", "references": ["U1"], "found": 2, "errors": []}]
    report = client.get(board + "/datasheets").json()
    u1 = next(r for r in report["components"] if r["reference"] == "U1")
    by_id = {f["id"]: f for f in report["fetchable"]}
    assert [by_id[i]["source"] for i in u1["candidates"]] == ["digikey", "mouser", "kicad_symbol", "maker_site"]
    assert u1["searched"]["found"] == 2 and report["search"] == {"mouser": True, "digikey": True}

    # The user picks the maker rule; it fails (404 on every address), then the other candidates are tried in order.
    maker = next(i for i in u1["candidates"] if by_id[i]["source"] == "maker_site")
    job = wait(client, client.post(board + "/datasheets/fetch", json={"candidates": [maker]}).json())
    result = job["result"]["results"][0]
    assert result["ok"] and result["source"] == "digikey" and result["identity"] == "matched", result
    assert [t["source"] for t in result["tried"]] == ["maker_site"] * 4
    entry = client.get("/api/datasheets").json()["entries"][0]
    assert entry["source"] == "digikey" and entry["urls"] == [DK_URL] and entry["part_numbers"] == ["SN65HVD230DR"]
    assert client.post(board + "/datasheets/fetch", json={"candidates": ["mo-0000000000"]}).json()["error_code"] \
        == "URL_NOT_FROM_LIBRARY"

    # A proposal adds a second transceiver part: its report and fetch work on the proposal's circuit.
    u1_pins = next(c for c in can_node().components if c.id == "u1").model_dump(mode="json")["pins"]
    r = client.post(board + "/proposals", json={"title": "U2 추가", "ops": [{"op": "add_component", "component": {
        "id": "u2", "reference": "U2", "value": "SN65HVD231", "symbol_id": "Interface_CAN_LIN:SN65HVD230",
        "manufacturer": "Texas Instruments", "manufacturer_part_number": "SN65HVD231DR", "pins": u1_pins}}]})
    assert r.status_code == 201, r.text
    proposal = r.json()["proposal"]["id"]
    wait(client, r.json()["job"])
    pr = client.get(board + "/datasheets", params={"proposal_id": proposal}).json()
    assert pr["proposal_id"] == proposal and [c["reference"] for c in pr["components"] if c["can_fetch"]] == []
    # U2 shares U1's symbol, so the library entry is found by symbol: nothing more to fetch for it.
    assert next(c for c in pr["components"] if c["reference"] == "U2")["datasheet"]["id"] == entry["id"]
    rechecked = client.post(f"{board}/proposals/{proposal}/recheck-datasheets").json()
    assert rechecked["evidence"]["datasheets"] == []  # no summary yet: nothing to compare

    client.delete("/api/datasheet-search/keys/mouser")
    assert not client.get("/api/datasheet-search/keys").json()["mouser"]["configured"] and "mouser" not in keys.keys


def test_search_errors_are_kept_per_part(tmp_path, fake):
    client, keys = client_with(tmp_path, fake, lambda url: (PDF, url))
    keys.keys["mouser"] = "wrong"
    pid = new_project(client, "CAN")
    save_ir(client, pid, can_node().model_dump(mode="json"))
    board = f"/api/projects/{pid}/boards/a"
    job = wait(client, client.post(board + "/datasheets/search", json={}).json())
    err = job["result"]["results"][0]["errors"][0]
    assert err["source"] == "Mouser" and err["error_code"] == "SEARCH_AUTH_FAILED"
    u1 = next(r for r in client.get(board + "/datasheets").json()["components"] if r["reference"] == "U1")
    assert u1["searched"]["errors"][0]["error_code"] == "SEARCH_AUTH_FAILED" and u1["searched"]["found"] == 0
