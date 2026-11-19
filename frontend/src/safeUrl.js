/*
 * The one place where a user- or data-supplied URL is checked before it becomes an href, a src, a CSS
 * url() or is opened. Defence in depth: the backend already refuses dangerous schemes on write, but
 * stored data, old backups and remote integration data reach the page too.
 *
 * Every helper returns the (trimmed) URL when it is acceptable and null otherwise; null is inert:
 * React omits an href/src that is null, and openUrl does nothing.
 *
 * Links use a DENYLIST of dangerous schemes (javascript, vbscript, data, livescript): link widgets
 * legitimately use app schemes such as obsidian://, steam://, ssh:// or mailto:. Images and iframes use
 * an allowlist: http, https or relative (images also accept data:image/png|jpeg|gif|webp).
 */

// Control characters, C1 controls, line separators and invisible characters. Browsers drop some of these
// inside a scheme ("java\tscript:"), so a URL that carries any of them is refused outright.
// eslint-disable-next-line no-control-regex
const TRICKY = /[\u0000-\u001f\u007f-\u009f\u00ad\u200b-\u200f\u2028\u2029\u2060\ufeff]/;
const SCHEME = /^([a-z][a-z0-9+.-]*):/i;
const DANGEROUS = new Set(["javascript", "vbscript", "data", "livescript"]);
const IMAGE_DATA = /^data:image\/(png|jpe?g|gif|webp)[;,]/i; // no svg: it can carry script

const MAX = 8_000_000; // a data: image is long; anything beyond this is not an icon

/** The trimmed URL, or null for a non-string, empty or control-character-laden value. */
function prepare(url) {
  if (typeof url !== "string") return null;
  const s = url.trim();
  if (!s || s.length > MAX || TRICKY.test(s)) return null;
  return s;
}

const schemeOf = (s) => SCHEME.exec(s)?.[1].toLowerCase() ?? null;

/** For <a href> and window.open: anything except the dangerous schemes (app schemes and relative are fine). */
export function safeHref(url) {
  const s = prepare(url);
  if (!s) return null;
  const scheme = schemeOf(s);
  return scheme && DANGEROUS.has(scheme) ? null : s;
}

/** For links that come from remote data (RSS items, integration rows): absolute http(s) only. */
export function safeHttpUrl(url) {
  const s = prepare(url);
  return s && /^https?:\/\//i.test(s) ? s : null;
}

/** For <img src> and CSS background images: http(s), relative, or a data:image (png, jpeg, gif, webp). */
export function safeImageSrc(url) {
  const s = prepare(url);
  if (!s) return null;
  const scheme = schemeOf(s);
  if (!scheme || scheme === "http" || scheme === "https") return s;
  return scheme === "data" && IMAGE_DATA.test(s) ? s : null;
}

/** For <iframe src>: http(s) or relative only. */
export function safeFrameSrc(url) {
  const s = prepare(url);
  if (!s) return null;
  const scheme = schemeOf(s);
  return !scheme || scheme === "http" || scheme === "https" ? s : null;
}

/** Whether `url` (absolute, or relative to this page) points at the app's own origin. Unparsable counts as yes: the safe answer. */
export function isSameOrigin(url, base = typeof window !== "undefined" ? window.location.href : "http://localhost/") {
  try {
    return new URL(url, base).origin === new URL(base).origin;
  } catch {
    return true;
  }
}

/** A CSS `url("...")` for a background image, or "none"; the quotes and backslashes of the value cannot end the string. */
export function cssUrl(url) {
  const s = safeImageSrc(url);
  return s ? `url("${s.replace(/["\\()]/g, (ch) => "%" + ch.charCodeAt(0).toString(16).toUpperCase())}")` : "none";
}
