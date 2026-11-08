// Phase 4 in the built UI with a scripted fake AI: the agent drafts a plan (dry run), the user approves its
// scope, the run executes through the tool gateway, code validates it, and the result is a proposal that only
// the user's approval turns into a revision.

import { expect, test } from "@playwright/test";

import { newProject, openTab, stage, startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("agent: plan → approve scope → sandboxed run → proposal → approve", async ({ page }) => {
  backend = await startBackend(tempWorkspace(), { fakeAi: true });
  await page.goto(backend.url);
  await page.request.put(`${backend.url}/api/ai/settings`, { data: { enabled: true } });
  await newProject(page, "Agent Demo");
  await openTab(page, "회로 데이터");
  await page.getByLabel("예제 불러오기").selectOption("led-resistor-connector");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  await openTab(page, "Agent");
  await page.getByLabel("Agent 요청").fill("R1을 1k로 바꿔 LED 전류를 줄여줘");
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "계획 만들기 (실행하지 않음)" }).click();
  await expect(page.locator(".side .chip").filter({ hasText: "범위 승인 대기" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("범위: 속성 변경 — res-001 (value)")).toBeChecked();
  await expect(page.getByRole("heading", { name: "도구 호출 기록" })).toHaveCount(0); // nothing ran yet

  await page.getByRole("button", { name: "고른 범위 승인하고 실행" }).click();
  await expect(page.locator(".side .chip").filter({ hasText: "제안 검토 필요" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("cell", { name: "propose_ir_patch" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "변경 전후 보고" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "R1" }).filter({ hasText: "330" }).filter({ hasText: "1k" })).toBeVisible();
  await expect(page.getByText("revision 1 · agent-demo-a")).toBeVisible(); // the board is unchanged

  await page.getByRole("button", { name: /제안 #1 열기/ }).click();
  await expect(page.getByText(/AI Agent/).first()).toBeVisible();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인 대기" })).toBeVisible({ timeout: 60_000 });
  page.once("dialog", (d) => d.accept());
  await stage(page, "결정");
  await page.locator(".proposal-detail").getByRole("button", { name: "승인", exact: true }).click();
  await expect(page.getByText(/revision 2으로 저장/)).toBeVisible();

  await openTab(page, "Agent");
  await expect(page.locator(".side .chip").filter({ hasText: "반영됨" })).toBeVisible({ timeout: 30_000 });
});
