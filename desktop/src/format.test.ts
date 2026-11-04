import { describe, expect, it } from "vitest";

import { automatedLabel, cropSvg, outputsLabel, parseJson, reviewLabel, toErrorBody } from "./format";
import type { OutputsStatus } from "./types";

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
      preview_bbox_mm: null,
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
