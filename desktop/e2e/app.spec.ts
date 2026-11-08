// Phase 1 user flow in the built UI, against a real backend (and real KiCad when configured).

import { expect, test, type Page } from "@playwright/test";

import { kicadExpected, newProject, openTab, stage, startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

async function createProject(page: Page, name: string) {
  await newProject(page, name);
  await expect(page.getByRole("heading", { name })).toBeVisible();
}

test("create → import example → save → generate → documents, then restore after restart", async ({ page }) => {
  const workspace = tempWorkspace();
  backend = await startBackend(workspace);
  await page.goto(backend.url);
  await createProject(page, "LED Demo");

  // Circuit IR: load the bundled example, check, save.
  await openTab(page, "회로 데이터");
  await page.getByLabel("예제 불러오기").selectOption("led-resistor-connector");
  await page.getByRole("button", { name: "검사" }).click();
  await expect(page.getByText("검사 통과: 오류가 없습니다.")).toBeVisible();
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Device:LED" })).toBeVisible();

  // Schematic: generate and see the automated check result.
  await openTab(page, "회로도 · 검증");
  await page.getByRole("button", { name: "회로도 생성" }).click();
  if (kicadExpected()) {
    await expect(page.locator(".card").getByText("Automated checks passed")).toBeVisible({ timeout: 180_000 });
    await expect(page.getByText("오류 0, 경고 0")).toBeVisible();
    await expect(page.getByRole("img", { name: "생성된 회로도 미리보기" })).toBeVisible();
  } else {
    await expect(page.locator(".card").getByText("Not checked")).toBeVisible({ timeout: 60_000 });
  }
  await expect(page.getByRole("cell", { name: "led-demo-a.kicad_pro" })).toBeVisible();

  // Documents: BOM and Markdown come from the saved IR.
  await openTab(page, "문서 · BOM");
  await expect(page.getByRole("cell", { name: "Resistor_SMD:R_0603_1608Metric" }).first()).toBeVisible();
  await expect(page.locator("article.markdown")).toContainText("LED_A");
  await page.getByRole("tab", { name: "근거 리포트" }).click();
  await expect(page.locator("article.markdown")).toContainText("근거 리포트: ");
  await expect(page.locator("article.markdown")).toContainText("기술 검토 (자동 계산)");
  await page.getByRole("tab", { name: "설계 문서" }).click();
  await openTab(page, "검토");
  await expect(page.getByRole("heading", { name: "기술 검토 (자동 계산)" })).toBeVisible();
  // VIN has no voltage in its name: write one; it becomes a proposal (nothing changes until approved).
  await expect(page.getByText("전압을 알 수 없는 전원 Net: VIN")).toBeVisible();
  await page.getByLabel("VIN 전압").fill("5");
  await page.getByLabel("VIN 부하 전류").fill("20");
  await page.getByRole("button", { name: "전원 조건 제안 만들기" }).click();
  await stage(page, "바뀌는 내용");
  await expect(page.getByRole("heading", { name: "전원 조건 (기술 검토용)" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "20 mA" })).toBeVisible();
  await openTab(page, "문서 · BOM");

  // Phase 1 gate: restart the backend and reload — project, revision and last tab come back.
  await backend.stop();
  backend = await startBackend(workspace);
  await page.goto(backend.url);
  await expect(page.getByRole("heading", { name: "LED Demo" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "문서 · BOM" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("revision 1 · led-demo-a")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Resistor_SMD:R_0603_1608Metric" }).first()).toBeVisible();
});

test("rule errors are shown and nothing is saved", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await createProject(page, "Broken");
  await openTab(page, "회로 데이터");
  await page.getByLabel("예제 불러오기").selectOption("led-resistor-connector");
  const editor = page.getByLabel("Circuit IR JSON");
  const text = await editor.inputValue();
  await editor.fill(text.replace('"reference": "D1"', '"reference": "R1"'));
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("DUPLICATE_REFERENCE")).toBeVisible();
  await expect(page.getByText("[INVALID_CIRCUIT]")).toBeVisible();
  await expect(page.getByText("저장된 회로 없음").first()).toBeVisible();

  await editor.fill("{ not json");
  await page.getByRole("button", { name: "검사" }).click();
  await expect(page.getByText("JSON_SYNTAX")).toBeVisible();
});

test("history restores an earlier revision", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await createProject(page, "History");
  await openTab(page, "회로 데이터");
  await page.getByLabel("예제 불러오기").selectOption("led-resistor-connector");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  const editor = page.getByLabel("Circuit IR JSON");
  await editor.fill((await editor.inputValue()).replace('"value": "330"', '"value": "1k"'));
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 2으로 저장했습니다.")).toBeVisible();

  await openTab(page, "이력");
  page.once("dialog", (d) => d.accept());
  await page.getByRole("row", { name: /^1 / }).getByRole("button", { name: "복원" }).click();
  await expect(page.getByText("revision 3으로 복원했습니다.")).toBeVisible();
  await expect(page.getByRole("cell", { name: "복원 (rev 1)" })).toBeVisible();
});
