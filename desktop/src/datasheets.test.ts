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
