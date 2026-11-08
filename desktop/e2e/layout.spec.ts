// Keeping a layout arranged in KiCad: the schematic tab turns the generated file's positions into a proposal.

import { expect, test } from "@playwright/test";

import { kicadExpected, newProject, openTab, stage, startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("import the KiCad layout as a proposal, approve, then go back to automatic placement", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await newProject(page, "Layout");
  await openTab(page, "회로 데이터");
  await page.getByLabel("예제 불러오기").selectOption("ne555-led-board");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  await openTab(page, "회로도 · 검증");
  await page.getByRole("button", { name: "회로도 생성" }).click();
  await expect(page.locator(".card").getByText(kicadExpected() ? "Automated checks passed" : "Not checked"))
    .toBeVisible({ timeout: 180_000 });
  await expect(page.getByText("고정된 부품: 0개")).toBeVisible();

  // Nothing was edited in KiCad: the full import says so (or that it needs kicad-cli to read connections).
  await page.getByRole("button", { name: "고친 것 모두 가져오기 (제안)" }).click();
  await expect(page.getByText(kicadExpected() ? "NO_KICAD_CHANGES" : "KICAD_UNAVAILABLE")).toBeVisible({ timeout: 60_000 });

  await page.getByRole("button", { name: "KiCad 배치 가져오기 (제안)" }).click();
  await expect(page.getByText("제안 #1을 만들었습니다. 승인하면 반영됩니다.")).toBeVisible();
  await page.getByRole("button", { name: "제안 #1 열기" }).click();
  const detail = page.locator(".proposal-detail");
  await expect(detail.getByText("KiCad 배치")).toBeVisible();
  await expect(detail.locator(".chip").filter({ hasText: "승인 대기" })).toBeVisible({ timeout: 180_000 });
  await stage(page, "바뀌는 내용");
  await expect(detail.getByText(/대비: 배치 12/)).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await stage(page, "결정");
  await detail.getByRole("button", { name: "승인", exact: true }).click();
  await expect(detail.locator(".chip").filter({ hasText: "승인됨" })).toBeVisible({ timeout: 60_000 });

  await openTab(page, "회로도 · 검증");
  await expect(page.getByText("고정된 부품: 12개")).toBeVisible();
  await expect(page.getByRole("button", { name: "자동 배치로 되돌리기" })).toBeVisible();
});
