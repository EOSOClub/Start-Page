import { readFileSync } from "node:fs";
import vm from "node:vm";
import { describe, expect, it, vi } from "vitest";
import { CACHE_PREFIX, assetUrls, cacheable, decide, isServerError, isShell, normalizePath, shellBuildId, shellMatchesBuild } from "./routing.js";
import { composeSw } from "./compose.js";

const ORIGIN = "https://start.example";
const ask = (path, extra = {}) => decide({ method: "GET", url: ORIGIN + path, mode: "no-cors", origin: ORIGIN, ...extra });

describe("decide: what the worker may touch", () => {
  it("never touches /api, in any spelling", () => {
    for (const p of [
      "/api", "/api/", "/api/dashboards", "/api/auth/status", "/api/config/export", "/api/backups", "/api/docs",
      "/API/dashboards", "//api/dashboards", "/%61pi/dashboards", "/%41PI/x", "/api%2Fx", "/assets/../api/dashboards",
      "/api/dashboards?x=1", "/api/health#frag",
    ]) {
      expect(ask(p), p).toBe("bypass");
      expect(ask(p, { mode: "navigate" }), `${p} (navigation)`).toBe("bypass");
    }
  });
  it("never touches the manifest or the worker script", () => {
    for (const p of ["/manifest.webmanifest", "/sw.js", "/SW.JS", "/manifest.webmanifest?v=1"]) {
      expect(ask(p), p).toBe("bypass");
      expect(ask(p, { mode: "navigate" }), p).toBe("bypass");
    }
  });
  it("never touches anything but GET", () => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
      expect(ask("/assets/index-abc.js", { method }), method).toBe("bypass");
      expect(ask("/", { method, mode: "navigate" }), method).toBe("bypass");
    }
    expect(ask("/api/dashboards", { method: "get" })).toBe("bypass"); // case does not matter, /api still does
  });
  it("never touches other origins, range requests or unparsable URLs", () => {
    expect(decide({ method: "GET", url: "https://evil.example/assets/x.js", mode: "no-cors", origin: ORIGIN })).toBe("bypass");
    expect(ask("/assets/video.mp4", { range: true })).toBe("bypass");
    expect(decide({ method: "GET", url: "not a url", mode: "navigate", origin: ORIGIN })).toBe("bypass");
  });
  it("caches build assets and answers navigations; leaves the rest alone", () => {
    expect(ask("/assets/index-abc123.js")).toBe("assets");
    expect(ask("/assets/index-abc123.css")).toBe("assets");
    expect(ask("/", { mode: "navigate" })).toBe("navigate");
    expect(ask("/index.html", { mode: "navigate" })).toBe("navigate");
    expect(ask("//index.html", { mode: "navigate" })).toBe("navigate");
    expect(ask("/?x=1", { mode: "navigate" })).toBe("navigate");
    expect(ask("/icon-192.png")).toBe("bypass");
    expect(ask("/favicon.ico")).toBe("bypass");
  });
});

describe("navigations to other paths on the origin are not the worker's", () => {
  it("bypasses every path but / and /index.html", () => {
    for (const p of ["/some/page", "/grafana/", "/pihole/admin", "/does-not-exist", "/index.html/x", "/Grafana/d/1", "/assets", "/icon-192.png"]) {
      expect(ask(p, { mode: "navigate" }), p).toBe("bypass");
    }
  });
});

describe("normalizePath", () => {
  it("reads the path the way the server does", () => {
    expect(normalizePath("//API/x")).toBe("/api/x");
    expect(normalizePath("/%61pi/x")).toBe("/api/x");
    expect(normalizePath("/%E0%A4%A")).toBe("/%e0%a4%a"); // malformed escape: kept, no throw
  });
});

// Node's own Response, as a browser would hand it over (type/redirected are set by the browser, so they are overridden here).
const res = ({ status = 200, type = "basic", redirected = false } = {}, headers = {}, body = "") => {
  const make = (r) => {
    Object.defineProperty(r, "type", { value: type });
    Object.defineProperty(r, "redirected", { value: redirected });
    const clone = r.clone.bind(r);
    r.clone = () => make(clone()); // a browser's clone keeps type and redirected
    return r;
  };
  return make(new Response([204, 304].includes(status) ? null : body, { status, headers }));
};
const BUILD_HTML = (id) => `<!doctype html><html><head><meta charset="utf-8" /><meta name="startpage-build" content="${id}" /></head><body><div id="root"></div></body></html>`;
const HTML_HEADERS = { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" };
const shellRes = (id = "build1") => res({}, HTML_HEADERS, BUILD_HTML(id));

describe("what may be stored", () => {
  it("accepts plain 200 same-origin responses only", () => {
    expect(cacheable(res())).toBe(true);
    expect(cacheable(res({ status: 404 }))).toBe(false);
    expect(cacheable(res({ status: 206 }))).toBe(false);
    expect(cacheable(res({ type: "opaque" }))).toBe(false);
    expect(cacheable(res({ type: "cors" }))).toBe(false);
    expect(cacheable(res({ redirected: true }))).toBe(false);
    expect(cacheable(null)).toBe(false);
  });
  it("refuses no-store and private", () => {
    expect(cacheable(res({}, { "cache-control": "no-store" }))).toBe(false);
    expect(cacheable(res({}, { "cache-control": "private, no-cache" }))).toBe(false);
    expect(cacheable(res({}, { "cache-control": "public, max-age=31536000, immutable" }))).toBe(true);
  });
  it("the shell is cacheable HTML, not a JSON 404 or a login redirect", () => {
    expect(isShell(res({}, { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" }))).toBe(true);
    expect(isShell(res({}, { "content-type": "application/json" }))).toBe(false);
    expect(isShell(res({ redirected: true }, { "content-type": "text/html" }))).toBe(false);
    expect(isShell(res({}, { "content-type": "text/html", "cache-control": "private" }))).toBe(false);
  });
  it("reads the build marker, and only the exact build matches", () => {
    expect(shellBuildId(BUILD_HTML("abc123"))).toBe("abc123");
    expect(shellBuildId("<html>Bad gateway</html>")).toBeNull();
    expect(shellBuildId('<meta name="generator" content="abc123">')).toBeNull();
    expect(shellMatchesBuild(BUILD_HTML("abc123"), "abc123")).toBe(true);
    expect(shellMatchesBuild(BUILD_HTML("new999"), "abc123")).toBe(false);
    expect(shellMatchesBuild("<html>SSO login</html>", "abc123")).toBe(false);
  });
  it("sees 5xx as a server error", () => {
    expect(isServerError(res({ status: 502 }))).toBe(true);
    expect(isServerError(res({ status: 503 }))).toBe(true);
    expect(isServerError(res({ status: 404 }))).toBe(false);
    expect(isServerError(res())).toBe(false);
  });
  it("finds the assets an index.html loads", () => {
    const html = '<script type="module" crossorigin src="/assets/index-A1.js"></script><link rel="stylesheet" href="/assets/index-B2.css"><link rel="icon" href="/icon-192.png">';
    expect(assetUrls(html)).toEqual(["/assets/index-A1.js", "/assets/index-B2.css"]);
  });
});

// ---- the composed worker, run against a fake worker scope ----
const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");

function boot(buildId = "build1", { caches: cacheStores = {}, fetchImpl } = {}) {
  const listeners = {};
  const stores = cacheStores;
  const cacheApi = {
    keys: async () => Object.keys(stores),
    delete: vi.fn(async (k) => delete stores[k]),
    open: async (name) => {
      stores[name] ??= new Map();
      const m = stores[name];
      return {
        match: async (req) => m.get(typeof req === "string" ? req : new URL(req.url).pathname),
        put: async (req, r) => void m.set(typeof req === "string" ? req : new URL(req.url).pathname, r),
      };
    },
  };
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type, fn) => (listeners[type] = fn),
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn(async () => {}) },
  };
  const fetchSpy = vi.fn(fetchImpl || (async () => shellRes("build1")));
  const code = composeSw(read("./routing.js"), read("./sw.js"), buildId);
  // Our own composed worker source (never untrusted input), run in an isolated context with a fake scope.
  vm.runInNewContext(code, { self, caches: cacheApi, fetch: fetchSpy, URL, setTimeout, clearTimeout, Promise, Error, Set, Response: undefined });
  const fire = (request) => {
    const event = { request: { headers: { has: () => false }, ...request }, respondWith: vi.fn() };
    listeners.fetch(event);
    return event;
  };
  return { self, listeners, stores, cacheApi, fetchSpy, fire };
}

describe("the built worker", () => {
  it("composes into a classic script with the build id filled in", () => {
    const code = composeSw(read("./routing.js"), read("./sw.js"), "abc123");
    expect(code).toContain('const BUILD_ID = "abc123"');
    expect(code).not.toMatch(/^\s*(import|export)\s/m);
    expect(code).not.toContain("__BUILD_ID__");
    expect(() => composeSw("x", "y", "bad id!")).toThrow();
  });

  it("tells the page which build it belongs to, over the port the page sent", () => {
    const sw = boot("abc123");
    const port = { postMessage: vi.fn() };
    sw.listeners.message({ data: { type: "build-id" }, ports: [port] });
    expect(port.postMessage).toHaveBeenCalledWith({ build: "abc123" });
    const other = { postMessage: vi.fn() };
    sw.listeners.message({ data: { type: "other" }, ports: [other] });
    sw.listeners.message({ data: { type: "build-id" }, ports: [] }); // no reply port: ignored
    expect(other.postMessage).not.toHaveBeenCalled();
  });

  it("does not intercept /api, the manifest, itself or non-GET requests", () => {
    const sw = boot();
    const calls = [
      { method: "GET", url: `${ORIGIN}/api/dashboards`, mode: "cors" },
      { method: "GET", url: `${ORIGIN}/api/dashboards`, mode: "navigate" },
      { method: "GET", url: `${ORIGIN}/manifest.webmanifest`, mode: "cors" },
      { method: "GET", url: `${ORIGIN}/sw.js`, mode: "same-origin" },
      { method: "POST", url: `${ORIGIN}/api/dashboards`, mode: "cors" },
      { method: "PUT", url: `${ORIGIN}/assets/x.js`, mode: "cors" },
      { method: "GET", url: `${ORIGIN}/icon-192.png`, mode: "no-cors" },
    ];
    for (const c of calls) expect(sw.fire(c).respondWith, `${c.method} ${c.url}`).not.toHaveBeenCalled();
    expect(sw.fetchSpy).not.toHaveBeenCalled();
  });

  it("answers assets and navigations", () => {
    const sw = boot();
    expect(sw.fire({ method: "GET", url: `${ORIGIN}/assets/index-1.js`, mode: "no-cors" }).respondWith).toHaveBeenCalledOnce();
    expect(sw.fire({ method: "GET", url: `${ORIGIN}/`, mode: "navigate" }).respondWith).toHaveBeenCalledOnce();
  });

  it("serves assets cache-first and stores only cacheable ones", async () => {
    const asset = res({}, { "content-type": "text/javascript", "cache-control": "public, immutable" });
    const sw = boot("b1", { fetchImpl: async () => asset });
    const req = { method: "GET", url: `${ORIGIN}/assets/index-1.js`, mode: "no-cors" };
    const first = sw.fire(req);
    expect(await first.respondWith.mock.calls[0][0]).toBe(asset);
    const second = sw.fire(req);
    expect((await second.respondWith.mock.calls[0][0]).status).toBe(200);
    expect(sw.fetchSpy).toHaveBeenCalledTimes(1); // the second one came from the cache
    expect(sw.stores[`${CACHE_PREFIX}b1`].has("/assets/index-1.js")).toBe(true);

    const noStore = res({}, { "cache-control": "no-store" });
    const sw2 = boot("b2", { fetchImpl: async () => noStore });
    await sw2.fire({ method: "GET", url: `${ORIGIN}/assets/x.js`, mode: "no-cors" }).respondWith.mock.calls[0][0];
    expect(sw2.stores["startpage-b2"].size).toBe(0);
  });

  it("falls back to the cached shell when the network is down", async () => {
    const shell = shellRes("b1");
    const sw = boot("b1", { fetchImpl: async () => { throw new TypeError("offline"); } });
    sw.stores["startpage-b1"] = new Map([["/", shell]]);
    const ev = sw.fire({ method: "GET", url: `${ORIGIN}/`, mode: "navigate" });
    expect(await ev.respondWith.mock.calls[0][0]).toBe(shell);
  });

  const nav = (path = "/") => ({ method: "GET", url: `${ORIGIN}${path}`, mode: "navigate" });
  const settle = () => new Promise((r) => setTimeout(r, 20));

  it("does not answer a navigation to another app's path, online or offline", () => {
    const sw = boot("b1", { fetchImpl: async () => { throw new TypeError("offline"); } });
    for (const p of ["/grafana/", "/pihole/admin", "/does-not-exist"]) expect(sw.fire(nav(p)).respondWith, p).not.toHaveBeenCalled();
    expect(sw.fetchSpy).not.toHaveBeenCalled();
  });

  it("stores a navigation answer only when it carries this build's marker", async () => {
    const good = boot("b1", { fetchImpl: async () => shellRes("b1") });
    await good.fire(nav()).respondWith.mock.calls[0][0];
    await settle();
    expect(good.stores["startpage-b1"].has("/")).toBe(true);

    for (const [name, html] of [["maintenance page", "<html><h1>Maintenance</h1></html>"], ["SSO login", "<form action=/login></form>"], ["a newer build", BUILD_HTML("b2")]]) {
      const sw = boot("b1", { fetchImpl: async () => res({}, HTML_HEADERS, html) });
      const out = await sw.fire(nav()).respondWith.mock.calls[0][0];
      expect(await out.text(), name).toBe(html); // the page still gets what the network said
      await settle();
      expect(sw.stores["startpage-b1"].has("/"), name).toBe(false);
    }
  });

  it("answers a 5xx from the proxy with the cached shell, and never stores the error", async () => {
    const shell = shellRes("b1");
    const sw = boot("b1", { fetchImpl: async () => res({ status: 503 }, HTML_HEADERS, BUILD_HTML("b1")) });
    sw.stores["startpage-b1"] = new Map([["/", shell]]);
    expect(await sw.fire(nav()).respondWith.mock.calls[0][0]).toBe(shell);
    await settle();
    expect(sw.stores["startpage-b1"].get("/")).toBe(shell);
    // without a cached shell the error is passed through untouched
    const bare = boot("b1", { fetchImpl: async () => res({ status: 502 }, HTML_HEADERS, "bad gateway") });
    const out = await bare.fire(nav()).respondWith.mock.calls[0][0];
    expect(out.status).toBe(502);
    expect(bare.stores["startpage-b1"].size).toBe(0);
  });

  it("the install-time fetch stores the shell only with this build's marker", async () => {
    const run = async (html, id) => {
      const sw = boot(id, { fetchImpl: async () => res({}, HTML_HEADERS, html) });
      let done;
      sw.listeners.install({ waitUntil: (p) => (done = p) });
      await done;
      return sw.stores[`startpage-${id}`];
    };
    expect((await run(BUILD_HTML("b1"), "b1")).has("/")).toBe(true);
    expect((await run("<html>Maintenance</html>", "b1")).has("/")).toBe(false);
    expect((await run(BUILD_HTML("other"), "b1")).has("/")).toBe(false);
  });

  it("activate deletes other builds' caches and claims clients", async () => {
    const sw = boot("new", { caches: { "startpage-old": new Map(), "startpage-new": new Map(), "someone-else": new Map() } });
    let done;
    sw.listeners.activate({ waitUntil: (p) => (done = p) });
    await done;
    expect(Object.keys(sw.stores).sort()).toEqual(["someone-else", "startpage-new"]);
    expect(sw.self.clients.claim).toHaveBeenCalled();
    sw.listeners.install({ waitUntil: () => {} });
    expect(sw.self.skipWaiting).toHaveBeenCalled();
  });
});
