/* Pure helpers of the page templates UI. */

/**
 * The schematic of a template for its card: an SVG viewBox and one rectangle per widget, in grid cells.
 * `template` = {columns, rows?, layout: [{type, x, y, w, h}]} (the list endpoint's shape). Thin decoration
 * widgets are kept; rectangles are inset slightly so neighbours stay distinguishable.
 */
export function miniMap(template, inset = 0.12) {
  const layout = Array.isArray(template?.layout) ? template.layout : [];
  const columns = Math.max(1, Number(template?.columns) || 24);
  const rows = Math.max(1, Number(template?.rows) || Math.max(1, ...layout.map((r) => r.y + r.h)));
  return {
    columns,
    rows,
    viewBox: `0 0 ${columns} ${rows}`,
    rects: layout.map((r, i) => ({
      key: `${i}-${r.type}`,
      type: r.type,
      x: r.x + inset,
      y: r.y + inset,
      width: Math.max(0.1, r.w - inset * 2),
      height: Math.max(0.1, r.h - inset * 2),
      kind: kindOf(r.type),
    })),
  };
}

/** Rectangle shade: decoration (headings, dividers) is drawn lighter than content. */
export const kindOf = (type) => (["heading", "divider", "box"].includes(type) ? "deco" : "widget");

/** A file name for a downloaded page: `<name>.startpage-page.json`, safe on every OS. */
export function exportFileName(name) {
  const base = String(name || "page").replace(/[\\/:*?"<>|\x00-\x1f]+/g, "-").replace(/\s+/g, " ").trim().replace(/^\.+/, "").slice(0, 80) || "page";
  return `${base}.startpage-page.json`;
}

/** The exported document without `warnings` and `contents` (both are for the person exporting, not part of the file). */
export function withoutWarnings(doc) {
  const { warnings, contents, ...rest } = doc || {};
  return rest;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * What an exported page file holds, as counts and yes/no flags. Uses the server's `contents` when it sent one,
 * and counts from the page JSON itself when it did not (an older server).
 */
export function contentsOf(doc) {
  const c = doc?.contents;
  if (c && typeof c === "object" && Number.isInteger(c.widget_count)) {
    return {
      widgets: c.widget_count,
      services: Number.isInteger(c.service_count) ? c.service_count : 0,
      integrations: Number.isInteger(c.integration_count) ? c.integration_count : 0,
      includes: { ...(c.includes || {}) },
    };
  }
  const widgets = Array.isArray(doc?.widgets) ? doc.widgets : [];
  const cfg = (w) => (w && typeof w.config === "object" && w.config) || {};
  const some = (type, pred) => widgets.some((w) => w?.type === type && pred(cfg(w)));
  return {
    widgets: widgets.length,
    services: Array.isArray(doc?.services) ? doc.services.length : 0,
    integrations: Array.isArray(doc?.integrations) ? doc.integrations.length : 0,
    includes: {
      text_content: some("text", (c) => c.content) || some("notes", (c) => c.title) || some("heading", (c) => c.text) || some("box", (c) => c.title),
      links: some("bookmarks", (c) => Array.isArray(c.links) && c.links.length) || some("link", (c) => c.url),
      location: some("weather", (c) => c.location) || some("clock", (c) => c.timezone),
      custom_code: widgets.some((w) => w?.type === "custom"),
      service_addresses: Array.isArray(doc?.services) && doc.services.length > 0,
      phone_layout: widgets.some((w) => w && "layout_sm" in w),
    },
  };
}

const INCLUDE_LABELS = {
  text_content: "text you wrote",
  links: "links",
  location: "a location or time zone",
  service_addresses: "service addresses",
  custom_code: "custom code",
  phone_layout: "a phone layout",
};

/** One line for the export dialog: "12 widgets, 4 services, 1 integration (name only). Includes: links, custom code." */
export function contentsLine(doc) {
  const c = contentsOf(doc);
  const counts = [plural(c.widgets, "widget"), plural(c.services, "service"), `${plural(c.integrations, "integration")}${c.integrations ? " (name and type only, no keys)" : ""}`];
  const inc = Object.entries(INCLUDE_LABELS).filter(([k]) => c.includes[k] === true).map(([, label]) => label);
  return `${counts.join(", ")}.${inc.length ? ` Includes: ${inc.join(", ")}.` : ""}`;
}
