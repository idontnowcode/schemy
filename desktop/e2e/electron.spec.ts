// The Electron shell: starts the backend with a per-launch token and keeps the renderer isolated.
// Needs a display; on Linux run under `xvfb-run -a npx playwright test e2e/electron.spec.ts`.

import { _electron as electron, expect, test } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";

import { backendPython, desktopDir, symbolDirs, tempWorkspace } from "./backend";

test.skip(process.platform === "linux" && !process.env.DISPLAY, "Electron needs a display (use xvfb-run)");

test("desktop shell runs the app with token auth and an isolated renderer", async () => {
  const main = path.join(desktopDir, "dist-electron", "main.cjs");
  test.skip(!existsSync(main), "run `npm run build` first");

  const asRoot = typeof process.getuid === "function" && process.getuid() === 0;
  const python = backendPython();
  const app = await electron.launch({
    args: [...(asRoot ? ["--no-sandbox"] : []), main],
    cwd: desktopDir,
    env: {
      ...process.env,
      AICS_WORKSPACE: tempWorkspace(),
      AICS_SYMBOL_DIRS: symbolDirs(),
      ...(python.prefix.length === 0 ? { AICS_BACKEND_PYTHON: python.cmd } : {}),
      ...(asRoot ? { AICS_ELECTRON_NO_SANDBOX: "1" } : {}),
    },
  });
  try {
    const win = await app.firstWindow();
    await expect(win).toHaveTitle("AI Circuit Studio");

    // The renderer has the bridge and token but no Node.js.
    expect(await win.evaluate(() => Boolean(window.aics?.token))).toBe(true);
    expect(await win.evaluate(() => typeof (window as unknown as { require?: unknown }).require)).toBe("undefined");
    expect(await win.evaluate(() => typeof (window as unknown as { process?: unknown }).process)).toBe("undefined");

    // The backend rejects requests without the token…
    expect(await win.evaluate(async () => (await fetch("/api/projects")).status)).toBe(401);
    // …and the UI, which sends it, works.
    await win.getByLabel("새 프로젝트 이름").fill("Electron Smoke");
    await win.getByRole("button", { name: "새 프로젝트" }).click();
    await expect(win.getByRole("heading", { name: "Electron Smoke" })).toBeVisible();

    // openPath refuses anything outside the workspace.
    const outside = await win.evaluate(() => window.aics!.openPath("/etc").then(() => "opened", (e: Error) => e.message));
    expect(outside).toContain("outside the workspace");
  } finally {
    await app.close();
  }
});
