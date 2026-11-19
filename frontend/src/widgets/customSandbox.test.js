import { describe, it, expect } from "vitest";
import { MAX_HEIGHT, MIN_HEIGHT, SANDBOX, buildPayload, buildSrcdoc, clampHeight, csp, escapeScript, helperSource, parseChildMessage } from "./customSandbox.js";

const CSP_DEFAULT = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: https: http:; font-src data:; connect-src 'none'";

describe("srcdoc builder", () => {
  it("puts a strict CSP meta first in the head", () => {
    const doc = buildSrcdoc({ html: "<b>x</b>", nonce: "ab12" });
    const meta = doc.match(/<meta http-equiv="Content-Security-Policy" content="([^"]*)">/);
    expect(meta[1].startsWith(CSP_DEFAULT)).toBe(true);
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(doc.indexOf("<script>"));
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(doc.indexOf("<b>x</b>"));
    expect(doc).toContain('<meta name="referrer" content="no-referrer">');
  });

  it("allow_network only widens connect-src", () => {
    expect(csp(false)).toContain("connect-src 'none'");
    expect(csp(true)).toContain("connect-src https: http:");
    expect(csp(true).replace("connect-src https: http:", "connect-src 'none'")).toBe(csp(false));
    expect(buildSrcdoc({ allowNetwork: true })).toContain("connect-src https: http:");
    expect(buildSrcdoc({ allowNetwork: false })).toContain("connect-src 'none'");
    expect(buildSrcdoc({})).toContain("connect-src 'none'");
  });

  it("transparent gives a transparent body", () => {
    expect(buildSrcdoc({ transparent: true })).toContain("background:transparent");
    expect(buildSrcdoc({ transparent: false })).toContain("background:var(--sp-bg,transparent)");
  });

  it("inlines only the author's code and the fixed helper", () => {
    const doc = buildSrcdoc({ html: "<p>h</p>", css: "p{color:red}", js: "var a = 1;", nonce: "ab12" });
    expect(doc).toContain("<p>h</p>");
    expect(doc).toContain("p{color:red}");
    expect(doc).toContain("var a = 1;");
    expect(doc).not.toMatch(/stats|integration_id|health_url/); // no data of any kind
    expect(helperSource("ab12")).toContain('"ab12"');
  });

  it("a </script> in the user's JS cannot end the script early", () => {
    const js = 'var s = "</script><img src=x onerror=alert(1)>"; /* </SCRIPT > */';
    const doc = buildSrcdoc({ js });
    expect(escapeScript(js)).not.toMatch(/<\/script/i);
    expect(doc.match(/<\/script>/gi)).toHaveLength(2); // the helper's and the author's own end tag
    expect(doc.endsWith("</script></body></html>")).toBe(true);
  });

  it("user CSS cannot close its style element", () => {
    const doc = buildSrcdoc({ css: "a{} </style><script>alert(1)</script>" });
    expect(doc.match(/<\/style>/gi)).toHaveLength(2); // base + author
  });

  it("the helper never touches the page's address, storage or network", () => {
    const src = helperSource("ab12");
    for (const word of ["localStorage", "cookie", "location", "document.referrer", "fetch(", "XMLHttpRequest"]) expect(src).not.toContain(word);
  });

  it("the sandbox is exactly allow-scripts", () => {
    expect(SANDBOX).toBe("allow-scripts");
  });
});

describe("messages from the frame", () => {
  const win = {};
  const ev = (data, source = win) => ({ data, source });
  const ok = { nonce: "n1" };

  it("ignores a message from any other source", () => {
    expect(parseChildMessage(ev({ ...ok, type: "startpage:ready" }, {}), win, "n1")).toBeNull();
    expect(parseChildMessage(ev({ ...ok, type: "startpage:ready" }, null), win, "n1")).toBeNull();
    expect(parseChildMessage(ev({ ...ok, type: "startpage:ready" }), null, "n1")).toBeNull();
  });

  it("accepts only ready and resize with a valid shape and the nonce", () => {
    expect(parseChildMessage(ev({ ...ok, type: "startpage:ready" }), win, "n1")).toEqual({ type: "ready" });
    expect(parseChildMessage(ev({ ...ok, type: "startpage:resize", height: 120 }), win, "n1")).toEqual({ type: "resize", height: 120 });
    for (const bad of [
      null, undefined, "startpage:ready", 5, [], {},
      { type: "startpage:ready" },
      { type: "startpage:ready", nonce: "other" },
      { ...ok, type: "startpage:navigate", url: "https://x" },
      { ...ok, type: "startpage:api", path: "/api/settings" },
      { ...ok, type: "startpage:resize" },
      { ...ok, type: "startpage:resize", height: "100" },
      { ...ok, type: "startpage:resize", height: NaN },
      { ...ok, type: "startpage:resize", height: Infinity },
      { ...ok, type: "startpage:resize", height: {} },
    ]) expect(parseChildMessage(ev(bad), win, "n1"), JSON.stringify(bad)).toBeNull();
  });

  it("clamps the requested height", () => {
    expect(clampHeight(5)).toBe(MIN_HEIGHT);
    expect(clampHeight(-100)).toBe(MIN_HEIGHT);
    expect(clampHeight(1e9)).toBe(MAX_HEIGHT);
    expect(clampHeight(150.4)).toBe(150);
    expect(clampHeight("9")).toBeNull();
    expect(parseChildMessage(ev({ ...ok, type: "startpage:resize", height: 1e9 }), win, "n1").height).toBe(MAX_HEIGHT);
  });
});

describe("data sent to the frame", () => {
  it("carries the integration's data payload and public service fields only", () => {
    const msg = buildPayload({
      theme: { mode: "dark" },
      integration: { integration_id: "i1", ok: true, error: null, data: { stats: [{ key: "a", value: 1 }] }, config: { api_key: "SECRET" } },
      serviceIds: ["s1", "gone"],
      services: [{ id: "s1", name: "Jelly", url: "http://j", host: "h", health_url: "http://secret", metadata: { k: 1 }, health_enabled: true }],
      health: { s1: { status: "online", latency_ms: 12, error: "x", http_status: 200 } },
    });
    expect(msg).toEqual({
      type: "startpage:data",
      theme: { mode: "dark" },
      integration: { stats: [{ key: "a", value: 1 }] },
      services: [{ name: "Jelly", url: "http://j", status: "online", latency_ms: 12 }],
    });
    expect(JSON.stringify(msg)).not.toContain("SECRET");
  });

  it("is null / empty when nothing is selected", () => {
    expect(buildPayload({ theme: {} })).toMatchObject({ integration: null, services: [] });
  });
});
