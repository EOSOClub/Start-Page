import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { WIDGET_TYPES, defaultConfig } from "./index.jsx";
import Icon from "./Icon.jsx";
import { ItemRows } from "./integrationViews.jsx";
import { openUrl } from "../search.js";
import { isSameOrigin } from "../safeUrl.js";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const widget = (type, config) => ({ id: "w", type, config: { ...defaultConfig(type), ...config } });
const show = (type, config, props = {}) => render(<>{WIDGET_TYPES[type].Render({ widget: widget(type, config), services: [], health: {}, history: {}, ...props })}</>);

describe("link safety in widgets", () => {
  it("a Link widget with a javascript: URL renders no anchor; app schemes and https stay links", () => {
    const { container, rerender } = show("link", { name: "Evil", url: "javascript:alert(1)" });
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("Evil")).toBeTruthy();
    cleanup();
    for (const url of ["obsidian://open?vault=x", "https://a.example", "ssh://host"]) {
      const r = show("link", { name: "Ok", url });
      expect(r.container.querySelector("a").getAttribute("href")).toBe(url);
      cleanup();
    }
  });

  it("an Image widget refuses javascript:/svg data sources and its link, and accepts a png data URL", () => {
    let r = show("image", { url: "javascript:alert(1)" });
    expect(r.container.querySelector("img")).toBeNull();
    cleanup();
    r = show("image", { url: "data:image/svg+xml,<svg onload=alert(1)>" });
    expect(r.container.querySelector("img")).toBeNull();
    cleanup();
    r = show("image", { url: "https://a.example/i.png", link: "javascript:alert(1)" });
    expect(r.container.querySelector("img").getAttribute("src")).toBe("https://a.example/i.png");
    expect(r.container.querySelector("a")).toBeNull();
    cleanup();
    r = show("image", { url: "data:image/png;base64,iVBORw0KGgo=" });
    expect(r.container.querySelector("img")).not.toBeNull();
  });

  it("an Iframe widget only frames http(s) or relative URLs", () => {
    for (const url of ["javascript:alert(1)", "data:text/html,<b>x</b>", "java\tscript:alert(1)"]) {
      const r = show("iframe", { url });
      expect(r.container.querySelector("iframe"), url).toBeNull();
      cleanup();
    }
    const ok = show("iframe", { url: "https://grafana.lan/d/x" });
    expect(ok.container.querySelector("iframe").getAttribute("src")).toBe("https://grafana.lan/d/x");
  });

  it("an Iframe widget keeps allow-same-origin for other sites but drops it for the app's own origin", () => {
    const sandboxOf = (url) => {
      const r = show("iframe", { url });
      const v = r.container.querySelector("iframe").getAttribute("sandbox");
      cleanup();
      return v;
    };
    expect(sandboxOf("https://grafana.lan/d/x")).toContain("allow-same-origin");
    expect(sandboxOf("//other.example/x")).toContain("allow-same-origin"); // protocol-relative: another host
    for (const own of ["/grafana/", "relative/page.html", "./x", `${window.location.origin}/index.html`, "?a=1", "#top"]) {
      const v = sandboxOf(own);
      expect(v, own).not.toContain("allow-same-origin");
      expect(v, own).toContain("allow-scripts"); // the frame still runs, as an opaque origin
    }
  });

  it("isSameOrigin: relative and own-origin URLs are same-origin, other hosts and ports are not", () => {
    expect(isSameOrigin("/x", "https://start.lan/")).toBe(true);
    expect(isSameOrigin("https://start.lan/x", "https://start.lan/")).toBe(true);
    expect(isSameOrigin("https://start.lan:8443/x", "https://start.lan/")).toBe(false);
    expect(isSameOrigin("http://start.lan/x", "https://start.lan/")).toBe(false);
    expect(isSameOrigin("https://evil.example/", "https://start.lan/")).toBe(false);
    expect(isSameOrigin("https://start.lan.evil.example/", "https://start.lan/")).toBe(false);
  });

  it("integration list rows link only to http(s)", () => {
    const items = [
      { title: "evil", url: "javascript:alert(1)" },
      { title: "data", url: "data:text/html,x" },
      { title: "fine", url: "https://a.example/x" },
    ];
    const { container } = render(<ItemRows items={items} />);
    expect([...container.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual(["https://a.example/x"]);
  });

  it("an icon given as a URL never loads a script or non-image data URL", () => {
    for (const icon of ["javascript:alert(1)", "data:text/html,x", "data:image/svg+xml,<svg/>"]) {
      const { container } = render(<Icon icon={icon} fallback="Evil" />);
      const src = container.querySelector("img")?.getAttribute("src") ?? "";
      // "javascript:alert(1)" is not a URL to the icon resolver: it becomes a dashboard-icons file name on https
      expect(src === "" || src.startsWith("https://cdn.jsdelivr.net/"), icon).toBe(true);
      cleanup();
    }
    const ok = render(<Icon icon="https://a.example/i.png" fallback="x" />);
    expect(ok.container.querySelector("img").getAttribute("src")).toBe("https://a.example/i.png");
  });

  it("openUrl does nothing for dangerous URLs and opens the rest", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    openUrl("javascript:alert(1)", true);
    openUrl("  javascript:alert(1)", true);
    openUrl("data:text/html,x", true);
    expect(open).not.toHaveBeenCalled();
    openUrl("https://a.example", true);
    expect(open).toHaveBeenCalledWith("https://a.example", "_blank", "noopener");
  });
});
