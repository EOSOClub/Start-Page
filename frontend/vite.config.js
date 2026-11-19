import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { swPlugin } from "./vite-plugin-sw.js";

export default defineConfig({
  plugins: [react(), swPlugin()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8471", "/manifest.webmanifest": "http://127.0.0.1:8471" },
  },
  build: { outDir: "../backend/static", emptyOutDir: true },
});
