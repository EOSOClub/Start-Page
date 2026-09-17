import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Standalone test config: vite.config.js (dev proxy, build outDir) is not used by vitest.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.js"],
  },
});
