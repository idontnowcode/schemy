// The default project screen with a scripted fake AI: an idea in plain words goes through the seven stages
// (아이디어 → 요구사항 → 설계 준비 → AI 설계 → 검토 → 확인·승인 → 회로도·출력). The automatic review needs one
// revision before 「수정 필요 0 · 수정 권장 ≤ 1」 holds, and nothing reaches the board until the person approves.

import { expect, test } from "@playwright/test";

import { kicadExpected, newProject, startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("idea → requirements → design → automatic review → approval → schematic, one stage at a time", async ({ page }) => {
  backend = await startBackend(tempWorkspace(), { fakeAi: true });
  await page.goto(backend.url);
  await page.request.put(`${backend.url}/api/ai/settings`, { data: { enabled: true } });
  await newProject(page, "CAN 노드");
  const rail = page.getByLabel("진행 단계");
  const card = page.locator(".flow-card");

  // 1 아이디어: plain words; later stages are not reachable yet.
  await expect(card.getByRole("heading", { name: "아이디어" })).toBeVisible();
  await expect(rail.getByRole("button", { name: /검토/ })).toBeDisabled();
  await page.getByRole("textbox", { name: "아이디어" }).fill("3.3V로 동작하는 CAN 노드. MCU와 UART로 연결하고 버스 종단 저항 포함.");
  await page.getByRole("button", { name: "AI로 요구사항 정리 →" }).click();

  // 2 요구사항: the AI's sheet as cards, its question answered.
  await expect(card.getByRole("heading", { name: "요구사항" })).toBeVisible();
  await expect(card.getByText("트랜시버 SN65HVD230")).toBeVisible();
  await page.getByRole("button", { name: "기능 고치기" }).click();
  await page.getByLabel("기능 항목").fill("CAN 통신\nMCU와 UART 연결\n상태 LED");
  await page.getByRole("button", { name: "기능 고치기" }).click();
  await expect(card.getByText("상태 LED")).toBeVisible();
  await page.getByLabel("CAN 버스 속도는?").fill("500 kbps");
  await page.getByRole("button", { name: "다음: 설계 준비 →" }).click();
  const saved = await (await page.request.get(`${backend.url}/api/projects`)).json();
  const pid = saved.find((p: { name: string }) => p.name === "CAN 노드").id;
  const spec = (await (await page.request.get(`${backend.url}/api/projects/${pid}/requirements/spec`)).json()).spec;
  expect(spec.functions).toEqual(["CAN 통신", "MCU와 UART 연결", "상태 LED"]);
  expect(spec.open_questions).toEqual([]);
  const req = (await (await page.request.get(`${backend.url}/api/projects/${pid}/requirements`)).json()).text;
  expect(req).toContain("답: 500 kbps");

  // 3 설계 준비 → 4 AI 설계: the job's steps show as they happen.
  await expect(card.getByRole("heading", { name: "설계 준비" })).toBeVisible();
  await expect(card.getByText(/보낼 데이터:/)).toBeVisible();
  // The datasheet step is the person's choice here, and the screen says what it downloads and sends.
  await expect(card.getByLabel("고른 부품의 데이터시트를 받아 대조")).toBeChecked();
  await expect(card.locator(".flow-note")).toContainText("새로 받은 데이터시트 PDF(요약용)");
  await page.getByRole("button", { name: "AI 설계 시작" }).click();
  await expect(card.getByRole("heading", { name: "AI 설계" })).toBeVisible();
  await expect(card.getByText("제안 #1 만듦", { exact: false }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel("설계 진행 기록")).toContainText("부품 6개 선택");
  await expect(page.getByLabel("설계 진행 기록")).toContainText("연결 완료: Net 6개");
  // Between choosing the parts and connecting the pins: the IC's datasheet is fetched and summarized.
  await expect(page.getByLabel("설계 진행 기록")).toContainText("데이터시트 받는 중: SN65HVD230DR");
  await expect(card.locator(".flow-progress-row").filter({ hasText: "데이터시트" }))
    .toContainText("데이터시트: 대상 1종 · 요약 있음 1 (새로 받음 1, 새로 요약 1)");

  // 5 검토: review → revision → review until the condition holds.
  await page.getByRole("button", { name: "다음: 검토 →" }).click();
  await expect(page.getByLabel("최대 반복 횟수")).toHaveValue("3");
  await page.getByRole("button", { name: "자동 반복 검토 시작" }).click();
  await expect(card.getByText("조건 충족: 제안 #2 (수정 필요 0 · 수정 권장 1)")).toBeVisible({ timeout: 90_000 });
  const rounds = page.getByLabel("검토 회차").getByRole("listitem");
  await expect(rounds).toHaveCount(2);
  await expect(rounds.nth(0)).toContainText("수정 필요 1");
  await expect(rounds.nth(0)).toContainText("→ 수정안 #2");
  await expect(rounds.nth(1)).toContainText("조건 충족 → 끝");

  // What is left can be kept as designed, with a reason: then nothing is left for the AI to fix.
  const left = page.getByLabel("남은 의견");
  await expect(left.getByRole("heading", { name: "남은 의견 1개" })).toBeVisible();
  await expect(left).toContainText("CAN 라인 TVS 권장");
  await left.getByRole("button", { name: "의도한 설계로 두기" }).click();
  await left.getByLabel("CAN 라인 TVS 권장 이유").fill("시험용 보드라 버스가 짧고 서지 요구가 없음");
  await left.getByRole("button", { name: "기록" }).click();
  await expect(left.getByRole("heading", { name: "남은 의견 0개 — AI가 고칠 것이 없습니다" })).toBeVisible();
  await expect(left.getByText("사람이 정한 것 1개")).toBeVisible();
  // Running the loop again does not count what a person kept: met at once, with no AI call.
  await page.getByRole("button", { name: "다시 실행" }).click();
  await expect(card.getByText("조건 충족: 제안 #2 (수정 필요 0 · 수정 권장 0)")).toBeVisible({ timeout: 30_000 });
  await expect(rounds).toHaveCount(1);
  await expect(rounds.nth(0)).toContainText("사람이 정함 1");
  await expect(rounds.nth(0)).toContainText("남은 지적 없음");

  // 6 확인·승인: one table; 「자세히」 opens the proposal's own slide; nothing saved before approval.
  await page.getByRole("button", { name: "다음: 확인·승인 →" }).click();
  await expect(page.getByRole("table", { name: "제안 요약" })).toContainText("수정 필요 0 · 권장 0");
  await page.getByRole("button", { name: "AI 검토 자세히" }).click();
  await expect(page.getByText("4 / 7")).toBeVisible();
  await expect(page.getByText("사람이 정함").first()).toBeVisible();
  await expect(page.getByText("의도한 설계로 둔 이유: 시험용 보드라 버스가 짧고 서지 요구가 없음")).toBeVisible();
  await page.getByRole("button", { name: "← 확인·승인(으)로 돌아가기" }).click();
  // The proposal was compared with the datasheet the design step fetched; the summary is the AI's, not yet checked.
  const dsRow = page.getByRole("table", { name: "제안 요약" }).getByRole("row").filter({ hasText: "데이터시트" });
  await expect(dsRow).toContainText("AI 요약 1개는 사람이 원문과 확인하기 전");
  await page.getByRole("button", { name: "데이터시트 자세히" }).click();
  await expect(page.getByText("설계 때 자동으로 확인한 데이터시트 1종 · 요약 있음 1")).toBeVisible();
  await page.getByRole("button", { name: "← 확인·승인(으)로 돌아가기" }).click();
  let circuit = (await (await page.request.get(`${backend.url}/api/projects/${pid}/boards/a/circuit`)).json()).circuit;
  expect(circuit).toBeNull();
  await page.getByRole("button", { name: "승인", exact: true }).click();
  await expect(page.getByText(/그래도 승인하면 revision 1로 저장|승인하면 revision 1로 저장/)).toBeVisible();
  await page.getByRole("button", { name: "승인 확정" }).click();

  // 7 회로도·출력: the approved revision and what to do next.
  await expect(card.getByRole("heading", { name: "회로도·출력" })).toBeVisible({ timeout: 60_000 });
  await expect(card.getByText(/revision 1의 KiCad 회로도/)).toBeVisible();
  await expect(page.getByRole("button", { name: "BOM·문서 받기" })).toBeVisible();
  if (kicadExpected()) await expect(page.getByRole("img", { name: /회로도 미리보기/ })).toBeVisible({ timeout: 60_000 });
  circuit = (await (await page.request.get(`${backend.url}/api/projects/${pid}/boards/a/circuit`)).json()).circuit;
  expect(circuit.components).toHaveLength(6);

  // Every stage stays reachable; the advanced screen still has every tab, grouped.
  await rail.getByRole("button", { name: /아이디어/ }).click();
  await expect(page.getByRole("textbox", { name: "아이디어" })).toHaveValue(/CAN 노드/);
  await page.keyboard.press("ArrowRight");
  await expect(card.getByRole("heading", { name: "요구사항" })).toBeVisible();
  await page.getByRole("button", { name: "고급 화면(탭)" }).click();
  await expect(page.getByRole("tablist", { name: "묶음" }).getByRole("tab")).toHaveCount(4);
  await page.getByRole("tab", { name: "검증", exact: true }).click();
  await expect(page.getByRole("tab", { name: "회로도 · 검증", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "← 단계 화면으로" }).click();
  await expect(rail).toBeVisible();
});

test("a part with no KiCad symbol: pick another part and the design continues", async ({ page }) => {
  backend = await startBackend(tempWorkspace(), { fakeAi: true });
  await page.goto(backend.url);
  await page.request.put(`${backend.url}/api/ai/settings`, { data: { enabled: true } });
  await newProject(page, "XYZ 노드");
  const card = page.locator(".flow-card");
  await page.getByRole("textbox", { name: "아이디어" }).fill("3.3V CAN 노드. 트랜시버는 XYZ999로, MCU와 UART로 연결.");
  await page.getByRole("button", { name: "AI로 요구사항 정리 →" }).click();
  await page.getByRole("button", { name: "다음: 설계 준비 →" }).click();
  await page.getByRole("button", { name: "AI 설계 시작" }).click();

  // Not a dead end: the design stops at the part and offers ways forward.
  await expect(card.getByText("부품 고르기에서 멈췄습니다. 진행할 방법을 고르세요.")).toBeVisible({ timeout: 60_000 });
  const part = page.getByRole("group", { name: /U1 · CAN 트랜시버/ });
  await expect(part.getByRole("heading", { name: "1) 다른 부품으로 대체" })).toBeVisible();
  await expect(part.getByRole("heading", { name: "2) 다른 방법으로 진행" })).toBeVisible();
  await expect(part.getByRole("heading", { name: "3) 기타 의견" })).toBeVisible();
  await expect(part.getByText("없는 부품")).toHaveCount(0); // an invented part number is not offered
  const replace = part.locator(".choice-option").filter({ hasText: "SN65HVD230으로 대체" });
  await expect(replace.getByText("KiCad 심볼 있음")).toBeVisible();
  await expect(part.locator(".choice-option").filter({ hasText: "CAN 대신 UART만 쓰기" }).getByText("AI가 다시 설계")).toBeVisible();
  await replace.getByRole("radio").check();
  await expect(card.getByText(/핀 연결부터 이어서 설계합니다/)).toBeVisible();
  await page.getByLabel("CAN 커넥터 형태?").fill("2핀 터미널");
  await page.getByRole("button", { name: "고른 방법으로 이어서 설계" }).click();

  await expect(card.getByText("제안 #1 만듦", { exact: false }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByLabel("설계 진행 기록")).toContainText("고른 대안으로 부품 계획을 고쳐 이어서 설계");
  await page.getByRole("button", { name: "다음: 검토 →" }).click();
  await expect(page.getByRole("button", { name: "자동 반복 검토 시작" })).toBeEnabled();
});

test("work keeps going when you leave the screen, and the status panel shows it everywhere", async ({ page }) => {
  backend = await startBackend(tempWorkspace(), { fakeAi: true });
  await page.goto(backend.url);
  await page.request.put(`${backend.url}/api/ai/settings`, { data: { enabled: true } });
  await newProject(page, "상태창 보드");
  await page.getByRole("textbox", { name: "아이디어" }).fill("3.3V로 동작하는 CAN 노드. MCU와 UART로 연결하고 버스 종단 저항 포함.");
  await page.getByRole("button", { name: "AI로 요구사항 정리 →" }).click();
  await page.getByRole("button", { name: "다음: 설계 준비 →" }).click();
  await page.getByRole("button", { name: "AI 설계 시작" }).click();
  // Leave for another screen at once: the design keeps running in the backend.
  await page.getByRole("button", { name: "설정" }).click();
  const panel = page.getByLabel("작업 상태");
  await expect(panel).toBeVisible();
  const row = panel.locator(".job-row").filter({ hasText: "AI 설계" }).filter({ hasText: "상태창 보드" });
  await expect(row).toBeVisible();
  await expect(row).toContainText(/끝남 .*제안 #1 열기/, { timeout: 60_000 });
  await expect(panel.getByText("작업 끝남")).toBeVisible();
  // The finished job takes you back to its project, where the result is waiting.
  await row.getByRole("button", { name: /AI 설계/ }).click();
  await expect(page.locator(".flow-card").getByText("제안 #1 만듦", { exact: false }).first()).toBeVisible();
  await expect(panel.locator(".job-row").filter({ hasText: "요구사항 정리" })).toBeVisible();  // every recent job is listed
  await row.getByRole("button", { name: "목록에서 지우기" }).click();
  await expect(row).toHaveCount(0);
  // With nothing running, the list folds to its header so it does not cover the page; the header opens it again.
  await expect(panel.locator(".job-status-list")).toHaveCount(0, { timeout: 20_000 });
  await expect(panel.getByRole("button", { name: /작업 끝남 1개/ })).toBeVisible();
  await panel.getByRole("button", { name: /작업 끝남/ }).click();
  await expect(panel.locator(".job-row").filter({ hasText: "요구사항 정리" })).toBeVisible();
});
