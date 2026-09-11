import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/static", emptyOutDir: true },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:18790",
      "/viewer": { target: "http://127.0.0.1:18790", ws: true },
      "/ws": { target: "ws://127.0.0.1:18790", ws: true },
    },
  },
});
