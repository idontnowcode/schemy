import { describe, expect, it } from "vitest";

import { lineWarnings } from "./components/BomAvailabilityCard";
import { fetchHint } from "./components/DatasheetsTab";
import type { BomOffer, DatasheetComponentRow, DatasheetReport } from "./types";

const row = (over: Partial<DatasheetComponentRow>): DatasheetComponentRow => ({
  id: "u1", reference: "U1", value: "X", symbol_id: "Lib:X", footprint_id: null, manufacturer: null,
  manufacturer_part_number: null, category: "ic", category_label: "IC", identity: "mpn", relevant: true,
  symbol_datasheet_url: null, can_fetch: false, candidates: [], searched: null, datasheet: null, checks: null, notes: [],
  uncertain: [], ...over,
});
const report = (components: DatasheetComponentRow[], over: Partial<DatasheetReport> = {}): DatasheetReport => ({
  revision: 1, proposal_id: null, components, fetchable: [], library_available: true,
  search: { mouser: false, digikey: false }, ...over,
});

describe("fetchHint", () => {
  it("explains why nothing can be fetched", () => {
    const cand = { id: "ki-1", source: "kicad_symbol" as const, label: "KiCad 심볼 링크", urls: ["u"], manufacturer: null,
      part_number: null, description: null, product_url: null, status: null };
    const text = (r: DatasheetReport) => fetchHint(r)?.text;
    expect(fetchHint(report([row({})], { fetchable: [{ ...cand, url: "u", references: ["U1"], symbol_ids: [] }] }))).toBeNull();
    expect(text(report([row({})], { library_available: false }))).toContain("심볼 라이브러리를 찾지 못해");
    expect(text(report([row({ identity: "generic" })]))).toContain("받을 부품이 없습니다");
    expect(text(report([row({})]))).toContain("U1: 받을 곳을 찾지 못했습니다");
    expect(text(report([row({})]))).toContain("Mouser·DigiKey 키를 넣거나");
    expect(fetchHint(report([row({})]))?.done).toBe(false);
    expect(text(report([row({})], { search: { mouser: true, digikey: false } }))).toContain("「유통사에서 찾기」");
    // Everything is already in the library: a success, not a warning.
    const all = fetchHint(report([row({ symbol_datasheet_url: "u", datasheet: {} as never })]));
    expect(all?.text).toContain("이미 받았습니다");
    expect(all?.done).toBe(true);
  });
});

import { diffFieldLabel, diffSummaryText, lifecycleTone, priceText, proposalSourceLabel, proposalStatusLabel } from "./format";

describe("proposal labels", () => {
  it("names statuses, sources and diff fields", () => {
    expect(proposalStatusLabel("ready")).toEqual({ text: "승인 대기", tone: "warn" });
    expect(proposalStatusLabel("stale").text).toBe("기준 회로 바뀜");
    expect(proposalSourceLabel("ai_design")).toBe("AI 설계");
    expect(diffFieldLabel("value")).toBe("값");
    expect(diffFieldLabel("pin 5 no_connect")).toBe("핀 5 미연결 표시");
    expect(diffSummaryText({ components_added: 2, connections_changed: 4, properties_changed: 0 })).toBe("부품 +2 · 연결 4");
    expect(diffSummaryText({})).toBe("바뀐 것 없음");
    expect(diffSummaryText({ components_added: 2, notes_changed: 2 })).toBe("부품 +2 · 주석 2");
  });
});

describe("BOM stock and price", () => {
  const offer = (over: Partial<BomOffer>): BomOffer => ({
    source: "Mouser", manufacturer: "TI", part_number: "LM1117MPX-3.3", distributor_part: null, stock: 1000,
    availability: null, price: 0.62, price_qty: 1, currency: "USD", lifecycle: "Active", product_url: null, ...over,
  });
  const line = (offers: BomOffer[], quantity = 2) => ({ references: "U1 U2", quantity, mpn: "LM1117MPX-3.3", manufacturer: "TI",
    offers, errors: [] });

  it("flags short stock and parts going away", () => {
    expect(lineWarnings(line([offer({})]))).toEqual([]);
    expect(lineWarnings(line([offer({ stock: 1 }), offer({ source: "DigiKey", stock: 0 })]))).toEqual(["재고 부족 (필요 2개)"]);
    expect(lineWarnings(line([offer({ stock: 1 }), offer({ source: "DigiKey", stock: 5 })]))).toEqual([]); // one has enough
    expect(lineWarnings(line([offer({ lifecycle: "Obsolete" })]))).toEqual(["단종·생산 종료"]);
    expect(lineWarnings(line([offer({ lifecycle: "Not Recommended for New Designs" })]))).toEqual(["신규 설계 비추천"]);
    expect(lineWarnings(line([]))).toEqual(["유통사에서 찾지 못함"]);
    expect(lineWarnings({ ...line([]), errors: [{ source: "Mouser", error_code: "SEARCH_QUOTA", message: "x" }] })).toEqual([]);
  });

  it("words prices and lifecycle", () => {
    expect(priceText(2.35, "USD", 1)).toBe("$2.35");
    expect(priceText(1.9, "USD", 10)).toBe("$1.9 (10개~)");
    expect(priceText(0.5, "EUR", 1)).toBe("0.5 EUR");
    expect(priceText(null, "USD", 1)).toBe("—");
    expect(lifecycleTone("Active")).toBe("ok");
    expect(lifecycleTone("NRND")).toBe("warn");
    expect(lifecycleTone("End of Life")).toBe("bad");
    expect(lifecycleTone(null)).toBe("muted");
  });
});
