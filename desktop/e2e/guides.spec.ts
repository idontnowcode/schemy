// The design guide: built-in entries, writing one, the checklist on a board, feedback that becomes a candidate,
// adopting it, and a rejected proposal's reason kept as a candidate. No AI is called here.

import { expect, test } from "@playwright/test";

import { startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("design guide grows from people's feedback and is checked on a board", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);

  // The built-in guide is there and adopted.
  await page.getByRole("button", { name: "설계 가이드" }).click();
  const guide = page.getByLabel("설계 가이드", { exact: true });
  await expect(guide.getByLabel("지침 G-MOT-01")).toContainText("모터 드라이버 공급 전압에 여유를 둔다");
  await guide.getByLabel("기록할 이름").fill("김엔지니어");
  await guide.getByLabel("기록할 이름").blur();

  // A person writes one and adopts it at once.
  await guide.getByRole("button", { name: "지침 추가" }).click();
  await guide.getByLabel("지침 제목").fill("모터 전원 입력에 TVS");
  await guide.getByLabel("지침 내용").fill("12 V 모터 전원 입력에 TVS 다이오드를 둔다.");
  await guide.getByLabel("지침 이유").fill("모터 감속 때 전원이 튄다.");
  await guide.getByRole("checkbox", { name: "모터 구동" }).check();
  await guide.getByRole("checkbox", { name: "바로 채택 (내가 쓴 지침)" }).check();
  await guide.getByRole("button", { name: "저장", exact: true }).click();
  await expect(guide.getByLabel("지침 G-0001")).toContainText("모터 전원 입력에 TVS");

  // A board with the motor example: the 검토 tab shows the guides that apply.
  await page.getByLabel("새 프로젝트 이름").fill("Guide Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("예제 불러오기").selectOption("dc-motor-drv8871");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();
  await page.getByRole("tab", { name: "검토" }).click();
  const checklist = page.getByLabel("설계 지침 확인");
  await expect(checklist).toContainText("G-PRT-03"); // TVS: a person checks it
  await checklist.getByRole("checkbox", { name: "자동 검사를 통과한 것도 보기" }).check();
  await expect(checklist).toContainText("G-PRT-02"); // over-current: checked by the fuse rule (PTC 2.5 A vs 1 A)
  await expect(checklist).toContainText("모터 전원 입력에 TVS");

  // Feedback from the review becomes a candidate (written as is, no AI).
  await checklist.getByLabel("피드백").fill("BOOT0 같은 부트 핀은 항상 10k로 풀다운한다.");
  await checklist.getByRole("button", { name: "지침으로 정리하기" }).click();
  await checklist.getByLabel("지침 제목").fill("부트 핀은 10k 풀다운");
  await checklist.getByRole("button", { name: "저장", exact: true }).click();
  await expect(checklist.getByRole("status")).toContainText("후보로 저장했습니다");

  // Adopt it on the guide page.
  await page.getByRole("button", { name: "설계 가이드" }).click();
  await guide.getByLabel("상태로 거르기").selectOption("candidate");
  const card = guide.getByLabel("지침 G-0002");
  await expect(card).toContainText("부트 핀은 10k 풀다운");
  await card.getByRole("button", { name: "채택" }).click();
  await guide.getByLabel("상태로 거르기").selectOption("adopted");
  await expect(guide.getByLabel("지침 G-0002")).toContainText("채택");
  await guide.getByLabel("지침 G-0002").getByRole("button", { name: "이력" }).click();
  await expect(guide.getByLabel("지침 G-0002")).toContainText("채택 · 김엔지니어");

  // A rejected proposal's reason is kept as a candidate.
  const projects = await (await page.request.get(`${backend.url}/api/projects`)).json();
  const pid = projects[0].id as string;
  await page.request.post(`${backend.url}/api/projects/${pid}/boards/a/proposals`, {
    data: { title: "LED 빼기", ops: [{ op: "remove_component", component_id: "d-led", expect_reference: "D3" }] },
  });
  await page.getByRole("button", { name: "Guide Demo" }).first().click();
  await page.getByRole("tab", { name: "제안" }).click();
  await page.getByText("LED 빼기").first().click();
  await page.getByRole("button", { name: "거부", exact: true }).click();
  await page.getByLabel("거부 이유").fill("전원 표시 LED는 항상 남긴다.");
  await page.getByRole("checkbox", { name: "설계 지침 후보로도 남기기" }).check();
  await page.getByRole("button", { name: "거부 확정" }).click();
  await expect(page.getByText(/설계 지침 후보 G-0003로 남겼습니다/)).toBeVisible();
});
