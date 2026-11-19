/*
 * Start Page service worker (hand-written, no libraries).
 *
 * Source file: the build (vite-plugin-sw.js) turns it into /sw.js by dropping the import below, pasting
 * routing.js in front and replacing the build id. Rules (see routing.js for the decisions):
 *  - /assets/*  cache-first (content-hashed, immutable), stored the first time they are fetched.
 *  - navigations to "/" and "/index.html" only: network-first, the cached app shell when the network is down,
 *    slower than NAV_TIMEOUT_MS or answers 5xx (a reverse proxy while the container is down). Any other path
 *    belongs to something else (another app behind the same host) and is not touched.
 *  - a shell is stored only when its body carries this build's <meta name="startpage-build"> marker.
 *  - everything else, and everything under /api, the manifest, this script and all non-GET requests:
 *    not intercepted at all. No API response is ever read, stored or replayed here.
 *  - only plain 200 same-origin responses without no-store / private are stored, and the install-time
 *    fetches send no credentials, so the shell cache can never hold anything user-specific.
 * The cache name carries the build id: a new build installs beside the old one, takes over at once
 * (skipWaiting + clients.claim) and deletes every other startpage-* cache when it activates.
 */
import { CACHE_PREFIX, NAV_TIMEOUT_MS, SHELL_URL, assetUrls, cacheable, decide, isServerError, isShell, shellMatchesBuild } from "./routing.js";

const BUILD_ID = "__BUILD_ID__";
const CACHE = CACHE_PREFIX + BUILD_ID;

// Stores a shell response only when it is the app's own page for this build. The body is read from a clone.
async function storeShell(cache, res) {
  if (!isShell(res)) return false;
  const html = await res.clone().text();
  if (!shellMatchesBuild(html, BUILD_ID)) return false;
  await cache.put(SHELL_URL, res);
  return true;
}

// Best effort: store the shell and the scripts/styles it loads, so the very first offline start works too.
async function precacheShell() {
  const cache = await caches.open(CACHE);
  const res = await fetch(SHELL_URL, { cache: "reload", credentials: "omit" });
  if (!isShell(res)) return;
  const html = await res.clone().text();
  if (!(await storeShell(cache, res))) return;
  for (const url of assetUrls(html)) {
    try {
      const asset = await fetch(url, { credentials: "omit" });
      if (cacheable(asset)) await cache.put(url, asset);
    } catch {
      // fetched and cached on demand later
    }
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request, { ignoreVary: true });
  if (hit) return hit;
  const res = await fetch(request);
  if (cacheable(res)) await cache.put(request, res.clone());
  return res;
}

async function networkFirstShell(request) {
  const cache = await caches.open(CACHE);
  const network = fetch(request).then(async (res) => {
    if (isServerError(res)) {
      const shell = await cache.match(SHELL_URL, { ignoreVary: true });
      return shell || res; // the error is never stored
    }
    storeShell(cache, res.clone()).catch(() => {});
    return res;
  });
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("slow")), NAV_TIMEOUT_MS);
  });
  try {
    return await Promise.race([network, timeout]);
  } catch (e) {
    const shell = await cache.match(SHELL_URL, { ignoreVary: true });
    if (shell) {
      network.catch(() => {}); // a late answer still refreshes the cached shell above
      return shell;
    }
    return network; // nothing cached: keep waiting for the network (or fail like a normal navigation)
  } finally {
    clearTimeout(timer);
  }
}

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(precacheShell().catch(() => {}));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) if (key.startsWith(CACHE_PREFIX) && key !== CACHE) await caches.delete(key);
      await self.clients.claim();
    })()
  );
});

// The page asks which build this worker belongs to (to tell "the page is already new" from "reload needed").
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "build-id" && event.ports && event.ports[0]) {
    event.ports[0].postMessage({ build: BUILD_ID });
  }
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const kind = decide({
    method: request.method,
    url: request.url,
    mode: request.mode,
    origin: self.location.origin,
    range: request.headers.has("range"),
  });
  if (kind === "assets") event.respondWith(cacheFirst(request));
  else if (kind === "navigate") event.respondWith(networkFirstShell(request));
  // "bypass": no respondWith, the browser handles the request as if there were no worker
});
