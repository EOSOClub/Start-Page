/*
 * Pure helpers of the sandboxed `custom` widget (see customWidget.jsx and README, "Custom widget").
 *
 * Security model in one paragraph: the widget's code runs in an <iframe sandbox="allow-scripts"> built from
 * `srcdoc`. Without `allow-same-origin` the frame has an opaque origin, so it cannot touch the app's
 * cookies, storage, DOM or API. The `sandbox` attribute is a constant; nothing from the widget's config
 * ever reaches the iframe element's attributes (the title is set as a plain attribute value by React).
 * The string assembled here only ever contains the author's own code and a fixed helper: data from
 * integrations and services reaches the frame ONLY through postMessage, never inside the srcdoc.
 */

/** The only sandbox flag: scripts. No same-origin, forms, popups, top-navigation, downloads or modals. */
export const SANDBOX = "allow-scripts";

export const MIN_HEIGHT = 20;
export const MAX_HEIGHT = 4000;
export const THROTTLE_MS = 250; // at most one data message per this long

/** The Content-Security-Policy of the frame. `allowNetwork` only widens `connect-src`. */
export function csp(allowNetwork = false) {
  return [
    "default-src 'none'",
    "script-src 'unsafe-inline'",
    "style-src 'unsafe-inline'",
    "img-src data: https: http:",
    "font-src data:",
    `connect-src ${allowNetwork ? "https: http:" : "'none'"}`,
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
}

/** A random token the frame must repeat in its messages, so a document that replaced ours cannot pose as it. */
export function makeNonce() {
  const bytes = new Uint8Array(12);
  (globalThis.crypto || { getRandomValues: (a) => a.map(() => Math.floor(Math.random() * 256)) }).getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The injected helper (runs inside the frame, before the author's code). `nonce` is hex only. */
export function helperSource(nonce) {
  return `(function () {
  var NONCE = ${JSON.stringify(String(nonce).replace(/[^a-f0-9]/gi, ""))};
  var listeners = [];
  var last = null;
  var overlay = null;
  function reveal() { document.documentElement.style.visibility = "visible"; }
  function showError(text) {
    try {
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.setAttribute("role", "alert");
        overlay.title = "Click to dismiss";
        overlay.style.cssText = "position:fixed;left:4px;right:4px;bottom:4px;max-height:60%;overflow:auto;padding:4px 8px;" +
          "font:11px/1.4 ui-monospace,Consolas,monospace;color:#fff;background:rgba(190,30,45,.94);border-radius:4px;" +
          "white-space:pre-wrap;z-index:2147483647;cursor:pointer";
        overlay.addEventListener("click", function () { overlay.style.display = "none"; });
        (document.body || document.documentElement).appendChild(overlay);
      }
      overlay.style.display = "block";
      overlay.textContent = String(text).slice(0, 2000);
      reveal();
    } catch (e) {}
  }
  function describe(e) { return e && e.message ? e.name + ": " + e.message : String(e); }
  function call(cb, data) { try { cb(data); } catch (e) { showError(describe(e)); } }
  function setVar(name, value) {
    if (typeof value === "string" && value.length < 300) document.documentElement.style.setProperty(name, value);
  }
  function applyTheme(t) {
    if (!t || typeof t !== "object") return;
    setVar("--sp-accent", t.accent); setVar("--sp-text", t.text); setVar("--sp-bg", t.background);
    setVar("--sp-muted", t.muted); setVar("--sp-border", t.border); setVar("--sp-font", t.font);
    document.documentElement.setAttribute("data-theme", t.mode === "light" ? "light" : "dark");
  }
  function post(message) { try { message.nonce = NONCE; window.parent.postMessage(message, "*"); } catch (e) {} }
  window.addEventListener("error", function (e) { showError(e.error ? describe(e.error) : e.message || "Script error"); });
  window.addEventListener("unhandledrejection", function (e) { showError("Unhandled promise rejection: " + describe(e.reason)); });
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (e.source !== window.parent || !d || d.type !== "startpage:data") return;
    applyTheme(d.theme);
    last = d;
    reveal();
    listeners.slice().forEach(function (cb) { call(cb, d); });
  });
  document.addEventListener("click", function (e) {
    var el = e.target;
    while (el && el.nodeType === 1) { if (el.tagName === "A") { e.preventDefault(); return; } el = el.parentNode; }
  }, true);
  window.StartPage = {
    onData: function (cb) {
      if (typeof cb !== "function") return;
      listeners.push(cb);
      if (last) { var d = last; setTimeout(function () { call(cb, d); }, 0); }
    },
    resize: function (height) {
      var h = typeof height === "number" ? height : document.documentElement.scrollHeight;
      post({ type: "startpage:resize", height: h });
    }
  };
  setTimeout(reveal, 400);
  post({ type: "startpage:ready" });
})();`;
}

/** Stop user text from closing the element it is placed in. (It is the author's own code inside the
 *  sandbox either way; this keeps the helper script and the structure intact.) */
export const escapeScript = (js) => String(js ?? "").replace(/<\/(script)/gi, "<\\/$1");
export const escapeStyle = (css) => String(css ?? "").replace(/<\/(style)/gi, "<\\/$1");

const baseCss = (transparent) =>
  `html{visibility:hidden}html,body{margin:0;padding:0}` +
  `body{color:var(--sp-text,#e6e8ee);font:14px/1.4 var(--sp-font,system-ui,sans-serif);` +
  `background:${transparent ? "transparent" : "var(--sp-bg,transparent)"}}` +
  (transparent ? "html{background:transparent}" : "");

/**
 * The frame's document. Order matters: the CSP meta comes first, then the fixed helper, then the author's
 * CSS, HTML and JavaScript. No config value or data is interpolated anywhere except the author's own code.
 */
export function buildSrcdoc({ html = "", css = "", js = "", allowNetwork = false, transparent = false, nonce = "" } = {}) {
  return (
    `<!doctype html><html><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="${csp(!!allowNetwork)}">` +
    `<meta name="referrer" content="no-referrer"><meta name="color-scheme" content="dark light">` +
    `<style>${baseCss(!!transparent)}</style>` +
    `<script>${helperSource(nonce)}</script>` +
    `<style>${escapeStyle(css)}</style></head><body>${html ?? ""}<script>${escapeScript(js)}</script></body></html>`
  );
}

/** Clamp a requested frame height to [MIN_HEIGHT, MAX_HEIGHT]; null when it is not a finite number. */
export function clampHeight(h) {
  if (typeof h !== "number" || !Number.isFinite(h)) return null;
  return Math.round(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, h)));
}

/**
 * A message from the frame, or null. Only messages whose `source` is this frame's window, from a document
 * that knows the nonce, with one of the two known shapes are accepted: {type:"startpage:ready"} and
 * {type:"startpage:resize", height: <finite number>}. Everything else is ignored.
 */
export function parseChildMessage(event, frameWindow, nonce) {
  if (!frameWindow || !event || event.source !== frameWindow) return null;
  const d = event.data;
  if (!d || typeof d !== "object" || Array.isArray(d) || d.nonce !== nonce) return null;
  if (d.type === "startpage:ready") return { type: "ready" };
  if (d.type === "startpage:resize") {
    const height = clampHeight(d.height);
    return height === null ? null : { type: "resize", height };
  }
  return null;
}

const cssVar = (style, name, fallback) => (style.getPropertyValue(name) || "").trim() || fallback;

/** The page's colours as the frame sees them. */
export function readTheme(root = document.documentElement) {
  const style = getComputedStyle(root);
  const pref = root.dataset?.theme;
  const light = pref === "light" || (pref !== "dark" && !!globalThis.matchMedia?.("(prefers-color-scheme: light)").matches);
  return {
    mode: light ? "light" : "dark",
    accent: cssVar(style, "--accent", "#6c8cff"),
    text: cssVar(style, "--text", light ? "#1b1f27" : "#e6e8ee"),
    background: cssVar(style, "--bg-widget", light ? "#ffffff" : "#1c2029"),
    muted: cssVar(style, "--muted", light ? "#6b7280" : "#8b92a5"),
    border: cssVar(style, "--border", light ? "#dcdfe6" : "#2a2f3a"),
    font: cssVar(style, "--font", "system-ui, sans-serif"),
  };
}

/**
 * What the frame receives (without `now`, which is added when sending). Only: the theme, the chosen
 * integration's data payload, and public fields of the chosen services. No integration config, no auth
 * state, nothing from other widgets.
 */
export function buildPayload({ theme, integration, serviceIds = [], services = [], health = {} }) {
  const wanted = Array.isArray(serviceIds) ? serviceIds : [];
  return {
    type: "startpage:data",
    theme,
    integration: integration?.data ?? null,
    services: wanted
      .map((id) => services.find((s) => s.id === id))
      .filter(Boolean)
      .map((s) => ({ name: s.name, url: s.url, status: health[s.id]?.status ?? (s.health_enabled ? "unknown" : "disabled"), latency_ms: health[s.id]?.latency_ms ?? null })),
  };
}
