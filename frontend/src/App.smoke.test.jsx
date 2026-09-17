import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

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
});

// One Proxy answers every api.* name: known loaders return seeded data, anything else resolves null.
vi.mock("./api.js", () => {
  const page = globalThis.__smokePage;
  const known = {
    listDashboards: () => Promise.resolve([{ id: "p1", name: "Home" }]),
    getDashboard: () => Promise.resolve(page),
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
});
