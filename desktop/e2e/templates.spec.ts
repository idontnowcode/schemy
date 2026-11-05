// Phase 3 in the built UI: place a reviewed template as a proposal, approve it, then record an engineering
// review; only that record turns the engineering review status into "Engineer reviewed".

import { expect, test } from "@playwright/test";

import { startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("template → proposal → approve → engineering review", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await page.getByLabel("새 프로젝트 이름").fill("Template Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await expect(page.getByRole("heading", { name: "Template Demo" })).toBeVisible();

  await page.getByRole("tab", { name: "템플릿" }).click();
  await page.getByRole("button", { name: /CAN 트랜시버 \(SN65HVD230\)/ }).click();
  await expect(page.getByText("검토 체크리스트")).toBeVisible();
  await expect(page.getByText(/Function Modes — Rs pin/).first()).toBeVisible();
  await page.getByLabel("120Ω 종단 저항 (yes/no)").selectOption("no");
  await page.getByRole("button", { name: "이 보드에 넣기 (제안 만들기)" }).click();

  await expect(page.getByRole("heading", { name: /템플릿: CAN 트랜시버/ }).first()).toBeVisible();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인 대기" })).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("cell", { name: "템플릿 can-sn65hvd230@1" }).first()).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.locator(".proposal-detail").getByRole("button", { name: "승인", exact: true }).click();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인됨" })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".chips").getByText("검토 필요")).toBeVisible();

  await page.getByRole("tab", { name: "검토" }).click();
  await page.getByLabel("검토자").fill("김엔지니어");
  await page.getByLabel("데이터시트 1 부품").fill("SN65HVD230");
  await page.getByLabel("데이터시트 1 쪽").fill("1, 20");
  await expect(page.getByLabel(/^결과: /).first()).toBeVisible();
  const selects = await page.getByLabel(/^결과: /).all();
  expect(selects.length).toBeGreaterThanOrEqual(5); // the template's checklist and the board item
  for (const select of selects) await select.selectOption("ok");
  await page.getByRole("button", { name: "검토 기록 저장" }).click();
  await expect(page.getByText(/검토를 기록했습니다/)).toBeVisible();
  await expect(page.locator(".chips").getByText("Engineer reviewed")).toBeVisible();
  await expect(page.getByText(/#1 · revision 1 · 김엔지니어 · 검토 완료/)).toBeVisible();
});
