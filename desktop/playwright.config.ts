import { defineConfig } from "@playwright/test";

// e2e/app.spec.ts drives the built UI in Chromium against a real backend process.
// e2e/electron.spec.ts launches the Electron shell (needs a display; on Linux run under xvfb-run).
// Build first: npm run build
export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: { headless: true, viewport: { width: 1400, height: 900 } },
});
