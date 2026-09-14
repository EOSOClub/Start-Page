/* Shared search logic for the command palette and the Search widget. */

export const SEARCH_ENGINES = {
  duckduckgo: { label: "DuckDuckGo", url: "https://duckduckgo.com/?q={q}" },
  google: { label: "Google", url: "https://www.google.com/search?q={q}" },
  bing: { label: "Bing", url: "https://www.bing.com/search?q={q}" },
  brave: { label: "Brave Search", url: "https://search.brave.com/search?q={q}" },
  startpage: { label: "Startpage", url: "https://www.startpage.com/sp/search?query={q}" },
  kagi: { label: "Kagi", url: "https://kagi.com/search?q={q}" },
};

const LOCAL_HOST = /^(localhost|\d{1,3}(\.\d{1,3}){3})(:\d+)?(\/|$)/i;

export const looksLikeUrl = (s) =>
  !/\s/.test(s) && (/^https?:\/\//i.test(s) || LOCAL_HOST.test(s) || /^[\w-]+(\.[\w-]+)+(:\d+)?(\/.*)?$/.test(s));

/** "github.com" -> "https://github.com", "192.168.1.2:8080" -> "http://…" (LAN hosts rarely have TLS). */
export function normalizeUrl(s) {
  s = s.trim();
  if (/^https?:\/\//i.test(s)) return s;
  return (LOCAL_HOST.test(s) || /:\d+(\/|$)/.test(s) ? "http://" : "https://") + s;
}

export const hostOf = (url) => {
  try {
    return new URL(normalizeUrl(url)).host.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/** "GitHub · Change is constant…" -> "GitHub" */
export const tidyTitle = (title) => (title || "").split(/\s+[-|·–—:]\s+/)[0].trim();

/** Where Enter should go for a free-text query: the URL itself, or a web search. */
export function queryTarget(q, template) {
  const s = q.trim();
  if (looksLikeUrl(s)) return normalizeUrl(s);
  const t = template || SEARCH_ENGINES.duckduckgo.url;
  // A template that forgot the {q} placeholder (e.g. "https://x/search?q=") gets the query
  // appended after the separator it already wrote, instead of silently opening an empty search.
  if (!t.includes("{q}") && t.endsWith("=")) return t + encodeURIComponent(s);
  return t.replace("{q}", encodeURIComponent(s));
}

export function openUrl(url, newTab) {
  if (newTab) window.open(url, "_blank", "noopener");
  else window.location.href = url;
}

/** Every link on the page, whether it is a Link widget or part of a Bookmarks widget. */
export function collectLinks(widgets = []) {
  const out = [];
  for (const w of widgets) {
    if (w.type === "link" && w.config?.url) {
      out.push({ id: w.id, name: w.config.name, url: w.config.url, icon: w.config.icon, description: w.config.description });
    } else if (w.type === "bookmarks") {
      for (const l of w.config?.links || []) if (l.url) out.push({ id: `${w.id}-${l.id}`, name: l.name, url: l.url, icon: l.icon, description: w.config.title });
    }
  }
  return out;
}

export function buildItems({ services = [], links = [], dashboards = [], health = {} }) {
  return [
    ...services.map((s) => ({
      kind: "service",
      id: `s-${s.id}`,
      title: s.name,
      sub: [s.category, s.url.replace(/^https?:\/\//, "")].filter(Boolean).join(" · "),
      icon: s.icon,
      url: s.url,
      status: health[s.id]?.status,
      text: `${s.name} ${s.category} ${s.host} ${s.description}`.toLowerCase(),
    })),
    ...links.map((l) => ({
      kind: "link",
      id: `w-${l.id}`,
      title: l.name || hostOf(l.url),
      sub: l.url,
      icon: l.icon,
      url: normalizeUrl(l.url),
      text: `${l.name} ${l.url} ${l.description || ""}`.toLowerCase(),
    })),
    ...dashboards.map((d) => ({
      kind: "page",
      id: `d-${d.id}`,
      title: d.name,
      sub: "Go to page",
      icon: "📄",
      page: d.id,
      text: d.name.toLowerCase(),
    })),
  ];
}

/** Exact > prefix > substring > other fields > fuzzy subsequence. Empty query returns the first `limit`. */
export function rankItems(list, q, limit = 12) {
  const s = q.trim().toLowerCase();
  if (!s) return list.slice(0, limit);
  const score = (it) => {
    const t = it.title.toLowerCase();
    if (t === s) return 0;
    if (t.startsWith(s)) return 1;
    if (t.includes(s)) return 2;
    if (it.text.includes(s)) return 3;
    let i = 0;
    for (const ch of t) if (ch === s[i]) i++;
    return i === s.length ? 4 : 99;
  };
  return list
    .map((it) => ({ it, sc: score(it) }))
    .filter((x) => x.sc < 99)
    .sort((a, b) => a.sc - b.sc || a.it.title.localeCompare(b.it.title))
    .slice(0, limit)
    .map((x) => x.it);
}
