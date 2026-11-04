import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on the Vite dev server and talks to a backend started with
//   uv run python -m backend --no-auth --port 8765
// In the desktop app the backend serves the built UI itself (same origin, no proxy).
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
