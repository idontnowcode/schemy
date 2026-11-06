import { describe, expect, it } from "vitest";

import { lineFormats, noteStatus, noteWhere, parseApply, parseChecks } from "./components/Notes";

describe("research note editing", () => {
  it("round-trips checks and apply-next items through plain lines", () => {
    const check = { what: "최저 입력 드롭아웃", how: "4.7 V − 3.3 V", result: "여유 0.2 V", status: "issue" as const };
    expect(parseChecks(lineFormats.check(check))).toEqual([check]);
    expect(parseChecks("항목만\n\n항목 | 방법 | 결과 | 이상한값")).toEqual([
      { what: "항목만", how: "", result: "", status: "unknown" },
      { what: "항목", how: "방법", result: "결과", status: "unknown" },
    ]);
    const apply = { text: "최저 입력으로 드롭아웃을 계산한다", why: "정격만 보면 여유를 과대평가" };
    expect(parseApply(lineFormats.apply(apply))).toEqual([apply]);
    expect(parseApply("이유 없는 할 일")).toEqual([{ text: "이유 없는 할 일", why: "" }]);
  });

  it("labels status and where a note belongs", () => {
    expect(noteStatus({ status: "draft", author_kind: "ai" })).toEqual({ text: "AI 초안", tone: "warn" });
    expect(noteStatus({ status: "reviewed", author_kind: "ai" }).text).toBe("검토함");
    expect(noteWhere({ project_name: "I2C", board_id: "a", board_name: "A", revision: 2, proposal_id: "0003", project_id: "p" }))
      .toBe("I2C / 보드 A / rev 2 / 제안 #3");
  });
});
