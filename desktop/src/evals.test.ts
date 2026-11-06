import { describe, expect, it } from "vitest";

import { estimateCalls, formatMetric, metricValue } from "./components/EvalPage";
import type { EvalCases } from "./types";

describe("AI evaluation page", () => {
  it("formats metrics and estimates calls", () => {
    expect(formatMetric(0.756, "rate")).toBe("76%");
    expect(formatMetric(12345, "count")).toBe((12345).toLocaleString());
    expect(formatMetric(1.5, "number")).toBe("1.5");
    expect(formatMetric(null, "rate")).toBe("—");
    expect(metricValue({ design: { completed: 0.8 } }, "design.completed")).toBe(0.8);
    expect(metricValue({ design: {} }, "review.found")).toBeNull();
    const cases = { calls_per_repeat: { design: 15, review: 10, notes: 2 } } as unknown as EvalCases;
    expect(estimateCalls(cases, ["design", "notes"], 2)).toBe(34);
    expect(estimateCalls(null, ["design"], 1)).toBe(0);
  });
});
