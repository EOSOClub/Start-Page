import { describe, expect, it } from "vitest";
import { marked } from "marked";
import { plainText, sanitizeHtml } from "./sanitize.js";

const dom = (html) => {
  const root = document.createElement("div");
  root.innerHTML = sanitizeHtml(html);
  return root;
};

describe("sanitizeHtml: hostile payloads", () => {
  it("drops <style> together with its text", () => {
    const root = dom("<p>a</p><style>*{display:none}</style>");
    expect(root.querySelector("style")).toBeNull();
    expect(root.textContent).toBe("a");
  });
  it("drops style attributes (fixed full-screen overlays)", () => {
    const root = dom('<div style="position:fixed;inset:0;z-index:99999"><a href="https://evil.example/">Sign in again</a></div>');
    expect(root.querySelector("[style]")).toBeNull();
  });
  it("drops forms and every form control", () => {
    const root = dom('<form action="https://evil/x"><input type="password"><textarea></textarea><select><option>a</option></select><button formaction="https://evil/y">Go</button></form>');
    expect(root.querySelector("form,input,textarea,select,option,button")).toBeNull();
    expect(root.querySelector("[action],[formaction]")).toBeNull();
  });
  it("drops id, class and name so the app's own ids and classes cannot be imitated", () => {
    const root = dom('<div id="root" class="modal-backdrop" name="x"><span class="language-js" id="a">t</span></div>');
    expect(root.querySelector("[id],[class],[name]")).toBeNull();
    expect(root.textContent).toBe("t");
  });
  it("drops frames, objects, embeds, link/meta/base, svg and math", () => {
    const root = dom(
      '<iframe src="https://evil/"></iframe><object data="x"></object><embed src="x"><link rel="stylesheet" href="https://evil/c.css"><meta http-equiv="refresh" content="0;url=https://evil/"><base href="https://evil/"><svg><a href="https://evil/"><text>x</text></a></svg><math><mi>x</mi></math>'
    );
    expect(root.querySelector("iframe,object,embed,link,meta,base,svg,math")).toBeNull();
  });
  it("drops scripts and event handlers", () => {
    const root = dom('<img src="https://ok.example/a.png" onerror="alert(1)"><script>alert(1)</script><a href="https://ok.example/" onclick="x()">l</a>');
    expect(root.querySelector("script")).toBeNull();
    expect(root.querySelector("[onerror],[onclick]")).toBeNull();
  });
  it("drops srcset", () => {
    expect(dom('<img src="https://ok.example/a.png" srcset="https://evil/x.png 2x">').querySelector("[srcset]")).toBeNull();
  });
  it("neutralises javascript:, data: and vbscript: links", () => {
    const root = dom('<a href="javascript:alert(1)">a</a><a href=" jav&#x09;ascript:alert(1)">b</a><a href="data:text/html,x">c</a><a href="vbscript:x">d</a>');
    for (const a of root.querySelectorAll("a")) expect(a.hasAttribute("href")).toBe(false);
  });
  it("forces rel=noopener noreferrer on every link and never sets a target", () => {
    const root = dom('<a href="https://ok.example/" target="_blank" rel="opener">x</a><a href="obsidian://vault">y</a>');
    for (const a of root.querySelectorAll("a")) {
      expect(a.getAttribute("rel")).toBe("noopener noreferrer");
      expect(a.hasAttribute("target")).toBe(false);
    }
    // DOMPurify's own protocol allowlist (http, https, mailto, tel, ...) also applies: app schemes are not links in rendered text
    expect(root.querySelectorAll("a")[1].hasAttribute("href")).toBe(false);
  });
  it("routes images through safeImageSrc", () => {
    const root = dom('<img src="https://ok.example/a.png" alt="a"><img src="javascript:alert(1)"><img src="data:image/svg+xml,<svg>"><img src="data:image/png;base64,AAAA">');
    const srcs = [...root.querySelectorAll("img")].map((i) => i.getAttribute("src"));
    expect(srcs).toEqual(["https://ok.example/a.png", "data:image/png;base64,AAAA"]);
    expect(root.querySelector("img").getAttribute("referrerpolicy")).toBe("no-referrer");
  });
});

describe("sanitizeHtml: normal markdown still renders", () => {
  it("keeps headings, lists, emphasis, tables, code blocks and images", () => {
    const md = "# T\n\n- a\n- b\n\n**bold** *it* [l](https://ok.example/)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```js\nconst x = 1;\n```\n\n![pic](https://ok.example/p.png)\n";
    const root = dom(marked.parse(md));
    expect(root.querySelector("h1")?.textContent).toBe("T");
    expect(root.querySelectorAll("li")).toHaveLength(2);
    expect(root.querySelector("strong")).not.toBeNull();
    expect(root.querySelector("table td")?.textContent).toBe("1");
    expect(root.querySelector("pre code")?.textContent).toContain("const x = 1;");
    expect(root.querySelector("pre code")?.hasAttribute("class")).toBe(false);
    expect(root.querySelector("img")?.getAttribute("src")).toBe("https://ok.example/p.png");
    expect(root.querySelector("a")?.getAttribute("href")).toBe("https://ok.example/");
  });
  it("handles null/undefined", () => {
    expect(sanitizeHtml(null)).toBe("");
    expect(sanitizeHtml(undefined)).toBe("");
  });
});

describe("plainText: RSS summaries", () => {
  it("strips tags, drops style/script content and decodes entities", () => {
    expect(plainText("<p>Hello &amp; <b>world</b></p><style>*{display:none}</style><script>alert(1)</script>")).toBe("Hello & world");
  });
  it("never yields markup that could be injected: tags typed as entities stay text", () => {
    expect(plainText("&lt;img src=x onerror=alert(1)&gt;")).toBe("<img src=x onerror=alert(1)>");
  });
  it("separates blocks with spaces, collapses whitespace and shortens", () => {
    expect(plainText("<p>one</p><p>two</p>line<br>three")).toBe("one two line three");
    expect(plainText("x".repeat(1000), 50)).toHaveLength(50);
    expect(plainText(null)).toBe("");
  });
});
