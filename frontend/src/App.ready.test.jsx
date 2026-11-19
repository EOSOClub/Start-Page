import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

// <html data-ready> is the signal the MCP screenshot tool waits for: "page" once a dashboard came from
// the server, "login" when the login screen is showing. Widgets carry data-widget-id for element shots.
const state = vi.hoisted(() => ({ status: null }));

vi.mock("./api.js", () => {
  const page = { id: "p1", name: "Home", settings: {}, widgets: [{ id: "w1", type: "text", x: 0, y: 0, w: 6, h: 3, service_id: null, config: { content: "HELLO" } }] };
  const known = {
    authStatus: () => Promise.resolve(state.status),
    listDashboards: () => Promise.resolve([{ id: "p1", name: "Home" }]),
    getDashboard: () => Promise.resolve(page),
    revision: () => Promise.resolve({ boot: "b", rev: 0 }),
    listServices: () => Promise.resolve([]),
    health: () => Promise.resolve({}),
    listIntegrations: () => Promise.resolve([]),
    integrationData: () => Promise.resolve({}),
    getSettings: () => Promise.resolve({}),
  };
  return { api: new Proxy({}, { get: (_t, name) => known[name] || (() => Promise.resolve(null)) }) };
});

const mountApp = async () => {
  vi.resetModules();
  const { default: App } = await import("./App.jsx");
  return render(<App />);
};

describe("readiness marker", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("is 'page' only after the dashboard loaded, and widgets expose their id", async () => {
    state.status = { enabled: false, authenticated: false, guest_access: "none", can_write: true };
    const { container } = await mountApp();
    await waitFor(() => expect(document.documentElement.dataset.ready).toBe("page"));
    expect(container.querySelector('[data-widget-id="w1"]')).not.toBeNull();
    cleanup();
    expect(document.documentElement.dataset.ready).toBeUndefined();
  });

  it("is 'login' on the login screen", async () => {
    state.status = { enabled: true, has_password: true, authenticated: false, guest_access: "none", can_write: false };
    await mountApp();
    await screen.findByLabelText("Password");
    await waitFor(() => expect(document.documentElement.dataset.ready).toBe("login"));
  });
});
