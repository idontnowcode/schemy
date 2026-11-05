import { describe, expect, it } from "vitest";

import { fetchHint } from "./components/DatasheetsTab";
import type { DatasheetComponentRow, DatasheetReport } from "./types";

const row = (over: Partial<DatasheetComponentRow>): DatasheetComponentRow => ({
  id: "u1", reference: "U1", value: "X", symbol_id: "Lib:X", footprint_id: null, manufacturer: null,
  manufacturer_part_number: null, category: "ic", category_label: "IC", identity: "mpn", relevant: true,
  symbol_datasheet_url: null, can_fetch: false, datasheet: null, checks: null, notes: [], uncertain: [], ...over,
});
const report = (components: DatasheetComponentRow[], over: Partial<DatasheetReport> = {}): DatasheetReport => ({
  revision: 1, components, fetchable: [], library_available: true, ...over,
});

describe("fetchHint", () => {
  it("explains why nothing can be fetched", () => {
    expect(fetchHint(report([row({})], { fetchable: [{ url: "u", references: ["U1"], symbol_ids: [] }] }))).toBeNull();
    expect(fetchHint(report([row({})], { library_available: false }))).toContain("심볼 라이브러리를 찾지 못해");
    expect(fetchHint(report([row({ identity: "generic" })]))).toContain("받을 부품이 없습니다");
    expect(fetchHint(report([row({})]))).toContain("U1: KiCad 심볼에 데이터시트 링크가 없습니다");
    expect(fetchHint(report([row({ symbol_datasheet_url: "u", datasheet: {} as never })]))).toContain("이미 받았습니다");
  });
});

import { diffFieldLabel, diffSummaryText, proposalSourceLabel, proposalStatusLabel } from "./format";

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
