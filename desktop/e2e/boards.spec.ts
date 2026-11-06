// Boards, pages, Markdown import (PDF copy step 1) and inventory matching, against a real backend.

import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";
import path from "node:path";

import { kicadExpected, repoRoot, startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

const PARTS_CSV = [
  "품번,제조사,종류,값,패키지,재고,우선순위,비고",
  "RC0603FR-07330RL,Yageo,칩저항,330R,0603,5000,1,표준품",
  "RC0805FR-07330RL,Yageo,칩저항,330,0805,100,2,",
  "LTST-C191KRKT,Lite-On,LED,LED,0603,40,1,빨강",
].join("\n");

test("inventory → Markdown import → pages → generate → apply stocked parts → second board", async ({ page }) => {
  const workspace = tempWorkspace();
  const csv = path.join(workspace, "parts.csv");
  writeFileSync(csv, PARTS_CSV, "utf-8");
  backend = await startBackend(workspace);
  await page.goto(backend.url);

  // Inventory: preview, then save.
  await page.getByRole("button", { name: "보유 자재" }).click();
  await page.locator('input[type=file]').setInputFiles(csv);
  await expect(page.getByText("3종을 읽었습니다.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "이 목록으로 저장" }).click();
  await expect(page.getByText("3종을 저장했습니다.")).toBeVisible();

  // Project with the default board A; import the Copilot-style Markdown.
  await page.getByLabel("새 프로젝트 이름").fill("Board Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await expect(page.getByRole("heading", { name: "Board Demo" })).toBeVisible();
  await expect(page.getByRole("tab", { name: /보드 A/ })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.locator('input[accept^=".md"]').setInputFiles(path.join(repoRoot, "examples", "led-resistor-connector", "schematic.md"));
  await expect(page.getByRole("heading", { name: "가져오기 결과" })).toBeVisible();
  await expect(page.getByText("부품 3개 · Net 3개 · 페이지 1")).toBeVisible();

  // Split into two pages, then save.
  await page.getByRole("button", { name: "+ 페이지" }).click();
  await page.getByLabel("D1 페이지").selectOption("p2");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  // Generate: two pages.
  await page.getByRole("tab", { name: "회로도 · 검증" }).click();
  await page.getByRole("button", { name: "회로도 생성" }).click();
  if (kicadExpected()) {
    await expect(page.locator(".card").getByText("Automated checks passed")).toBeVisible({ timeout: 180_000 });
    await expect(page.getByLabel("페이지 선택")).toBeVisible();
  } else {
    await expect(page.locator(".card").getByText("Not checked")).toBeVisible({ timeout: 60_000 });
  }
  await expect(page.getByRole("cell", { name: "board-demo-a-p2-p2.kicad_sch" })).toBeVisible();

  // Materials: R1 and D1 are stocked; apply writes their part numbers as a new revision.
  await page.getByRole("tab", { name: "자재" }).click();
  await expect(page.getByText("보유 2 · 대체 가능 0 · 미보유 1", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "보유 자재 적용" }).click();
  await expect(page.getByText(/revision 2으로 저장했습니다: .*R1 품번 → RC0603FR-07330RL/)).toBeVisible();
  await page.getByRole("tab", { name: "이력" }).click();
  await expect(page.getByRole("cell", { name: /보유 자재 적용/ }).first()).toBeVisible();

  // A second board starts empty and keeps its own history.
  await page.getByRole("button", { name: "보드 추가" }).click();
  await expect(page.getByRole("tab", { name: /보드 B/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("아직 저장된 revision이 없습니다.")).toBeVisible();
  await page.getByRole("tab", { name: /보드 A/ }).click();
  await expect(page.getByText("revision 2 · board-demo-a")).toBeVisible();
});

test("AI buttons stay off until AI is enabled with a key", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await page.getByLabel("새 프로젝트 이름").fill("No AI");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await expect(page.getByRole("button", { name: "PDF 가져오기 (AI)" })).toBeDisabled();
  await page.getByRole("button", { name: "설정" }).click();
  await expect(page.getByLabel("AI 기능 사용")).not.toBeChecked();
  await expect(page.getByLabel("AI 모델")).toHaveValue("gemini-flash-latest");
  await expect(page.getByRole("button", { name: "연결 시험" })).toBeDisabled(); // no key yet
  // Claude: its default model, and a login button (disabled with an install hint when the `ant` CLI is missing).
  await page.getByRole("radio", { name: "Anthropic Claude API" }).click();
  await expect(page.getByLabel("AI 모델")).toHaveValue("claude-opus-5-5");
  await expect(page.getByRole("heading", { name: "Claude 연결" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Claude 로그인" })).toBeDisabled();
  await expect(page.getByText("Anthropic CLI(`ant`)가 필요합니다", { exact: false })).toBeVisible();
  // ChatGPT: the user's own account, no API key field.
  await page.getByRole("radio", { name: "OpenAI ChatGPT (내 계정)" }).click();
  await expect(page.getByLabel("AI 모델")).toHaveValue("gpt-6.1-sol");
  await expect(page.getByRole("heading", { name: "ChatGPT 계정" })).toBeVisible();
  await expect(page.getByRole("button", { name: "ChatGPT로 계속하기" })).toBeEnabled();
  await expect(page.getByLabel("Gemini API 키")).toHaveCount(0);
  await page.getByRole("radio", { name: "Google Gemini API" }).click();
  await expect(page.getByLabel("AI 모델")).toHaveValue("gemini-flash-latest");
});

test("connection test sends one fixed line and shows the answer", async ({ page }) => {
  backend = await startBackend(tempWorkspace(), { fakeAi: true });
  await page.goto(backend.url);
  await page.getByRole("button", { name: "설정" }).click();
  let asked = "";
  page.once("dialog", (d) => {
    asked = d.message();
    void d.accept();
  });
  await page.getByRole("button", { name: "연결 시험" }).click();
  await expect(page.getByRole("status")).toContainText('연결됨: gemini-flash-latest');
  await expect(page.getByRole("status")).toContainText('응답 "OK"');
  expect(asked).toContain("보내는 것: 고정 문장 한 줄");
  expect(asked).toContain("회로·파일·프로젝트 정보는 보내지 않습니다");
});
