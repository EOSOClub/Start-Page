// Regenerates mcp/widget_catalog.json from the widget registry (see src/widgets/catalog.test.js).
// A wrapper so the env var works in every shell, Windows included.
import { spawnSync } from "node:child_process";

const { status } = spawnSync("npx", ["vitest", "run", "src/widgets/catalog.test.js"], {
  stdio: "inherit",
  shell: true,
  env: { ...process.env, UPDATE_CATALOG: "1" },
});
process.exit(status ?? 1);
