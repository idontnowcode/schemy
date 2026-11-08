// Start and stop a real backend process for end-to-end tests.

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(here, "..", "..");
export const desktopDir = path.resolve(here, "..");
export const fixtureSymbols = path.join(repoRoot, "tests", "fixtures", "kicad_symbols");

/** Python of the repo's virtualenv when present (faster than `uv run`). */
export function backendPython(): { cmd: string; prefix: string[] } {
  const venv =
    process.platform === "win32"
      ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
      : path.join(repoRoot, ".venv", "bin", "python");
  return existsSync(venv) ? { cmd: venv, prefix: [] } : { cmd: "uv", prefix: ["run", "--project", repoRoot, "python"] };
}

export function tempWorkspace(): string {
  return mkdtempSync(path.join(tmpdir(), "aics-e2e-"));
}

/** Symbol libraries: AICS_SYMBOL_DIRS if set (full KiCad install), else the test subset. */
export function symbolDirs(): string {
  return process.env.AICS_SYMBOL_DIRS || fixtureSymbols;
}

export function kicadExpected(): boolean {
  return Boolean(process.env.AICS_KICAD_CLI || process.env.AICS_KICAD_DOCKER_IMAGE);
}

export interface Backend {
  url: string;
  stop(): Promise<void>;
}

/** ``fakeAi`` runs the test-only launcher (tests/e2e_backend.py) with a scripted AI instead of the real app entry. */
export function startBackend(workspace: string, opts: { fakeAi?: boolean } = {}): Promise<Backend> {
  const { cmd, prefix } = backendPython();
  const args = [...prefix, "-m", opts.fakeAi ? "tests.e2e_backend" : "backend", "--no-auth", "--exit-on-stdin-eof",
    "--workspace", workspace, "--ui-dir", path.join(desktopDir, "dist")];
  const child: ChildProcessWithoutNullStreams = spawn(cmd, args, {
    cwd: repoRoot,
    env: { ...process.env, AICS_SYMBOL_DIRS: symbolDirs() },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", (d: Buffer) => (stderr += d.toString()));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`backend did not start\n${stderr}`)), 60_000);
    child.on("exit", (code) => reject(new Error(`backend exited (${code})\n${stderr}`)));
    readline.createInterface({ input: child.stdout }).on("line", (line) => {
      try {
        const msg = JSON.parse(line) as { event?: string; url?: string };
        if (msg.event === "ready" && msg.url) {
          clearTimeout(timer);
          resolve({
            url: msg.url,
            stop: () =>
              new Promise<void>((done) => {
                if (child.exitCode !== null) return done();
                child.once("exit", () => done());
                child.stdin.end();
              }),
          });
        }
      } catch {
        // ignore non-protocol output
      }
    });
  });
}

/** Open one stage of the proposal review (the slides: 개요, 바뀌는 내용, 자동 검사, AI 검토, 데이터시트, 설계 지침, 결정). */
export async function stage(page: import("@playwright/test").Page, name: string): Promise<void> {
  await page.getByLabel("검토 단계").getByRole("button", { name: new RegExp(`\\d ${name}`) }).click();
}

/** Create a project from the sidebar: 「+ 새 프로젝트」 → name → 만들기. */
export async function newProject(page: import("@playwright/test").Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "새 프로젝트" }).click();
  await page.getByLabel("새 프로젝트 이름").fill(name);
  await page.getByRole("button", { name: "만들기" }).click();
}

const GROUP_OF: Record<string, string> = {
  요구사항: "설계", 템플릿: "설계", 제안: "설계", "회로 데이터": "설계",
  "회로도 · 검증": "검증", 검토: "검증", Agent: "검증",
  자재: "자료", 데이터시트: "자료", "문서 · BOM": "자료",
  이력: "기록",
};

/** Open one tab of the advanced screen: switch to it (from the step screens) and to the tab's group first. */
export async function openTab(page: import("@playwright/test").Page, name: string): Promise<void> {
  const advanced = page.getByRole("button", { name: "고급 화면(탭)" });
  const groups = page.getByRole("tablist", { name: "묶음" });
  await advanced.or(groups).first().waitFor(); // the project screen has rendered, in either mode
  if (await advanced.isVisible()) await advanced.click();
  await groups.getByRole("tab", { name: GROUP_OF[name], exact: true }).click();
  await page.getByRole("tab", { name, exact: true }).click();
}
