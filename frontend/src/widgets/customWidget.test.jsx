import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, render } from "@testing-library/react";
import { CustomWidget, CustomWidgetView } from "./customWidget.jsx";

const widget = (config) => ({ id: "w1", type: "custom", config });
const frameOf = (root) => root.querySelector("iframe");

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("custom widget element", () => {
  it("the sandbox attribute is fixed, whatever the config says", () => {
    const evil = {
      html: "<b>x</b>",
      title: '" sandbox="allow-same-origin allow-top-navigation allow-forms" src="https://evil.example" onload="alert(1)" x="',
      sandbox: "allow-same-origin",
      allow_network: true,
    };
    const { container } = render(<CustomWidgetView widget={widget(evil)} />);
    const f = frameOf(container);
    expect(f.getAttribute("sandbox")).toBe("allow-scripts");
    expect(f.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(f.hasAttribute("src")).toBe(false);
    expect(f.hasAttribute("onload")).toBe(false);
    expect(f.getAttribute("title")).toBe(evil.title); // plain attribute text, not markup
    expect([...f.attributes].map((a) => a.name).sort()).toEqual(["referrerpolicy", "sandbox", "srcdoc", "title"]);
  });

  it("does not put integration or service data into the srcdoc", () => {
    const { container } = render(
      <CustomWidgetView
        widget={widget({ html: "x", integration_id: "i1", service_ids: ["s1"] })}
        services={[{ id: "s1", name: "TopSecretService", url: "http://nas.internal:9999" }]}
        integrationData={{ i1: { ok: true, data: { stats: [{ label: "TopSecretStat", value: 42 }] } } }}
      />
    );
    const srcdoc = frameOf(container).getAttribute("srcdoc");
    expect(srcdoc).not.toContain("TopSecretService");
    expect(srcdoc).not.toContain("TopSecretStat");
    expect(srcdoc).not.toContain("nas.internal");
  });

  it("shows a hint instead of a frame when there is no code", () => {
    const { container, getByText } = render(<CustomWidgetView widget={widget({})} />);
    expect(frameOf(container)).toBeNull();
    expect(getByText(/Empty custom widget/)).toBeTruthy();
  });

  it("declares the fields of the spec", () => {
    expect(CustomWidget.fields.map((f) => f.key)).toEqual(["title", "html", "css", "js", "integration_id", "service_ids", "refresh_seconds", "transparent", "allow_network"]);
    expect(CustomWidget.fields.find((f) => f.key === "allow_network").default).toBe(false);
    expect(CustomWidget.fields.find((f) => f.key === "html").examples).toHaveLength(3);
    expect(CustomWidget.fields.filter((f) => f.mono).map((f) => f.key)).toEqual(["html", "css", "js"]);
  });
});

describe("messages", () => {
  const cfg = { html: "<b>x</b>", integration_id: "i1" };
  const mount = () => {
    const out = render(<CustomWidgetView widget={widget(cfg)} integrationData={{ i1: { data: { v: 1 } } }} />);
    const f = frameOf(out.container);
    const posted = vi.fn();
    const win = { postMessage: posted };
    Object.defineProperty(f, "contentWindow", { value: win, configurable: true });
    const nonce = /var NONCE = "([a-f0-9]+)"/.exec(f.getAttribute("srcdoc"))[1];
    // A plain Event carries any `source` (jsdom's MessageEvent only takes a real Window).
    const from = (source, data) => act(() => { const e = new Event("message"); e.data = data; e.source = source; window.dispatchEvent(e); });
    const fromFrame = (data) => from(win, data);
    return { ...out, f, win, posted, nonce, from, fromFrame };
  };

  it("answers ready with the data", () => {
    const { posted, nonce, fromFrame } = mount();
    fromFrame({ type: "startpage:ready", nonce });
    expect(posted).toHaveBeenCalledTimes(1);
    const [msg, target] = posted.mock.calls[0];
    expect(target).toBe("*");
    expect(msg).toMatchObject({ type: "startpage:data", integration: { v: 1 }, services: [] });
    expect(typeof msg.now).toBe("number");
    expect(msg.theme).toHaveProperty("mode");
  });

  it("ignores messages from other windows and with bad shapes", () => {
    const { posted, nonce, from, fromFrame } = mount();
    from({}, { type: "startpage:ready", nonce });
    from(window, { type: "startpage:ready", nonce });
    fromFrame({ type: "startpage:ready", nonce: "wrong" });
    fromFrame({ type: "startpage:ready" });
    fromFrame("startpage:ready");
    fromFrame({ type: "startpage:navigate", nonce });
    expect(posted).not.toHaveBeenCalled();
  });

  it("clamps resize requests onto the frame's height", () => {
    const { container, nonce, fromFrame } = mount();
    fromFrame({ type: "startpage:resize", nonce, height: 1e9 });
    expect(frameOf(container).style.height).toBe("4000px");
    fromFrame({ type: "startpage:resize", nonce, height: 1 });
    act(() => { vi.advanceTimersByTime(150); });
    expect(frameOf(container).style.height).toBe("20px");
    fromFrame({ type: "startpage:resize", nonce, height: "huge" });
    act(() => { vi.advanceTimersByTime(150); });
    expect(frameOf(container).style.height).toBe("20px");
  });

  it("throttles resize messages: the first applies at once, a burst ends on the latest height", () => {
    const { container, nonce, fromFrame } = mount();
    fromFrame({ type: "startpage:resize", nonce, height: 100 });
    expect(frameOf(container).style.height).toBe("100px");
    for (const h of [200, 300, 400]) fromFrame({ type: "startpage:resize", nonce, height: h });
    expect(frameOf(container).style.height).toBe("100px"); // held back
    act(() => { vi.advanceTimersByTime(150); });
    expect(frameOf(container).style.height).toBe("400px");
  });

  it("marks a resized frame so the stylesheet stops stretching it (flex: 0 0 auto beats the inline height otherwise)", () => {
    const { container, nonce, fromFrame } = mount();
    expect(frameOf(container).classList.contains("custom-sized")).toBe(false);
    fromFrame({ type: "startpage:resize", nonce, height: 300 });
    expect(frameOf(container).classList.contains("custom-sized")).toBe(true);
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    const rule = /\.custom-widget iframe\.custom-sized\s*\{([^}]*)\}/.exec(css);
    expect(rule?.[1]).toMatch(/flex:\s*0 0 auto/);
  });

  it("sends nothing on the refresh interval while the tab is hidden, and catches up when it is shown", () => {
    const out = render(<CustomWidgetView widget={widget({ html: "<b>x</b>", refresh_seconds: 5 })} />);
    const f = frameOf(out.container);
    const posted = vi.fn();
    Object.defineProperty(f, "contentWindow", { value: { postMessage: posted }, configurable: true });
    const nonce = /var NONCE = "([a-f0-9]+)"/.exec(f.getAttribute("srcdoc"))[1];
    const e = new Event("message"); e.data = { type: "startpage:ready", nonce }; e.source = f.contentWindow;
    act(() => { window.dispatchEvent(e); });
    const n = posted.mock.calls.length;
    let hidden = true;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    act(() => { vi.advanceTimersByTime(20000); });
    expect(posted.mock.calls.length).toBe(n);
    hidden = false;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(posted.mock.calls.length).toBe(n + 1);
    delete document.hidden;
  });

  it("throttles repeated data changes", () => {
    const { posted, nonce, fromFrame, rerender } = mount();
    fromFrame({ type: "startpage:ready", nonce });
    for (let n = 2; n < 6; n++) rerender(<CustomWidgetView widget={widget(cfg)} integrationData={{ i1: { data: { v: n } } }} />);
    expect(posted.mock.calls.length).toBeLessThanOrEqual(2);
    act(() => { vi.advanceTimersByTime(300); });
    expect(posted.mock.calls.at(-1)[0].integration).toEqual({ v: 5 });
  });
});
