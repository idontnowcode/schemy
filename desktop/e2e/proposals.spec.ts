// Phase 2a in the built UI: a change becomes a proposal, is checked in isolation, shows a code-computed diff,
// and only approval changes the circuit. Also: the requirements tab offers AI design once AI is set up.

import { expect, test } from "@playwright/test";

import { kicadExpected, stage, startBackend, tempWorkspace, type Backend } from "./backend";

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
  await stage(page, "바뀌는 내용");
  const row = page.getByRole("row").filter({ hasText: "R1" }).filter({ hasText: "1k" });
  await expect(row).toContainText("330");
  if (kicadExpected()) {
    await expect(page.getByRole("img", { name: "제안된 회로도 미리보기" })).toBeVisible();
    await stage(page, "자동 검사");
    await expect(page.locator(".proposal-detail").getByText("Automated checks passed")).toBeVisible();
  }

  // Nothing changed yet.
  await expect(page.getByText("revision 1 · proposal-demo-a")).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await stage(page, "결정");
  await page.locator(".proposal-detail").getByRole("button", { name: "승인", exact: true }).click();
  await expect(page.getByText(/revision 2으로 저장/)).toBeVisible();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인됨" })).toBeVisible();
  await expect(page.getByText("revision 2 · proposal-demo-a")).toBeVisible();

  // A second change, rejected with a reason (inline form: Electron has no prompt()).
  await page.getByLabel("제안할 부품").selectOption({ label: "D1 (LED)" });
  await page.getByLabel("새 값").fill("RED");
  await page.getByRole("button", { name: "제안 만들기" }).click();
  await expect(page.getByRole("heading", { name: /#2 D1 값: LED → RED/ })).toBeVisible();
  await expect(page.locator(".proposal-detail .chip").filter({ hasText: "승인 대기" })).toBeVisible({ timeout: 120_000 });
  await stage(page, "결정");
  await page.locator(".proposal-detail").getByRole("button", { name: "거부", exact: true }).click();
  await page.getByLabel("거부 이유").fill("색은 나중에 정함");
  await page.getByRole("button", { name: "거부 확정" }).click();
  await expect(page.getByText("거부했습니다. 회로는 바뀌지 않았습니다.")).toBeVisible();
  await expect(page.locator(".proposal-detail").getByText("색은 나중에 정함")).toBeVisible();
  await expect(page.getByText("revision 2 · proposal-demo-a")).toBeVisible();

  await page.getByRole("tab", { name: "이력" }).click();
  await expect(page.getByRole("cell", { name: "제안 승인" }).first()).toBeVisible();
});

test("a proposal is reviewed one stage at a time, like slides", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await page.getByLabel("새 프로젝트 이름").fill("Slides Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("예제 불러오기").selectOption("led-resistor-connector");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();
  await page.getByRole("tab", { name: "제안" }).click();
  await page.getByLabel("제안할 부품").selectOption({ label: "R1 (330)" });
  await page.getByLabel("새 값").fill("1k");
  await page.getByRole("button", { name: "제안 만들기" }).click();

  // Starts at the overview; the decision (approve / reject) is the last slide.
  const slide = page.locator(".slide");
  await expect(slide.getByRole("heading", { name: "개요" })).toBeVisible();
  await expect(slide.getByText("1 / 7")).toBeVisible();
  await expect(page.locator(".proposal-detail").getByRole("button", { name: "승인", exact: true })).toHaveCount(0);
  await page.keyboard.press("ArrowRight"); // like a slide deck
  await expect(slide.getByRole("heading", { name: "바뀌는 내용" })).toBeVisible();
  await expect(slide.getByText("2 / 7")).toBeVisible();
  await slide.getByRole("button", { name: "다음: 자동 검사 →" }).click();
  await expect(slide.getByRole("heading", { name: "자동 검사" })).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(slide.getByRole("heading", { name: "바뀌는 내용" })).toBeVisible();
  await stage(page, "결정");
  await expect(slide.getByRole("table", { name: "단계별 결과" })).toContainText("자동 검사");
  await expect(slide.getByRole("button", { name: "승인", exact: true })).toBeVisible();
  await slide.getByRole("button", { name: "바뀌는 내용 다시 보기" }).click();
  await expect(slide.getByRole("heading", { name: "바뀌는 내용" })).toBeVisible();

  // Everything on one page instead, remembered for the next visit.
  await page.getByRole("checkbox", { name: "한 화면에 모두 보기" }).check();
  await expect(page.getByRole("heading", { name: "7. 결정" })).toBeVisible();
  await expect(page.getByLabel("검토 단계")).toHaveCount(0);
  await page.reload();
  await page.getByRole("tab", { name: "제안" }).click();
  await expect(page.getByRole("heading", { name: "3. 자동 검사" })).toBeVisible();
  await page.getByRole("checkbox", { name: "한 화면에 모두 보기" }).uncheck();
  await expect(page.getByLabel("검토 단계")).toBeVisible();
});
