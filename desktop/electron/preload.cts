// Preload bridge: the only way the sandboxed renderer reaches the desktop shell.

import { contextBridge, ipcRenderer } from "electron";

const config = ipcRenderer.sendSync("aics:config") as { token: string; platform: string } | null;

contextBridge.exposeInMainWorld("aics", {
  token: config?.token ?? null,
  platform: config?.platform ?? "unknown",
  openPath: (target: string): Promise<string> => ipcRenderer.invoke("aics:open-path", target),
});
