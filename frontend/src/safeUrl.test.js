import { describe, expect, it } from "vitest";
import { cssUrl, safeFrameSrc, safeHref, safeHttpUrl, safeImageSrc } from "./safeUrl.js";

const PNG = "data:image/png;base64,iVBORw0KGgo=";

// Things a browser would run or navigate to, however they are dressed up.
const EVIL = [
  "javascript:alert(1)",
  "JAVASCRIPT:alert(1)",
  "  javascript:alert(1)",
  "\tjavascript:alert(1)",
  "\njavascript:alert(1)",
  "java\tscript:alert(1)",
  "java\nscript:alert(1)",
  "java\r\nscript:alert(1)",
  "\u0001javascript:alert(1)",
  "\u0000javascript:alert(1)",
  "jav&#x09;ascript:alert(1)".replace("&#x09;", "\t"),
  " javascript:alert(1)",
  "​javascript:alert(1)",
  "vbscript:msgbox(1)",
  "VbScript:x",
  "data:text/html,<script>alert(1)</script>",
  "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
  "data:image/svg+xml,<svg onload=alert(1)>",
];

describe("safeHref (links: denylist)", () => {
  it.each(EVIL)("refuses %j", (url) => expect(safeHref(url)).toBeNull());

  it.each([
    ["https://a.example/x?y=1#z", "https://a.example/x?y=1#z"],
    ["http://192.168.1.2:8080", "http://192.168.1.2:8080"],
    ["/relative/path", "/relative/path"],
    ["relative.html", "relative.html"],
    ["//cdn.example/x", "//cdn.example/x"],
    ["#anchor", "#anchor"],
    ["obsidian://open?vault=Notes", "obsidian://open?vault=Notes"],
    ["steam://run/440", "steam://run/440"],
    ["ssh://me@host", "ssh://me@host"],
    ["mailto:a@b.example", "mailto:a@b.example"],
    ["tel:+123", "tel:+123"],
    ["  https://trimmed.example  ", "https://trimmed.example"],
    ["https://a.example/javascript:alert(1)", "https://a.example/javascript:alert(1)"], // only a leading scheme counts
  ])("keeps %j", (url, out) => expect(safeHref(url)).toBe(out));

  it("returns null for empty and non-string values", () => {
    for (const v of ["", "   ", null, undefined, 5, {}, []]) expect(safeHref(v)).toBeNull();
  });
});

describe("safeHttpUrl (remote data: absolute http(s) only)", () => {
  it("accepts http(s) only", () => {
    expect(safeHttpUrl("https://a.example/x")).toBe("https://a.example/x");
    expect(safeHttpUrl("HTTP://a.example")).toBe("HTTP://a.example");
    for (const v of [...EVIL, "/relative", "obsidian://x", "mailto:a@b.c", "ftp://x", ""]) expect(safeHttpUrl(v)).toBeNull();
  });
});

describe("safeImageSrc", () => {
  it("allows http(s), relative and png/jpeg/gif/webp data URLs", () => {
    for (const v of ["https://a.example/i.png", "http://a.example/i.png", "/local/i.png", "i.png", "//cdn.example/i.png", PNG, "data:image/jpeg;base64,/9j/4AAQ", "data:image/webp;base64,UklG", "data:image/gif;base64,R0lG"]) {
      expect(safeImageSrc(v)).toBe(v);
    }
  });
  it("refuses scripts, other data types (svg included) and other schemes", () => {
    for (const v of [...EVIL, "ftp://x/i.png", "obsidian://x", "data:image/svg+xml;base64,PHN2Zz4=", "data:application/pdf;base64,AAAA"]) {
      expect(safeImageSrc(v), v).toBeNull();
    }
  });
});

describe("safeFrameSrc", () => {
  it("allows http(s) and relative only", () => {
    for (const v of ["https://a.example", "http://10.0.0.2:3000/d", "/status", "page.html"]) expect(safeFrameSrc(v)).toBe(v);
    for (const v of [...EVIL, PNG, "obsidian://x", "ftp://x", "blob:https://a.example/uuid"]) expect(safeFrameSrc(v), v).toBeNull();
  });
});

describe("cssUrl", () => {
  it("wraps a safe image and cannot be broken out of", () => {
    expect(cssUrl("https://a.example/bg.jpg")).toBe('url("https://a.example/bg.jpg")');
    expect(cssUrl('https://a.example/x");background:url(javascript:alert(1)')).toBe('url("https://a.example/x%22%29;background:url%28javascript:alert%281%29")');
    expect(cssUrl("javascript:alert(1)")).toBe("none");
    expect(cssUrl("")).toBe("none");
    expect(cssUrl("data:text/html,x")).toBe("none");
  });
  it("encodes quotes, backslashes and parentheses", () => {
    const out = cssUrl('/a"b\\c(d)');
    expect(out).toBe('url("/a%22b%5Cc%28d%29")');
  });
});
