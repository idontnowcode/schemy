// Electron main process: start the Python backend, then show its UI in a window.
//
// Security model (plan v1.2 section 14):
// - the backend listens on 127.0.0.1 only and requires a per-launch random token;
// - the token reaches the renderer through the preload bridge, never through the URL;
// - the renderer runs sandboxed with contextIsolation and no Node.js;
// - navigation away from the backend origin and new windows are blocked;
// - openPath only accepts paths inside the backend's workspace.

import { app, BrowserWindow, dialog, ipcMain, session, shell } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";
import readline from "node:readline";

const desktopDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(desktopDir, "..");
const uiDir = path.join(desktopDir, "dist");
const token = randomBytes(32).toString("hex");

let backend: ChildProcessWithoutNullStreams | null = null;
let backendUrl = "";
let workspace = "";
let quitting = false;

if (process.env.AICS_ELECTRON_NO_SANDBOX === "1") {
  // Only for containers/CI that run as root; never set this on a desktop machine.
  app.commandLine.appendSwitch("no-sandbox");
}

function backendCommand(): { cmd: string; args: string[] } {
  const args = ["-m", "backend", "--exit-on-stdin-eof", "--ui-dir", uiDir];
  if (process.env.AICS_BACKEND_PYTHON) return { cmd: process.env.AICS_BACKEND_PYTHON, args };
  return { cmd: "uv", args: ["run", "--project", repoRoot, "python", ...args] };
}

function startBackend(): Promise<{ url: string; workspace: string }> {
  return new Promise((resolve, reject) => {
    const { cmd, args } = backendCommand();
    const child = spawn(cmd, args, {
      cwd: repoRoot,
      env: { ...process.env, AICS_TOKEN: token },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    backend = child;
    let stderr = "";
    let ready = false;
    const timer = setTimeout(() => reject(new Error(`backend did not start within 90 s\n${stderr}`)), 90_000);

    child.stderr.on("data", (chunk: Buffer) => {
      stderr = (stderr + chunk.toString()).slice(-4000);
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (!ready) {
        reject(new Error(`backend exited with code ${code}\n${stderr}`));
      } else if (!quitting) {
        dialog.showErrorBox("AI Circuit Studio", `백엔드가 종료되었습니다 (code ${code}).\n\n${stderr}`);
        app.quit();
      }
    });

    readline.createInterface({ input: child.stdout }).on("line", (line) => {
      try {
        const msg = JSON.parse(line) as { event?: string; url?: string; workspace?: string };
        if (msg.event === "ready" && msg.url && msg.workspace) {
          ready = true;
          clearTimeout(timer);
          resolve({ url: msg.url, workspace: msg.workspace });
        }
      } catch {
        // not a protocol line
      }
    });
  });
}

function fromOurPage(frameUrl: string | undefined): boolean {
  return !!frameUrl && (frameUrl === backendUrl || frameUrl.startsWith(`${backendUrl}/`));
}

function registerIpc(): void {
  ipcMain.on("aics:config", (event) => {
    event.returnValue = fromOurPage(event.senderFrame?.url) ? { token, platform: process.platform } : null;
  });
  ipcMain.handle("aics:open-path", async (event, target: unknown) => {
    if (!fromOurPage(event.senderFrame?.url)) throw new Error("not allowed");
    if (typeof target !== "string") throw new Error("invalid path");
    const resolved = path.resolve(target);
    const root = path.resolve(workspace);
    if (resolved !== root && !resolved.startsWith(root + path.sep)) throw new Error("path is outside the workspace");
    return shell.openPath(resolved);
  });
}

function setContentSecurityPolicy(): void {
  const csp =
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
    "img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'";
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [csp] } });
  });
}

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    title: "AI Circuit Studio",
    backgroundColor: "#0f1216",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (event, target) => {
    if (!fromOurPage(target)) event.preventDefault();
  });
  void win.loadURL(`${backendUrl}/`);
}

app.whenReady().then(async () => {
  try {
    const started = await startBackend();
    backendUrl = started.url;
    workspace = started.workspace;
    registerIpc();
    setContentSecurityPolicy();
    createWindow();
  } catch (err) {
    dialog.showErrorBox("AI Circuit Studio", `백엔드를 시작하지 못했습니다.\n\n${String(err)}`);
    app.quit();
  }
});

app.on("window-all-closed", () => app.quit());

app.on("before-quit", () => {
  quitting = true;
  if (backend && backend.exitCode === null) {
    backend.stdin.end(); // the backend exits when stdin closes
    setTimeout(() => backend?.kill(), 3000).unref();
  }
});
