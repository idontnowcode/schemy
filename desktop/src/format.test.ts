import { describe, expect, it } from "vitest";

import {
  addPage,
  automatedLabel,
  cropSvg,
  datasheetLabel,
  externalLabel,
  identityLabel,
  matchLabel,
  moveToPage,
  outputsLabel,
  pagesOf,
  parseJson,
  pinMapLabel,
  rangeText,
  ratingsLabel,
  removePage,
  renamePage,
  reviewLabel,
  sourceLabel,
  toErrorBody,
} from "./format";
import type { CircuitIR, OutputsStatus } from "./types";

function outputs(partial: Partial<OutputsStatus>, status: "passed" | "failed" | "not_checked" = "passed"): OutputsStatus {
  return {
    manifest: {
      project_id: "p",
      source_revision: 1,
      generated_at: "2026-11-04T10:00:00+09:00",
      generator: "AI Circuit Studio 0.1.0",
      automated_check: {
        status,
        reason: null,
        kicad_version: "10.0.8",
        erc_errors: 0,
        erc_warnings: 0,
        netlist_matches_ir: true,
        violations: [],
        netlist_diff: null,
      },
      warnings: [],
      board_id: "a",
      kicad_project: "p-a",
      pages: [],
      artifacts: [],
    },
    stale: false,
    externally_modified: [],
    missing: [],
    current_revision: 1,
    path: "/w/projects/p/outputs",
    ...partial,
  };
}

describe("status labels keep the three axes apart", () => {
  it("never calls an automated pass a review", () => {
    expect(automatedLabel("passed").text).toBe("Automated checks passed");
    expect(reviewLabel("unverified").text).toBe("미확인");
    expect(reviewLabel("verified").text).toBe("Engineer reviewed");
  });

  it("marks stale and externally modified outputs", () => {
    expect(outputsLabel(null).text).toBe("생성 전");
    expect(outputsLabel(outputs({})).tone).toBe("ok");
    expect(outputsLabel(outputs({ stale: true })).text).toContain("오래됨 (rev 1)");
    expect(outputsLabel(outputs({ externally_modified: ["schematic.kicad_sch"] })).text).toContain("외부에서 수정됨");
    expect(outputsLabel(outputs({}, "not_checked")).text).toBe("Not checked");
  });
});

describe("toErrorBody", () => {
  it("passes the API error contract through", () => {
    const body = toErrorBody(
      { error_code: "NO_CIRCUIT", message: "m", affected_component_ids: ["a"], recovery_hint: "h" },
      409,
    );
    expect(body).toEqual({ error_code: "NO_CIRCUIT", message: "m", affected_component_ids: ["a"], recovery_hint: "h", details: undefined });
  });

  it("wraps anything else", () => {
    expect(toErrorBody("oops", 500)).toEqual({
      error_code: "HTTP_500",
      message: "oops",
      affected_component_ids: [],
      recovery_hint: null,
    });
  });
});

describe("parseJson", () => {
  it("reports syntax errors instead of throwing", () => {
    expect(parseJson('{"a": 1}')).toEqual({ ok: true, value: { a: 1 } });
    const bad = parseJson("{ nope");
    expect(bad.ok).toBe(false);
  });
});

describe("cropSvg", () => {
  const svg =
    '<?xml version="1.0"?>\n<svg\n  xmlns="http://www.w3.org/2000/svg"\n  version="1.1"\n  width="297.0022mm" height="210.0072mm" viewBox="0.0000 0.0000 297.0022 210.0072">\n<g><svg width="1" viewBox="0 0 1 1"></svg></g></svg>';

  it("rewrites only the root element", () => {
    const out = cropSvg(svg, [53.34, 62.88, 45.72, 54.58]);
    expect(out).toContain('width="45.72mm" height="54.58mm" viewBox="53.34 62.88 45.72 54.58"');
    expect(out).toContain('<g><svg width="1" viewBox="0 0 1 1"></svg></g>');
  });

  it("leaves the SVG alone without a usable box", () => {
    expect(cropSvg(svg, null)).toBe(svg);
    expect(cropSvg(svg, [0, 0, 0, 10])).toBe(svg);
  });
});

describe("inventory and revision labels", () => {
  it("names every state", () => {
    expect(matchLabel("matched", false)).toEqual({ text: "보유", tone: "ok" });
    expect(matchLabel("matched", true).text).toBe("보유 · 재고 부족");
    expect(matchLabel("candidate", null).text).toBe("대체 가능");
    expect(matchLabel("missing", null).tone).toBe("bad");
    expect(sourceLabel("inventory")).toBe("보유 자재 적용");
  });
});

describe("page helpers", () => {
  const ir: CircuitIR = {
    schema_version: "0.2.0",
    project_id: "p",
    components: ["a", "b", "c"].map((id) => ({ id, reference: id.toUpperCase() + "1", value: "1k", symbol_id: "Device:R", pins: [] })),
    nets: [{ id: "n", name: "N", nodes: [{ component_id: "a", pin: "1" }] }],
  };

  it("starts with one implicit page", () => {
    expect(pagesOf(ir)).toEqual([{ id: "p1", name: "Page 1", components: ["a", "b", "c"] }]);
  });

  it("adds, moves, renames and removes pages without touching nets", () => {
    let next = addPage(ir);
    expect(next.sheets!.map((s) => s.id)).toEqual(["p1", "p2"]);
    next = moveToPage(next, "c", "p2");
    expect(pagesOf(next).map((p) => p.components)).toEqual([["a", "b"], ["c"]]);
    next = renamePage(next, "p2", "LED");
    expect(next.sheets![1].name).toBe("LED");
    expect(next.nets).toBe(ir.nets);
    next = removePage(next, "p2");
    expect(next.sheets).toEqual([]);
    expect(pagesOf(next)[0].components).toEqual(["a", "b", "c"]);
  });
});

describe("datasheet labels", () => {
  it("names how sure we are about the part", () => {
    expect(identityLabel("mpn").text).toBe("제조사·품번 확정");
    expect(identityLabel("generic").tone).toBe("muted");
    expect(datasheetLabel("family")).toEqual({ text: "제품군 확인됨", tone: "info" });
    expect(datasheetLabel("not_found").tone).toBe("warn");
    expect(datasheetLabel(undefined).text).toBe("확인 안 됨");
  });

  it("summarises the checks", () => {
    expect(pinMapLabel("match", { match: 8 }, true)).toEqual({ text: "핀맵 일치", tone: "ok" });
    expect(pinMapLabel("mismatch", { match: 6, mismatch: 2 }, true)).toEqual({ text: "핀맵 불일치 2", tone: "bad" });
    expect(pinMapLabel("unchecked", {}, false).text).toBe("패키지 선택 필요");
    expect(ratingsLabel("error").tone).toBe("bad");
    expect(ratingsLabel("unchecked").text).toBe("전압 확인 불가");
    expect(externalLabel([{ status: "missing", requirement: "required" }]).text).toBe("필수 부품 확인 1");
    expect(externalLabel([{ status: "missing", requirement: "recommended" }, { status: "present", requirement: "required" }]))
      .toEqual({ text: "권장 부품 확인 1", tone: "info" });
    expect(externalLabel([{ status: "direct", requirement: "recommended" }]).tone).toBe("muted");
    expect(rangeText(-0.3, 6)).toBe("-0.3 ~ 6 V");
    expect(rangeText(null, 3.6)).toBe("최대 3.6 V");
  });
});
