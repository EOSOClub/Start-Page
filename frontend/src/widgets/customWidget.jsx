import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TRANSPARENT_FIELD } from "./startpage.jsx";
import { EXAMPLES } from "./customExamples.js";
import { SANDBOX, THROTTLE_MS, buildPayload, buildSrcdoc, makeNonce, parseChildMessage, readTheme } from "./customSandbox.js";

/*
 * Custom widget: the author's HTML/CSS/JS in a sandboxed iframe (see customSandbox.js for the security model).
 * The frame gets data only through postMessage; it may answer with `startpage:ready` and `startpage:resize`.
 */

/** The page's colours, kept current when the theme or accent changes (the app sets them on <html>). */
function useTheme() {
  const [theme, setTheme] = useState(() => readTheme());
  useEffect(() => {
    const update = () => setTheme((cur) => {
      const next = readTheme();
      return JSON.stringify(next) === JSON.stringify(cur) ? cur : next;
    });
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["style", "data-theme"] });
    const media = globalThis.matchMedia?.("(prefers-color-scheme: light)");
    media?.addEventListener?.("change", update);
    return () => {
      observer.disconnect();
      media?.removeEventListener?.("change", update);
    };
  }, []);
  return theme;
}

const RESIZE_THROTTLE_MS = 100; // a frame calling StartPage.resize in a loop must not re-render the grid on every message

export function CustomWidgetView({ widget, services = [], health = {}, integrationData = {} }) {
  const c = widget.config || {};
  const frame = useRef(null);
  const nonce = useMemo(makeNonce, []);
  const srcdoc = useMemo(
    () => buildSrcdoc({ html: c.html, css: c.css, js: c.js, allowNetwork: !!c.allow_network, transparent: !!c.transparent, nonce }),
    [c.html, c.css, c.js, c.allow_network, c.transparent, nonce]
  );
  const theme = useTheme();
  const payload = useMemo(
    () => buildPayload({ theme, integration: c.integration_id ? integrationData[c.integration_id] : null, serviceIds: c.service_ids, services, health }),
    [theme, c.integration_id, c.service_ids, services, health, integrationData]
  );
  const [height, setHeight] = useState(null);
  const [navigated, setNavigated] = useState(0); // >0: the frame left our document and was stopped
  const state = useRef({ loads: 0, ready: false, lastSent: 0, timer: null, payload, resizeAt: 0, resizeTimer: null });
  state.current.payload = payload;

  const send = useCallback(() => {
    const s = state.current;
    const win = frame.current?.contentWindow;
    if (!win || !s.ready || s.loads > 1) return;
    s.lastSent = Date.now();
    win.postMessage({ ...s.payload, now: Date.now() }, "*"); // an opaque origin can only be addressed with "*"
  }, []);
  const schedule = useCallback(() => {
    const s = state.current;
    clearTimeout(s.timer);
    const wait = THROTTLE_MS - (Date.now() - s.lastSent);
    if (wait <= 0) send();
    else s.timer = setTimeout(send, wait);
  }, [send]);

  // Leading + trailing throttle for the heights a frame asks for: the latest one always wins.
  const applyHeight = useCallback((h) => {
    const s = state.current;
    clearTimeout(s.resizeTimer);
    const wait = RESIZE_THROTTLE_MS - (Date.now() - s.resizeAt);
    if (wait <= 0) {
      s.resizeAt = Date.now();
      setHeight(h);
    } else {
      s.resizeTimer = setTimeout(() => {
        s.resizeAt = Date.now();
        setHeight(h);
      }, wait);
    }
  }, []);

  // A new document (the code changed): start over.
  useEffect(() => {
    clearTimeout(state.current.resizeTimer);
    Object.assign(state.current, { loads: 0, ready: false });
    setHeight(null);
    setNavigated(0);
  }, [srcdoc]);

  useEffect(() => {
    const onMessage = (event) => {
      const msg = parseChildMessage(event, frame.current?.contentWindow, nonce);
      if (!msg || state.current.loads > 1) return;
      if (msg.type === "ready") {
        state.current.ready = true;
        schedule();
      } else {
        applyHeight(msg.height);
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [nonce, schedule, applyHeight]);

  useEffect(() => schedule(), [payload, schedule]); // whenever the data changes (throttled)
  useEffect(() => {
    const every = Number(c.refresh_seconds);
    if (!(every > 0)) return undefined;
    // Nothing is sent to a hidden tab; one catch-up message goes out when it becomes visible again.
    const t = setInterval(() => { if (!document.hidden) send(); }, Math.max(1, every) * 1000);
    const onVisible = () => { if (!document.hidden) send(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [c.refresh_seconds, send]);
  useEffect(() => () => { clearTimeout(state.current.timer); clearTimeout(state.current.resizeTimer); }, []);

  if (!c.html && !c.css && !c.js) return <div className="muted pad">Empty custom widget. Add HTML, CSS or JavaScript in the widget settings.</div>;
  if (navigated) {
    return (
      <div className="muted pad">
        This widget tried to load another page, so it was stopped.{" "}
        <button type="button" className="btn-sm" onClick={() => { Object.assign(state.current, { loads: 0, ready: false }); setNavigated(0); }}>Restart</button>
      </div>
    );
  }
  return (
    <div className="custom-widget">
      {c.title && <div className="group-title">{c.title}</div>}
      <iframe
        key={srcdoc}
        ref={frame}
        title={c.title || "Custom widget"}
        sandbox={SANDBOX}
        srcDoc={srcdoc}
        referrerPolicy="no-referrer"
        // A requested height: a fixed-size flex item (the widget body scrolls when it is taller than the cell).
        className={height ? "custom-sized" : undefined}
        style={height ? { height } : undefined}
        onLoad={() => {
          const s = state.current;
          s.loads += 1;
          if (s.loads > 1) setNavigated((n) => n + 1);
          else {
            s.ready = true;
            schedule();
          }
        }}
      />
    </div>
  );
}

const CODE_HELP = "Runs in a sandbox: no access to this page, its cookies or the Start Page API.";

export const CustomWidget = {
  label: "Custom (HTML/JS)",
  group: "Advanced",
  size: { w: 6, h: 4 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    { key: "html", label: "HTML", type: "textarea", mono: true, rows: 7, placeholder: '<div id="out"></div>', help: CODE_HELP, examples: EXAMPLES },
    { key: "css", label: "CSS", type: "textarea", mono: true, rows: 5, placeholder: "body { padding: 8px }" },
    { key: "js", label: "JavaScript", type: "textarea", mono: true, rows: 9, placeholder: "StartPage.onData(function (data) { /* data.integration, data.services, data.theme */ });" },
    { key: "integration_id", label: "Integration (optional)", type: "integration", help: "Its data (not its settings or keys) is passed to your code as data.integration." },
    { key: "service_ids", label: "Services (optional)", type: "services", help: "Their name, address, status and latency are passed as data.services." },
    { key: "refresh_seconds", label: "Send data again every (seconds)", type: "number", default: 0, min: 0, max: 3600, help: "0 = only when the data changes." },
    { ...TRANSPARENT_FIELD, label: "Transparent background" },
    {
      key: "allow_network",
      label: "Allow network requests (fetch, XHR, WebSocket)",
      type: "checkbox",
      default: false,
      help: "Off by default. When on, the code can send the data it receives to any web address. Images can always load from the web, and the sandbox cannot stop code from putting data into an image address, so only use code you trust.",
    },
  ],
  Render: CustomWidgetView,
};
