import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies to the backend so the browser sees one origin, which
// keeps the websocket and the REST calls on the same host in dev and in prod.
// Production builds land in backend/static/, mounted after every route.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/static", emptyOutDir: true },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:18790",
      "/assets/urdf": "http://127.0.0.1:18790",
      "/ws": { target: "ws://127.0.0.1:18790", ws: true },
    },
  },
});
