// Datasheets in the built UI against a real backend: upload a PDF for an IC, import its summary,
// see the pin map / voltage / external-part checks, then find it in the workspace library.
// No download from the internet happens here (only uploads of test files).

import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

import { repoRoot, startBackend, tempWorkspace, type Backend } from "./backend";

let backend: Backend | null = null;

test.afterEach(async () => {
  await backend?.stop();
  backend = null;
});

const fixtures = path.join(repoRoot, "tests", "fixtures", "datasheets");

test("upload a datasheet, import its summary and see the checks", async ({ page }) => {
  backend = await startBackend(tempWorkspace());
  await page.goto(backend.url);
  await page.getByLabel("새 프로젝트 이름").fill("CAN Node");
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await expect(page.getByRole("heading", { name: "CAN Node" })).toBeVisible();

  // Save the CAN circuit (U1 = SN65HVD230 with maker and part number).
  await page.getByRole("tab", { name: "회로 (IR)" }).click();
  await page.getByLabel("Circuit IR JSON").fill(readFileSync(path.join(fixtures, "can-node.circuit.json"), "utf-8"));
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByText("revision 1으로 저장했습니다.")).toBeVisible();

  // Datasheet tab: U1 is listed (passives are hidden), its KiCad symbol has a link.
  await page.getByRole("tab", { name: "데이터시트" }).click();
  const parts = page.getByRole("table", { name: "부품별 데이터시트" });
  const u1 = parts.getByRole("row").filter({ hasText: "SN65HVD230DR" });
  await expect(u1).toContainText("제조사·품번 확정");
  await expect(u1).toContainText("링크 있음 (받기 전)");
  await expect(parts.getByRole("row").filter({ hasText: "C1" })).toHaveCount(0);
  // Where to get it: the KiCad symbol link first, then TI's own address pattern. No search keys: the search is off.
  const finder = page.getByLabel("데이터시트 찾기");
  const where = finder.getByLabel("받을 곳: U1");
  await expect(where.locator("option")).toHaveText([
    "KiCad 링크: www.ti.com · SN65HVD230",
    "제조사 사이트: www.ti.com · SN65HVD230DR",
    "받지 않음",
  ]);
  await expect(finder.getByRole("button", { name: "선택한 곳에서 받기 (1)" })).toBeEnabled();
  await expect(finder.getByRole("button", { name: "유통사에서 찾기 (Mouser·DigiKey)" })).toBeDisabled();

  // Upload the (test) PDF for U1.
  let chooser = page.waitForEvent("filechooser");
  await u1.getByRole("button", { name: "PDF 올리기" }).click();
  await (await chooser).setFiles(path.join(fixtures, "sn65hvd230-test.pdf"));
  await expect(page.getByText(/U1: sn65hvd230-test.pdf을\(를\) 저장했습니다 \(품번 확인됨\)/)).toBeVisible();
  await expect(page.getByLabel("데이터시트 찾기")).toHaveCount(0); // nothing left to get

  // An empty summary for U1 to fill in from the datasheet: maker, part number and pin numbers only.
  const download = page.waitForEvent("download");
  await u1.getByRole("button", { name: "요약 틀 받기" }).click();
  const skeleton = await download;
  expect(skeleton.suggestedFilename()).toBe("SN65HVD230DR-datasheet-summary.md");
  const skeletonText = readFileSync(await skeleton.path(), "utf-8");
  expect(skeletonText).toContain("| 품번 | SN65HVD230DR |");
  expect(skeletonText).not.toContain("| CANH |");

  // Import the summary Markdown: the checks appear.
  chooser = page.waitForEvent("filechooser");
  await u1.getByRole("button", { name: "요약 MD 가져오기" }).click();
  await (await chooser).setFiles(path.join(fixtures, "sn65hvd230-test.md"));
  await expect(page.getByText("U1: 요약을 저장했습니다.")).toBeVisible();
  await expect(u1).toContainText("핀맵 일치");
  await expect(u1).toContainText("전압 정상");
  await expect(u1).toContainText("외부 부품 있음");
  await expect(u1).toContainText("원문 미확인");
  await expect(page.getByRole("heading", { name: /핀맵 대조/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: "C1 100nF", exact: true })).toBeVisible();
  await expect(page.getByText("Rs를 GND에 직접 연결하면 고속 모드로 동작한다")).toBeVisible();

  await u1.getByRole("button", { name: "원문 확인함" }).click();
  await expect(u1.getByText("원문 확인함", { exact: true }).first()).toBeVisible();
  await expect(u1.getByRole("button", { name: "확인 취소" })).toBeVisible();

  // The library page lists the file for every project.
  await page.getByRole("button", { name: "데이터시트", exact: true }).click();
  await expect(page.getByRole("heading", { name: "데이터시트" })).toBeVisible();
  await expect(page.getByRole("cell", { name: /sn65hvd230-test\.pdf/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: /Texas Instruments/ })).toBeVisible();
});
