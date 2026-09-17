import { describe, it, expect } from "vitest";
import { buildItems, collectLinks, normalizeUrl, queryTarget, rankItems } from "./search.js";

describe("search", () => {
  it("queryTarget: URL, LAN host, plain query, {q}-less template", () => {
    expect(queryTarget("github.com")).toBe("https://github.com");
    expect(queryTarget("192.168.1.2:8080")).toBe("http://192.168.1.2:8080");
    expect(queryTarget("hello world")).toBe("https://duckduckgo.com/?q=hello%20world");
    expect(queryTarget("hello world", "https://x/search?q=")).toBe("https://x/search?q=hello%20world");
  });

  it("normalizeUrl keeps explicit schemes and picks http for LAN hosts", () => {
    expect(normalizeUrl("  http://a.b ")).toBe("http://a.b");
    expect(normalizeUrl("localhost:3000")).toBe("http://localhost:3000");
    expect(normalizeUrl("example.com")).toBe("https://example.com");
  });

  it("collectLinks survives hostile shapes (Iterations 017/018)", () => {
    const widgets = [
      {
        id: "b",
        type: "bookmarks",
        config: { links: [null, { url: 5 }, { id: { toString: 1 }, url: "https://a" }, { id: 7, url: "https://b", name: 3 }] },
      },
      { id: "l", type: "link", config: { url: 123 } },
      { id: "x", type: "bookmarks", config: { links: "nope" } },
    ];
    const out = collectLinks(widgets);
    // null link and numeric url skipped; object id -> index 2; numeric id kept.
    expect(out.map((l) => l.id)).toEqual(["b-2", "b-7"]);
    expect(out[1].name).toBe("");
    expect(() => rankItems(buildItems({ links: out }), "a")).not.toThrow();
  });
});
