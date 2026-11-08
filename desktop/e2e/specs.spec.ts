// Worst-case inputs: a supply's voltage range and the parts' own ratings, entered on the 검토 tab, go through a
// proposal and change the technical review (the I2C sensor example: dropout at the lowest input).

import { expect, test } from "@playwright/test";

import { stage, startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("voltage range and part specs drive the technical review", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await page.getByLabel("새 프로젝트 이름").fill("Specs Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("예제 불러오기").selectOption("i2c-sensor-3v3");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  // The example already says +5V is 4.7~5.25 V: the dropout check works at 4.7 V.
  await page.getByRole("tab", { name: "검토" }).click();
  const review = page.getByLabel("기술 검토");
  await expect(review).toContainText("최저 입력 4.7 V에서 입출력 전압 차 1.4 V");

  // Widen the range on +5V and give C3 its ratings: one proposal each.
  await review.getByText("전원 조건 (Net 전압·부하 전류)").click();
  await review.getByLabel("+5V 최저 전압").fill("4.4");
  await review.getByRole("button", { name: "전원 조건 제안 만들기" }).click();
  await expect(page.getByRole("heading", { name: /전원 조건: \+5V/ })).toBeVisible();
  await stage(page, "바뀌는 내용");
  await expect(page.getByRole("cell", { name: "최저 전압" })).toBeVisible();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인 대기" })).toBeVisible({ timeout: 120_000 });
  page.once("dialog", (d) => d.accept());
  await stage(page, "결정");
  await page.locator(".proposal-detail").getByRole("button", { name: "승인", exact: true }).click();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인됨" })).toBeVisible({ timeout: 60_000 });

  await page.getByRole("tab", { name: "검토" }).click();
  await expect(page.getByLabel("기술 검토")).toContainText("최저 입력 4.4 V에서 입출력 전압 차 1.1 V");
  const specs = page.getByLabel("부품 사양");
  await specs.getByText("부품 사양", { exact: true }).click();
  await specs.getByLabel("C3 종류").selectOption("X7R");
  await specs.getByLabel("C3 정격 전압 (V)").fill("25");
  await specs.getByRole("button", { name: "부품 사양 제안 만들기 (1)" }).click();
  await expect(page.getByRole("heading", { name: /부품 사양: C3/ })).toBeVisible();
  await stage(page, "바뀌는 내용");
  await expect(page.getByText("사양: 종류")).toBeVisible();
});
