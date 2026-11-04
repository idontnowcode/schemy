// Phase 2a in the built UI: a change becomes a proposal, is checked in isolation, shows a code-computed diff,
// and only approval changes the circuit. Also: the requirements tab offers AI design once AI is set up.

import { expect, test } from "@playwright/test";

import { kicadExpected, startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("propose R1 330 → 1k, review the diff, approve", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await page.getByLabel("새 프로젝트 이름").fill("Proposal Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await expect(page.getByRole("heading", { name: "Proposal Demo" })).toBeVisible();

  // The requirements tab is the starting point; AI design waits for the AI settings.
  await expect(page.getByRole("heading", { name: "AI 회로 설계" })).toBeVisible();
  await expect(page.getByRole("button", { name: "AI로 회로 설계" })).toBeDisabled();

  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("예제 불러오기").selectOption("led-resistor-connector");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  await page.getByRole("tab", { name: "제안" }).click();
  await page.getByLabel("제안할 부품").selectOption({ label: "R1 (330)" });
  await page.getByLabel("새 값").fill("1k");
  await page.getByRole("button", { name: "제안 만들기" }).click();
  await expect(page.getByRole("heading", { name: /#1 R1 값: 330 → 1k/ })).toBeVisible();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인 대기" })).toBeVisible({ timeout: 120_000 });
  const row = page.getByRole("row").filter({ hasText: "R1" }).filter({ hasText: "1k" });
  await expect(row).toContainText("330");
  if (kicadExpected()) {
    await expect(page.locator(".proposal-detail").getByText("Automated checks passed")).toBeVisible();
    await expect(page.getByRole("img", { name: "제안된 회로도 미리보기" })).toBeVisible();
  }

  // Nothing changed yet.
  await expect(page.getByText("revision 1 · proposal-demo-a")).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.locator(".proposal-detail").getByRole("button", { name: "승인", exact: true }).click();
  await expect(page.getByText(/revision 2으로 저장/)).toBeVisible();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인됨" })).toBeVisible();
  await expect(page.getByText("revision 2 · proposal-demo-a")).toBeVisible();

  await page.getByRole("tab", { name: "이력" }).click();
  await expect(page.getByRole("cell", { name: "제안 승인" }).first()).toBeVisible();
});
