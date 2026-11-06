// Research notes (연구노트): the (scripted) AI writes a draft from the review evidence on the 검토 tab, a person reviews
// it, turns a lesson into a design-guide candidate, finds it in the notebook and writes a note of their own.

import { expect, test } from "@playwright/test";

import { startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

test("AI writes a research note from the review; people review it and reuse it", async ({ page }) => {
  backend = await startBackend(tempWorkspace(), { fakeAi: true });
  await page.goto(backend.url);
  await page.request.put(`${backend.url}/api/ai/settings`, { data: { enabled: true } });
  await page.getByLabel("새 프로젝트 이름").fill("Notes Demo");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("예제 불러오기").selectOption("i2c-sensor-3v3");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  // 검토 tab: the AI writes a draft; the confirmation lists what is sent.
  await page.getByRole("tab", { name: "검토" }).click();
  const card = page.getByLabel("연구노트", { exact: true });
  await card.getByLabel("노트에서 다룰 것").fill("전원 입력");
  let asked = "";
  page.once("dialog", (d) => {
    asked = d.message();
    void d.accept();
  });
  await card.getByRole("button", { name: "AI로 연구노트 쓰기" }).click();
  await expect(card.getByRole("status")).toContainText("연구노트 N-0001(초안)를 썼습니다", { timeout: 30_000 });
  expect(asked).toContain("앱이 찾은 근거");
  expect(asked).toContain('다룰 것: "전원 입력"');
  const note = card.getByLabel("연구노트 N-0001");
  await expect(note).toContainText("AI 초안");
  await expect(note.locator("article")).toContainText("배운 점·깨달은 점");
  await expect(note.locator("article")).toContainText("(근거: E1 계산)");
  await expect(note.locator("article")).toContainText("근거 없음: AI의 일반 지식, 확인 필요");

  // A person reviews it and sends the lesson to the design guide (as a candidate).
  await note.getByLabel("노트 기록할 이름").fill("김엔지니어");
  await note.getByRole("button", { name: "검토함으로 표시" }).click();
  await expect(note.getByText("검토함", { exact: true })).toBeVisible();
  await note.getByRole("button", { name: "지침 후보로" }).click();
  await expect(note).toContainText("설계 지침 G-0001(후보)로 남김");

  // The notebook: search, and a note of one's own.
  await page.getByRole("button", { name: "연구노트", exact: true }).click();
  const book = page.getByLabel("연구노트", { exact: true });
  await expect(book.getByLabel("노트 N-0001", { exact: true })).toContainText("Notes Demo / 보드 A / rev 1");
  await book.getByLabel("기록할 이름").fill("박팀장");
  await book.getByRole("button", { name: "노트 쓰기" }).click();
  await book.getByLabel("노트 제목").fill("I2C 풀업은 버스에 한 벌만");
  await book.getByLabel("배운 점").fill("센서 모듈에 풀업이 있으면 보드 풀업과 병렬이 된다.");
  await book.getByRole("button", { name: "저장", exact: true }).click();
  await expect(book.getByLabel("노트 N-0002", { exact: true })).toContainText("검토함");
  await book.getByLabel("노트 검색").fill("병렬");
  await expect(book.getByLabel("노트 N-0002", { exact: true })).toBeVisible();
  await expect(book.getByLabel("노트 N-0001", { exact: true })).toHaveCount(0);
});
