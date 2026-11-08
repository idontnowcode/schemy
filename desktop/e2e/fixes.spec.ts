// Acting on check results without AI: a proposal that leaves the motor driver's inputs floating offers
// "바로 고치기"; choosing the suggested fixes makes a follow-up proposal that shows what changed from the first one.

import { expect, test } from "@playwright/test";

import { stage, startBackend, tempWorkspace, type Backend } from "./backend";

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
  // Before approval the proposal's parts can get their datasheets too (TI's DRV8871: symbol link and TI's pattern).
  await stage(page, "데이터시트");
  const finder = page.getByLabel("데이터시트 찾기");
  await expect(finder.getByLabel("받을 곳: U1").locator("option", { hasText: "제조사 사이트: www.ti.com · DRV8871DDAR" }))
    .toBeAttached({ timeout: 120_000 });
  await stage(page, "자동 검사");
  const panel = page.getByLabel("바로 고치기");
  await expect(panel).toBeVisible({ timeout: 120_000 });
  await expect(panel.getByLabel("조치: U1.2")).toHaveValue("0"); // the suggested pull-down
  await expect(panel.getByRole("option", { name: "10kΩ로 GND에 풀다운 (추천)" }).first()).toBeAttached();
  page.once("dialog", (d) => d.accept());
  await panel.getByRole("button", { name: "선택한 조치로 수정 제안 만들기 (2개)" }).click();

  await expect(page.getByRole("heading", { name: /#2 .*\(제안 #1 수정\)/ })).toBeVisible({ timeout: 60_000 });
  await stage(page, "바뀌는 내용");
  await expect(page.getByText(/제안 #1 대비 바뀐 점: 부품 \+2/)).toBeVisible();
  await stage(page, "자동 검사");
  await expect(page.locator(".proposal-detail").getByText(/자동 검사:/)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByLabel("바로 고치기")).toHaveCount(0, { timeout: 120_000 }); // nothing floats any more
});

test("an approved proposal keeps its fix actions, and the schematic tab offers them too", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await page.getByLabel("새 프로젝트 이름").fill("Approved Fix");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("예제 불러오기").selectOption("dc-motor-drv8871");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  // Approve a change that leaves the driver inputs floating (the board is now that circuit, revision 2).
  const projects = await (await page.request.get(`${backend.url}/api/projects`)).json();
  const pid = projects[0].id as string;
  const base = `${backend.url}/api/projects/${pid}/boards/a`;
  const created = await (await page.request.post(`${base}/proposals`, {
    data: { title: "제어 헤더 빼기", ops: [{ op: "remove_component", component_id: "j-ctl", expect_reference: "J2" }] },
  })).json();
  await expect.poll(async () => (await (await page.request.get(`${base}/proposals/${created.proposal.id}`)).json()).status,
    { timeout: 120_000 }).not.toBe("checking");
  const approved = await page.request.post(`${base}/proposals/${created.proposal.id}/approve`,
    { data: { accept_failed_checks: true, generate: false } });
  expect(approved.ok()).toBeTruthy();

  await page.reload();
  await page.getByRole("tab", { name: "제안" }).click();
  await page.getByText("제어 헤더 빼기").first().click();
  await expect(page.getByText("승인").first()).toBeVisible();
  // The fixes stay: the AI ones visible and disabled with the reason (AI is off here), AI-free ones on the saved circuit.
  await stage(page, "결정");
  const revise = page.getByRole("button", { name: "검토 반영 수정안 만들기" }).first();
  await expect(revise).toBeVisible();
  await expect(revise).toBeDisabled();
  await stage(page, "자동 검사");
  const panel = page.getByLabel("바로 고치기");
  await expect(panel).toBeVisible({ timeout: 60_000 });
  page.once("dialog", (d) => d.accept());
  await panel.getByRole("button", { name: /선택한 조치로 수정 제안 만들기/ }).click();
  await expect(page.getByRole("heading", { name: /#2 / })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/기준 revision 2/).first()).toBeVisible(); // started from the saved circuit, not undone

  // The same quick fixes are on the schematic tab's check card, for the saved circuit.
  await page.getByRole("tab", { name: "회로도 · 검증" }).click();
  await page.getByRole("button", { name: /회로도 생성|다시 생성/ }).first().click();
  await expect(page.getByLabel("바로 고치기")).toBeVisible({ timeout: 120_000 });
});
