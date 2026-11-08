import { describe, expect, it } from "vitest";

import { ideaText, roundsOf } from "./components/StageFlow";
import type { AutoReviewRound } from "./types";

const round = (n: number, extra: Partial<AutoReviewRound> = {}): AutoReviewRound => ({
  round: n, proposal_id: String(n).padStart(4, "0"), errors: 0, warnings: 0, check: "", check_failed: false, said: [], ...extra,
});

describe("step screens", () => {
  it("treats the new-project requirements template as no idea yet", () => {
    expect(ideaText("# 요구사항\n\n<!-- 회로 요구사항을 자유롭게 적으세요. 예: 입력 전압 -->\n")).toBe("");
    expect(ideaText("")).toBe("");
    const real = "# 요구사항\n\n3.3V CAN 노드";
    expect(ideaText(real)).toBe(real);
  });

  it("rebuilds the review timeline from job steps, keeping each round's latest report", () => {
    const steps = [
      { at: "t", step: "review", text: "1회차 검토 중" },
      { at: "t", step: "round", text: "1회차", data: round(1, { errors: 1 }) },
      { at: "t", step: "revised", text: "수정안", data: round(1, { errors: 1, revised_to: "0002" }) },
      { at: "t", step: "round", text: "2회차", data: round(2, { warnings: 1 }) },
      { at: "t", step: "done", text: "멈춤: 조건 충족" },
    ];
    const rounds = roundsOf(steps);
    expect(rounds.map((r) => r.round)).toEqual([1, 2]);
    expect(rounds[0].revised_to).toBe("0002");
    expect(roundsOf([])).toEqual([]);
  });
});
