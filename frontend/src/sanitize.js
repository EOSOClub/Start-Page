/*
 * The one HTML sanitiser. Every dangerouslySetInnerHTML in the app goes through sanitizeHtml, and remote
 * feed text goes through plainText (tags stripped, nothing rendered as HTML at all).
 *
 * The input is untrusted: the markdown widget's text can come from an imported page file or a template,
 * and RSS summaries come from remote feeds. Scripts were already removed by DOMPurify's defaults; what the
 * defaults keep, and this module removes, is everything that can restyle, cover or imitate the app:
 * <style>, style/class/id/name attributes (app class names such as "modal-backdrop", id="root"), forms and
 * form controls (a fake sign-in), frames/objects, svg/math, srcset, formaction.
 *
 * It is an allowlist (markdown output needs very little) with the forbidden tags and attributes also listed
 * explicitly, so adding a tag to the allowlist later cannot silently re-enable one of them.
 * Fenced code blocks: marked emits class="language-x"; nothing in the stylesheet uses it, so classes are dropped.
 */
import DOMPurify from "dompurify";
import { safeHref, safeImageSrc } from "./safeUrl.js";

export const ALLOWED_TAGS = [
  "a", "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "strong", "b", "em", "i", "del", "s", "u",
  "code", "pre", "blockquote", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "img", "span", "div", "sup", "sub",
  "details", "summary", "kbd", "mark", "small",
];
export const ALLOWED_ATTR = ["href", "src", "alt", "title", "colspan", "rowspan", "align", "start"];
export const FORBID_TAGS = [
  "style", "form", "input", "button", "textarea", "select", "option", "iframe", "object", "embed", "link", "meta", "base", "svg", "math",
];
export const FORBID_ATTR = ["style", "id", "class", "name", "srcset", "formaction", "action", "target"];

const purify = DOMPurify(window);

purify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    const href = node.hasAttribute("href") ? safeHref(node.getAttribute("href")) : null;
    if (href) node.setAttribute("href", href);
    else node.removeAttribute("href");
    node.setAttribute("rel", "noopener noreferrer");
  } else if (node.tagName === "IMG") {
    const src = safeImageSrc(node.getAttribute("src"));
    if (!src) {
      node.remove();
      return;
    }
    node.setAttribute("src", src);
    node.setAttribute("referrerpolicy", "no-referrer");
    node.setAttribute("loading", "lazy");
  }
});

/** Sanitised HTML for dangerouslySetInnerHTML. */
export function sanitizeHtml(html) {
  return purify.sanitize(String(html ?? ""), {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    FORBID_TAGS,
    FORBID_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    ALLOW_UNKNOWN_PROTOCOLS: false,
  });
}

/** Text only: tags dropped (style/script content too), entities decoded, whitespace collapsed. Render it as a React text node. */
export function plainText(html, max = 600) {
  const spaced = String(html ?? "").replace(/<\/?(?:p|div|br|li|ul|ol|h[1-6]|tr|td|th|blockquote|pre)\b[^>]*>/gi, " ");
  const stripped = purify.sanitize(spaced, { ALLOWED_TAGS: [], ALLOWED_ATTR: [], KEEP_CONTENT: true });
  const text = new DOMParser().parseFromString(stripped, "text/html").body.textContent || "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? flat.slice(0, max - 1) + "…" : flat;
}
