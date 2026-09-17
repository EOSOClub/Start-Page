import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

// App.jsx reads sp.cache.v1 / sp.dashboard at module scope, so the seed must be hoisted above the import.
vi.hoisted(() => {
  const page = {
    id: "p1",
    name: "Home",
    settings: {},
    widgets: [
      { id: "w1", type: "text", x: 0, y: 0, w: 6, h: 3, service_id: null, config: { content: "# SMOKE-SURVIVOR\n\n**bold**" } },
      { id: "w2", type: "link", x: 6, y: 0, w: 3, h: 1, service_id: null, config: { url: "https://example.com", name: "Example" } },
    ],
  };
  localStorage.setItem(
    "sp.cache.v1",
    JSON.stringify({ dashboards: [{ id: "p1", name: "Home" }], pages: { p1: page }, services: [], integrations: [], settings: {} })
  );
  localStorage.setItem("sp.dashboard", "p1");
  globalThis.__smokePage = page;
  globalThis.__smokeRev = { boot: "b1", rev: 0 };
  globalThis.__smokeLoads = 0;
});

// One Proxy answers every api.* name: known loaders return seeded data, anything else resolves null.
vi.mock("./api.js", () => {
  const page = globalThis.__smokePage;
  const known = {
    listDashboards: () => Promise.resolve([{ id: "p1", name: "Home" }]),
    getDashboard: () => {
      globalThis.__smokeLoads += 1;
      return Promise.resolve(page);
    },
    revision: () => Promise.resolve(globalThis.__smokeRev),
    listServices: () => Promise.resolve([]),
    health: () => Promise.resolve({}),
    listIntegrations: () => Promise.resolve([]),
    integrationData: () => Promise.resolve({}),
    getSettings: () => Promise.resolve({}),
  };
  return { api: new Proxy({}, { get: (_t, name) => known[name] || (() => Promise.resolve(null)) }) };
});

import App from "./App.jsx";

describe("App smoke", () => {
  afterEach(cleanup); // no vitest globals, so RTL doesn't unmount on its own
  it("mounts App -> Canvas -> grid with the seeded widgets", async () => {
    const { container } = render(<App />);
    screen.getByText("SMOKE-SURVIVOR");
    expect(container.querySelector(".react-grid-layout")).not.toBeNull();
    // Yield past the mocked network round and first frames, then assert again.
    await new Promise((r) => setTimeout(r, 300));
    await waitFor(() => screen.getByText("SMOKE-SURVIVOR"));
    expect(container.querySelector(".react-grid-layout")).not.toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.textContent).not.toContain("failed to render");
  });

  it("reloads the page when the external revision moves", async () => {
    render(<App />);
    await new Promise((r) => setTimeout(r, 300));
    const before = globalThis.__smokeLoads;
    globalThis.__smokeRev = { boot: "b1", rev: 1 };
    await waitFor(() => screen.getByText(/Page updated outside this tab/), { timeout: 5000 });
    expect(globalThis.__smokeLoads).toBeGreaterThan(before);
  }, 10_000);
});
