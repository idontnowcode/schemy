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

test("a guide a board does not follow is an exception; company guides come in from a document", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);

  // A company design standard as a Markdown list: imported as candidates.
  await page.getByRole("button", { name: "설계 가이드" }).click();
  const guide = page.getByLabel("설계 가이드", { exact: true });
  await guide.getByLabel("가져올 설계 가이드 파일").setInputFiles({
    name: "company-standard.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("## 전원\n- [필수] 전원 커넥터 극성 표시: 실크에 +/- 를 적는다.\n- [권장] 테스트 포인트: 각 전원 레일에 테스트 포인트를 둔다.\n"),
  });
  await expect(guide.getByRole("status")).toContainText("지침 2개 중 2개를 후보로 가져왔습니다");
  await expect(guide).toContainText("전원 커넥터 극성 표시");

  // The I2C example records that it has no reverse-polarity diode on purpose (decision dec-norev waives G-PRT-01).
  await page.getByLabel("새 프로젝트 이름").fill("Exception Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("예제 불러오기").selectOption("i2c-sensor-3v3");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();
  await page.getByRole("tab", { name: "검토" }).click();
  const checklist = page.getByLabel("설계 지침 확인");
  const waived = checklist.getByRole("listitem").filter({ hasText: "G-PRT-01" });
  await expect(waived).toContainText("예외");
  await expect(waived).toContainText("설계 결정 「역극성 보호」");

  // Another guide this board does not follow: the reason goes into a proposal as a decision.
  await checklist.getByRole("button", { name: "예외로 기록" }).first().click();
  await checklist.getByLabel("예외 이유").fill("시제품 1장이라 이 보드에서는 생략한다.");
  await checklist.getByRole("button", { name: "예외 제안 만들기" }).click();
  await expect(page.getByRole("heading", { name: /지침 예외: G-/ })).toBeVisible();

  // The BOM names the pin column for what it is; stock lookup asks for distributor keys first.
  await page.getByRole("tab", { name: "문서 · BOM" }).click();
  await expect(page.getByRole("columnheader", { name: "핀 검토(엔지니어)" })).toBeVisible();
  const stock = page.getByLabel("유통사 재고·가격");
  await expect(stock.getByRole("button", { name: "유통사 재고·가격 조회" })).toBeDisabled();
  await expect(stock).toContainText("Mouser API 키나 DigiKey Client ID·Secret");
});
