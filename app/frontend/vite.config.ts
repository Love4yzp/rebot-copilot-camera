import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { mockPreview } from "./mock/plugin";

// The dev server proxies to the backend so the browser sees one origin, which
// keeps the websocket and the REST calls on the same host in dev and in prod.
// Production builds land in backend/static/, mounted after every route.
//
// `--mode mock` (`npm run dev:mock`) drops the proxy and mounts the mockPreview
// plugin instead: it answers /api and /ws from in-memory state and serves
// /assets/urdf from the vendored submodule, so the whole UI — 3D view included —
// runs without the backend service.
export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === "mock" ? [mockPreview()] : [])],
  build: { outDir: "../backend/static", emptyOutDir: true },
  server: {
    proxy:
      mode === "mock"
        ? undefined
        : {
            "/api": "http://127.0.0.1:18790",
            "/assets/urdf": "http://127.0.0.1:18790",
            "/ws": { target: "ws://127.0.0.1:18790", ws: true },
          },
  },
}));
