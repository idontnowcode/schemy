// Acting on check results without AI: a proposal that leaves the motor driver's inputs floating offers
// "바로 고치기"; choosing the suggested fixes makes a follow-up proposal that shows what changed from the first one.

import { expect, test } from "@playwright/test";

import { startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("floating inputs get a one-click fix as a follow-up proposal", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await page.getByLabel("새 프로젝트 이름").fill("Fix Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("예제 불러오기").selectOption("dc-motor-drv8871");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  // A proposal that removes the control header (IN1 / IN2 are left alone on their nets).
  const projects = await (await page.request.get(`${backend.url}/api/projects`)).json();
  const pid = projects[0].id as string;
  const created = await page.request.post(`${backend.url}/api/projects/${pid}/boards/a/proposals`, {
    data: { title: "제어 헤더 빼기", ops: [{ op: "remove_component", component_id: "j-ctl", expect_reference: "J2" }] },
  });
  expect(created.ok()).toBeTruthy();

  await page.getByRole("tab", { name: "제안" }).click();
  await page.getByText("제어 헤더 빼기").first().click();
  const panel = page.getByLabel("바로 고치기");
  await expect(panel).toBeVisible({ timeout: 120_000 });
  await expect(panel.getByLabel("조치: U1.2")).toHaveValue("0"); // the suggested pull-down
  await expect(panel.getByRole("option", { name: "10kΩ로 GND에 풀다운 (추천)" }).first()).toBeAttached();
  // Before approval the proposal's parts can get their datasheets too (TI's DRV8871: symbol link and TI's pattern).
  const finder = page.getByLabel("데이터시트 찾기");
  await expect(finder.getByLabel("받을 곳: U1").locator("option", { hasText: "제조사 사이트: www.ti.com · DRV8871DDAR" })).toBeAttached();
  page.once("dialog", (d) => d.accept());
  await panel.getByRole("button", { name: "선택한 조치로 수정 제안 만들기 (2개)" }).click();

  await expect(page.getByRole("heading", { name: /#2 .*\(제안 #1 수정\)/ })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/제안 #1 대비 바뀐 점: 부품 \+2/)).toBeVisible();
  await expect(page.getByLabel("바로 고치기")).toHaveCount(0, { timeout: 120_000 }); // nothing floats any more
});
