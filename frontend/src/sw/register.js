/*
 * Registers the service worker, or removes it.
 *  - Only in production builds, only where service workers can run (secure context: HTTPS or localhost).
 *  - Kill switch: when the server answers /sw.js with 404 (STARTPAGE_DISABLE_SW=1) every registered
 *    worker is unregistered and the app's caches are deleted, and no new worker is registered.
 *  - After a new worker takes over a page that already had one, UPDATE_EVENT is dispatched on `window`
 *    so the app can offer a reload. It never reloads by itself. When the page was itself built from the
 *    build the new worker belongs to (the visit that fetched the new build), there is nothing to reload
 *    for and no event is sent: the page's build id (a <meta> injected at build time) is compared with the
 *    worker's (asked over a MessageChannel). Whenever either is unknown the event is sent, as before.
 * Every dependency is a parameter so the tests can run it against fakes.
 */
export const UPDATE_EVENT = "sp:sw-updated";
const CACHE_PREFIX = "startpage-"; // keep in step with routing.js
export const BUILD_META = "startpage-build"; // <meta name> that vite-plugin-sw.js adds to index.html

/** Should the "Updated - reload" prompt appear? Not when both builds are known and the same. Pure. */
export function shouldOfferReload(pageBuild, workerBuild) {
  return !(pageBuild && workerBuild && pageBuild === workerBuild);
}

/** The build id of the page, from its <meta>; "" in dev or when the tag is missing. */
export function readPageBuild(doc = typeof document === "undefined" ? undefined : document) {
  return doc?.querySelector?.(`meta[name="${BUILD_META}"]`)?.content || "";
}

/** The build id a worker reports, or "" when it does not answer (an older worker, a dead one) within `ms`. */
export function askWorkerBuild(worker, { channel = () => new MessageChannel(), ms = 1500 } = {}) {
  if (!worker || typeof worker.postMessage !== "function") return Promise.resolve("");
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(""), ms);
    try {
      const ch = channel();
      ch.port1.onmessage = (e) => {
        clearTimeout(timer);
        resolve(typeof e.data?.build === "string" ? e.data.build : "");
      };
      worker.postMessage({ type: "build-id" }, [ch.port2]);
    } catch {
      clearTimeout(timer);
      resolve("");
    }
  });
}

export async function registerServiceWorker({
  prod = import.meta.env.PROD,
  nav = typeof navigator === "undefined" ? undefined : navigator,
  win = typeof window === "undefined" ? undefined : window,
  fetchFn = typeof fetch === "undefined" ? undefined : fetch,
  cacheStorage = typeof caches === "undefined" ? undefined : caches,
  pageBuild = readPageBuild(),
  askBuild = askWorkerBuild,
} = {}) {
  if (!prod || !win || !nav || !("serviceWorker" in nav) || !win.isSecureContext) return "skipped";
  const sw = nav.serviceWorker;

  // Kill switch: a 404 for the worker script (not a network error, which just means "offline").
  try {
    const res = await fetchFn("/sw.js", { cache: "no-store" });
    if (res.status === 404) {
      const regs = await sw.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
      if (cacheStorage) {
        for (const key of await cacheStorage.keys()) if (key.startsWith(CACHE_PREFIX)) await cacheStorage.delete(key);
      }
      return "unregistered";
    }
  } catch {
    // offline: leave an existing worker alone; registering below is a no-op or fails quietly
  }

  const hadController = !!sw.controller;
  let refreshing = false;
  sw.addEventListener("controllerchange", () => {
    if (!hadController || refreshing) return; // the first install claiming the page is not an update
    refreshing = true;
    const announce = () => win.dispatchEvent(new win.CustomEvent(UPDATE_EVENT));
    if (!pageBuild) return announce(); // cannot compare (dev build, no tag): offer the reload
    askBuild(sw.controller).then((workerBuild) => {
      if (shouldOfferReload(pageBuild, workerBuild)) announce();
    });
  });
  try {
    await sw.register("/sw.js", { scope: "/" });
    return "registered";
  } catch {
    return "failed";
  }
}
