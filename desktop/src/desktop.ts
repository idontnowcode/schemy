// Bridge exposed by electron/preload.cts. Absent when the UI runs in a plain browser (development).

export interface DesktopBridge {
  token: string | null;
  platform: string;
  /** Opens a file or folder inside the workspace with the system default app (e.g. KiCad for .kicad_pro). */
  openPath(path: string): Promise<string>;
}

declare global {
  interface Window {
    aics?: DesktopBridge;
  }
}

export const desktop: DesktopBridge | null = typeof window !== "undefined" ? (window.aics ?? null) : null;
