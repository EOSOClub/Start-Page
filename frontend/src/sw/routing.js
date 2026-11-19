/*
 * The service worker's decisions, as pure functions: no `self`, no caches, no network.
 * sw.js (the worker) is built from this file plus sw.js's own code (see compose.js), and the tests
 * import it directly, so the rules below are exactly what runs in the browser.
 *
 * Plain function/const declarations only: the build strips the `export` keyword and pastes the
 * file into the worker script.
 */

/** Every cache this app creates starts with this, so old builds' caches can be found and removed. */
export const CACHE_PREFIX = "startpage-";
/** The cached app shell (index.html) lives under this key. */
export const SHELL_URL = "/";
/** How long a navigation waits for the network before the cached shell answers instead. */
export const NAV_TIMEOUT_MS = 3000;

/** The path the way the server reads it: leading slashes collapsed, percent-escapes decoded, lower case. */
export function normalizePath(pathname) {
  let p = "/" + String(pathname).replace(/^\/+/, "");
  try {
    p = decodeURIComponent(p);
  } catch {
    // a malformed escape: keep the raw text (still compared lower-cased)
  }
  return p.toLowerCase();
}

/**
 * What the worker does with a request: "bypass" (never touched, the browser does its normal thing),
 * "assets" (cache-first, content-hashed build files) or "navigate" (network-first, cached shell as fallback;
 * only for "/" and "/index.html", the app has no other client routes).
 *
 * Bypass wins over everything: anything that is not a plain same-origin GET, anything under /api
 * (data, auth, and everything the API may ever return), the manifest, the worker script itself and
 * range requests. Nothing else is ever cached.
 */
export function decide({ method, url, mode, origin, range = false }) {
  if (String(method).toUpperCase() !== "GET" || range) return "bypass";
  let u;
  try {
    u = new URL(url);
  } catch {
    return "bypass";
  }
  if (u.origin !== origin) return "bypass";
  const path = normalizePath(u.pathname);
  if (path === "/api" || path.startsWith("/api/")) return "bypass";
  if (path === "/manifest.webmanifest" || path === "/sw.js") return "bypass";
  if (path.startsWith("/assets/")) return "assets";
  // Only the app's own entry points are the worker's business. Another app path-proxied on the same
  // host (/grafana/, /pihole/admin) must never be answered with, or cached as, the Start Page shell.
  if (mode === "navigate" && (path === "/" || path === "/index.html")) return "navigate";
  return "bypass";
}

/**
 * Whether a response may be stored: a plain, successful, same-origin answer that is not marked private.
 * (Set-Cookie is not checked: browsers never expose it on Response.headers. The install-time fetches send
 * no credentials instead, and the shell must carry the app's own build marker, see shellMatchesBuild.)
 */
export function cacheable(res) {
  if (!res || res.status !== 200 || res.type !== "basic" || res.redirected) return false;
  return !/no-store|private/i.test(res.headers.get("cache-control") || "");
}

/** Whether a response is the app shell: cacheable HTML. Anything else a navigation returns (a 404, a login redirect) is not. */
export function isShell(res) {
  return cacheable(res) && /^text\/html/i.test(res.headers.get("content-type") || "");
}

/** The build id an index.html declares in <meta name="startpage-build">, or null when it has no such marker. */
export function shellBuildId(html) {
  const m = /<meta\s+name=["']startpage-build["']\s+content=["']([A-Za-z0-9_-]+)["']/i.exec(String(html));
  return m ? m[1] : null;
}

/** Whether this HTML is the app's own shell for exactly this worker's build (not a proxy error page, an SSO login or another build). */
export function shellMatchesBuild(html, buildId) {
  return shellBuildId(html) === buildId;
}

/** A proxy or server error for the shell navigation: answered by the cached shell, never stored. */
export function isServerError(res) {
  return !!res && res.status >= 500 && res.status <= 599;
}

/** The /assets/... URLs an index.html loads, so the shell and its scripts can be stored together. */
export function assetUrls(html) {
  const out = new Set();
  for (const m of String(html).matchAll(/(?:src|href)=["'](\/assets\/[^"'?#\s]+)["']/g)) out.add(m[1]);
  return [...out];
}
