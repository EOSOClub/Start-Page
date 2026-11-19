import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// Each test seeds localStorage and then imports a fresh App (it reads the cache at module scope).
const PAGE = {
  id: "p1",
  name: "Home",
  settings: {},
  widgets: [{ id: "w1", type: "text", x: 0, y: 0, w: 6, h: 3, service_id: null, config: { content: "CACHED-SECRET-PAGE" } }],
};
const LOCKED = { enabled: true, has_password: true, authenticated: false, guest_access: "none", can_write: false };
const GUEST = { ...LOCKED, guest_access: "read" };
const SIGNED_IN = { ...LOCKED, authenticated: true, can_write: true, method: "session" };

const calls = vi.hoisted(() => ({ status: null, login: null, getDashboard: 0, integrations: [] }));

vi.mock("./api.js", () => {
  const page = {
    id: "p1",
    name: "Home",
    settings: {},
    widgets: [{ id: "w1", type: "text", x: 0, y: 0, w: 6, h: 3, service_id: null, config: { content: "CACHED-SECRET-PAGE" } }],
  };
  const known = {
    authStatus: () => Promise.resolve(calls.status),
    login: (pw) => calls.login(pw),
    listDashboards: () => Promise.resolve([{ id: "p1", name: "Home" }]),
    getDashboard: () => {
      calls.getDashboard += 1;
      return Promise.resolve(page);
    },
    revision: () => Promise.resolve({ boot: "b", rev: 0 }),
    listServices: () => Promise.resolve([]),
    health: () => Promise.resolve({}),
    listIntegrations: () => Promise.resolve(calls.integrations),
    integrationData: () => Promise.resolve({}),
    getSettings: () => Promise.resolve({}),
  };
  return { api: new Proxy({}, { get: (_t, name) => known[name] || (() => Promise.resolve(null)) }) };
});

const seedCache = () => {
  localStorage.setItem(
    "sp.cache.v1",
    JSON.stringify({ dashboards: [{ id: "p1", name: "Home" }], pages: { p1: PAGE }, services: [], integrations: [], settings: {} })
  );
  localStorage.setItem("sp.dashboard", "p1");
};

const mountApp = async () => {
  vi.resetModules();
  const { default: App } = await import("./App.jsx");
  return render(<App />);
};

describe("App authentication", () => {
  beforeEach(() => {
    localStorage.clear();
    calls.getDashboard = 0;
    calls.integrations = [];
    calls.login = vi.fn(() => Promise.resolve({ ok: true }));
  });
  afterEach(cleanup);

  it("shows the login screen, not the cached dashboard, when a login is required", async () => {
    seedCache();
    calls.status = LOCKED;
    await mountApp();
    await screen.findByLabelText("Password");
    expect(screen.queryByText("CACHED-SECRET-PAGE")).toBeNull();
    expect(localStorage.getItem("sp.cache.v1")).toBeNull(); // wiped, not just hidden
  });

  it("does not paint the cache at all when the last known state was locked", async () => {
    seedCache();
    localStorage.setItem("sp.auth.v1", JSON.stringify(LOCKED));
    calls.status = LOCKED;
    await mountApp();
    expect(screen.queryByText("CACHED-SECRET-PAGE")).toBeNull();
    screen.getByLabelText("Password");
  });

  it("with proxy auth, an unreachable server shows a neutral state instead of the cached page", async () => {
    seedCache();
    localStorage.setItem("sp.auth.v1", JSON.stringify({ ...SIGNED_IN, method: "proxy" }));
    calls.status = Promise.reject(new TypeError("Failed to fetch"));
    calls.status.catch(() => {});
    await mountApp();
    await screen.findByText("Can't reach the server");
    expect(screen.queryByText("CACHED-SECRET-PAGE")).toBeNull();
  });

  it("with proxy auth, the cached page paints once the server confirms the session", async () => {
    seedCache();
    localStorage.setItem("sp.auth.v1", JSON.stringify({ ...SIGNED_IN, method: "proxy" }));
    calls.status = { ...SIGNED_IN, method: "proxy" };
    await mountApp();
    await screen.findByText("CACHED-SECRET-PAGE");
  });

  it("with password auth, an unreachable server still shows the last saved copy", async () => {
    seedCache();
    localStorage.setItem("sp.auth.v1", JSON.stringify(SIGNED_IN));
    calls.status = Promise.reject(new TypeError("Failed to fetch"));
    calls.status.catch(() => {});
    await mountApp();
    await screen.findByText("CACHED-SECRET-PAGE");
  });

  it("guest read mode shows the page without the Edit control", async () => {
    seedCache();
    calls.status = GUEST;
    await mountApp();
    await screen.findByRole("button", { name: "Sign in" });
    await screen.findByText("CACHED-SECRET-PAGE");
    expect(screen.queryByTitle("Toggle edit mode (E)")).toBeNull();
    fireEvent.keyDown(window, { key: "e" }); // the shortcut does nothing either
    expect(document.querySelector(".app-edit")).toBeNull();
    expect(screen.queryByText("Integrations")).toBeNull();
  });

  it("with auth off the page and Edit button are unchanged", async () => {
    seedCache();
    calls.status = { enabled: false, authenticated: false, guest_access: "none", can_write: true };
    await mountApp();
    await screen.findByText("CACHED-SECRET-PAGE");
    screen.getByTitle("Toggle edit mode (E)");
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  });

  it("a successful login loads the page", async () => {
    calls.status = LOCKED;
    await mountApp();
    const input = await screen.findByLabelText("Password");
    calls.status = SIGNED_IN;
    fireEvent.change(input, { target: { value: "hunter22hunter" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(calls.login).toHaveBeenCalledWith("hunter22hunter"));
    await screen.findByText("CACHED-SECRET-PAGE");
    screen.getByTitle("Toggle edit mode (E)");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
  });

  it("never writes integration configs (API keys, headers) to the local cache", async () => {
    calls.status = SIGNED_IN;
    calls.integrations = [{ id: "i1", name: "Kuma", type: "json", config: { url: "http://x", headers: { Key: "SECRET-API-KEY" } } }];
    await mountApp();
    await screen.findByText("CACHED-SECRET-PAGE");
    await waitFor(() => expect(localStorage.getItem("sp.cache.v1")).toContain('"Kuma"'));
    const raw = localStorage.getItem("sp.cache.v1");
    expect(raw).not.toContain("SECRET-API-KEY");
    expect(JSON.parse(raw).integrations).toEqual([{ id: "i1", name: "Kuma", type: "json", config: {} }]);
  });

  it("scrubs integration configs out of a cache written by an older build", async () => {
    seedCache();
    const cache = JSON.parse(localStorage.getItem("sp.cache.v1"));
    cache.integrations = [{ id: "i1", name: "Kuma", type: "json", config: { headers: { Key: "OLD-SECRET" } } }];
    localStorage.setItem("sp.cache.v1", JSON.stringify(cache));
    calls.status = SIGNED_IN;
    await mountApp();
    expect(localStorage.getItem("sp.cache.v1")).not.toContain("OLD-SECRET");
  });

  it("sign-out clears the cache and a late poll cannot bring it back", async () => {
    calls.status = SIGNED_IN;
    await mountApp();
    await screen.findByText("CACHED-SECRET-PAGE");
    await waitFor(() => expect(localStorage.getItem("sp.cache.v1")).not.toBeNull());
    calls.status = LOCKED;
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByLabelText("Password");
    expect(localStorage.getItem("sp.cache.v1")).toBeNull();
    await new Promise((r) => setTimeout(r, 50));
    expect(localStorage.getItem("sp.cache.v1")).toBeNull();
  });

  it("shows the wait on a 429", async () => {
    calls.status = LOCKED;
    calls.login = vi.fn(() => Promise.reject(Object.assign(new Error("Too many attempts"), { status: 429, retryAfter: 7 })));
    await mountApp();
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "whatever1" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText(/Try again in 7 s/);
    expect(screen.getByRole("button", { name: "Sign in" }).disabled).toBe(true);
  });
});
