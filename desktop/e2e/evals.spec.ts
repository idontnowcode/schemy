// AI performance evaluation: run the research-note suite with the scripted AI, see the score and the report.

import { expect, test } from "@playwright/test";

import { startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("run an AI evaluation and read its report", async ({ page }) => {
  backend = await startBackend(tempWorkspace(), { fakeAi: true });
  await page.goto(backend.url);
  await page.getByRole("button", { name: "AI 성능 평가" }).click();
  const evals = page.getByLabel("AI 성능 평가", { exact: true });
  await expect(evals.getByRole("button", { name: "평가 실행" })).toBeDisabled(); // AI is off
  await page.request.put(`${backend.url}/api/ai/settings`, { data: { enabled: true } });
  await page.getByRole("button", { name: "설정" }).click();
  await page.getByRole("button", { name: "AI 성능 평가" }).click();

  await evals.getByLabel("설계 묶음").uncheck();
  await evals.getByLabel("검토 묶음").uncheck();
  await evals.getByLabel("수정 묶음").uncheck();
  await expect(evals).toContainText("AI 호출 약 2회");
  let asked = "";
  page.once("dialog", (d) => {
    asked = d.message();
    void d.accept();
  });
  await evals.getByRole("button", { name: "평가 실행" }).click();
  await expect(evals.getByRole("status").last()).toContainText("를 마쳤습니다", { timeout: 60_000 });
  expect(asked).toContain("사용자 프로젝트는 보내지 않음");
  const history = evals.getByRole("table", { name: "평가 기록" });
  await expect(history.getByRole("row")).toHaveCount(2); // header + one run
  await expect(history.getByRole("row").nth(1)).toContainText("60%"); // 3 of 5 note lines cite evidence
  const detail = evals.locator("article");
  await expect(detail).toContainText("연구노트");
  await expect(detail).toContainText("채점은 모두 코드가 합니다");
});
