import { describe, expect, it, vi } from "vitest";
import { UPDATE_EVENT, askWorkerBuild, readPageBuild, registerServiceWorker, shouldOfferReload } from "./register.js";

function env({ prod = true, secure = true, status = 200, controller = null, fetchThrows = false, hasSw = true } = {}) {
  const listeners = {};
  const regs = [{ unregister: vi.fn(async () => true) }, { unregister: vi.fn(async () => true) }];
  const sw = {
    controller,
    register: vi.fn(async () => ({})),
    getRegistrations: vi.fn(async () => regs),
    addEventListener: (type, fn) => (listeners[type] = fn),
  };
  const dispatched = [];
  const win = { isSecureContext: secure, dispatchEvent: (e) => dispatched.push(e.type), CustomEvent: class { constructor(type) { this.type = type; } } };
  const store = { "startpage-a": 1, "startpage-b": 1, other: 1 };
  const cacheStorage = { keys: async () => Object.keys(store), delete: vi.fn(async (k) => delete store[k]) };
  const fetchFn = vi.fn(async () => {
    if (fetchThrows) throw new TypeError("offline");
    return { status };
  });
  const nav = hasSw ? { serviceWorker: sw } : {};
  return { args: { prod, nav, win, fetchFn, cacheStorage }, sw, regs, listeners, dispatched, store, fetchFn, cacheStorage };
}

describe("registerServiceWorker", () => {
  it("registers in production on a secure context", async () => {
    const e = env();
    expect(await registerServiceWorker(e.args)).toBe("registered");
    expect(e.sw.register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
  });

  it("does nothing outside production", async () => {
    const e = env({ prod: false });
    expect(await registerServiceWorker(e.args)).toBe("skipped");
    expect(e.sw.register).not.toHaveBeenCalled();
    expect(e.fetchFn).not.toHaveBeenCalled();
  });

  it("does nothing on an insecure context", async () => {
    const e = env({ secure: false });
    expect(await registerServiceWorker(e.args)).toBe("skipped");
    expect(e.sw.register).not.toHaveBeenCalled();
  });

  it("does nothing where service workers do not exist", async () => {
    const e = env({ hasSw: false });
    expect(await registerServiceWorker(e.args)).toBe("skipped");
  });

  it("unregisters every worker and clears the app's caches when /sw.js is a 404", async () => {
    const e = env({ status: 404 });
    expect(await registerServiceWorker(e.args)).toBe("unregistered");
    for (const r of e.regs) expect(r.unregister).toHaveBeenCalled();
    expect(Object.keys(e.store)).toEqual(["other"]); // only startpage-* caches
    expect(e.sw.register).not.toHaveBeenCalled();
  });

  it("other statuses and being offline do not trigger the kill switch", async () => {
    for (const opts of [{ status: 500 }, { status: 403 }, { fetchThrows: true }]) {
      const e = env(opts);
      expect(await registerServiceWorker(e.args)).toBe("registered");
      for (const r of e.regs) expect(r.unregister).not.toHaveBeenCalled();
    }
  });

  it("reports a failed registration without throwing", async () => {
    const e = env();
    e.sw.register.mockRejectedValue(new Error("nope"));
    expect(await registerServiceWorker(e.args)).toBe("failed");
  });

  it("announces an update only when a worker was already in control", async () => {
    const first = env({ controller: null });
    await registerServiceWorker(first.args);
    first.listeners.controllerchange(); // first install claiming the page
    expect(first.dispatched).toEqual([]);

    const update = env({ controller: {} });
    await registerServiceWorker(update.args);
    update.listeners.controllerchange();
    update.listeners.controllerchange(); // once only
    expect(update.dispatched).toEqual([UPDATE_EVENT]);
  });
});

describe("the update prompt and build ids", () => {
  it("shouldOfferReload: only silent when both builds are known and equal", () => {
    expect(shouldOfferReload("aaa", "aaa")).toBe(false); // the page was fetched with this build
    expect(shouldOfferReload("aaa", "bbb")).toBe(true);
    expect(shouldOfferReload("", "bbb")).toBe(true);
    expect(shouldOfferReload("aaa", "")).toBe(true);
    expect(shouldOfferReload(undefined, undefined)).toBe(true);
  });

  it("readPageBuild reads the meta tag and is empty without one", () => {
    const doc = (content) => ({ querySelector: (sel) => (sel === 'meta[name="startpage-build"]' && content ? { content } : null) });
    expect(readPageBuild(doc("abc123def456"))).toBe("abc123def456");
    expect(readPageBuild(doc(""))).toBe("");
    expect(readPageBuild(undefined)).toBe("");
  });

  it("askWorkerBuild returns the worker's answer, or '' when it cannot answer", async () => {
    const channel = () => ({ port1: {}, port2: {} });
    const answering = { postMessage: vi.fn((msg, [port]) => void 0) };
    const ch = channel();
    const p = askWorkerBuild({ postMessage: (msg, ports) => { expect(msg).toEqual({ type: "build-id" }); ch.port1.onmessage({ data: { build: "xyz" } }); } }, { channel: () => ch, ms: 50 });
    expect(await p).toBe("xyz");
    expect(await askWorkerBuild(null)).toBe("");
    expect(await askWorkerBuild({}, { ms: 10 })).toBe("");
    expect(await askWorkerBuild(answering, { channel, ms: 10 })).toBe(""); // never answers
    expect(await askWorkerBuild({ postMessage: () => { throw new Error("dead"); } }, { channel, ms: 10 })).toBe("");
  });

  it("no prompt when the page already is the build the new worker belongs to", async () => {
    const same = env({ controller: {} });
    await registerServiceWorker({ ...same.args, pageBuild: "b1", askBuild: async () => "b1" });
    same.listeners.controllerchange();
    await new Promise((r) => setTimeout(r, 0));
    expect(same.dispatched).toEqual([]);

    const newer = env({ controller: {} });
    await registerServiceWorker({ ...newer.args, pageBuild: "b1", askBuild: async () => "b2" });
    newer.listeners.controllerchange();
    newer.listeners.controllerchange();
    await new Promise((r) => setTimeout(r, 0));
    expect(newer.dispatched).toEqual([UPDATE_EVENT]);

    const unknown = env({ controller: {} });
    await registerServiceWorker({ ...unknown.args, pageBuild: "b1", askBuild: async () => "" });
    unknown.listeners.controllerchange();
    await new Promise((r) => setTimeout(r, 0));
    expect(unknown.dispatched).toEqual([UPDATE_EVENT]);
  });
});
