import { useEffect, useMemo, useRef, useState } from "react";
import Icon from "../widgets/Icon.jsx";
import { buildItems, looksLikeUrl, openUrl, queryTarget, rankItems } from "../search.js";

/**
 * Search services, links (Link and Bookmarks widgets) and pages. Enter opens the selected item;
 * if nothing matches, Enter runs a web search (or opens the query if it looks like a URL).
 */
export default function CommandPalette({ services, dashboards, links, health, settings, onGoToPage, onClose }) {
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const inputRef = useRef();

  const all = useMemo(() => buildItems({ services, links, dashboards, health }), [services, links, dashboards, health]);
  const items = useMemo(() => rankItems(all, q), [all, q]);

  useEffect(() => setIdx(0), [q]);

  const open = (item) => {
    if (item.kind === "page") onGoToPage(item.page);
    else openUrl(item.url, settings.open_new_tab);
    onClose();
  };

  const fallback = () => {
    if (!q.trim()) return;
    window.location.href = queryTarget(q, settings.search_url);
    onClose();
  };

  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); items[idx] ? open(items[idx]) : fallback(); }
    else if (e.key === "Escape") onClose();
  };

  return (
    <div className="modal-backdrop palette-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette">
        <input
          ref={inputRef}
          autoFocus
          placeholder="Search services, links, pages… or type a URL / web search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
        />
        <div className="palette-list">
          {items.map((it, i) => (
            <div
              key={it.id}
              className={`palette-item ${i === idx ? "active" : ""}`}
              onMouseEnter={() => setIdx(i)}
              onClick={() => open(it)}
            >
              <Icon icon={it.icon} url={it.url} fallback={it.title} size={24} />
              <div className="palette-text">
                <div className="row">
                  {it.status && <span className={`status-dot status-${it.status}`} />}
                  <span>{it.title}</span>
                </div>
                <div className="muted">{it.sub}</div>
              </div>
              <span className="muted kind">{it.kind}</span>
            </div>
          ))}
          {items.length === 0 && q && (
            <div className="palette-item active" onClick={fallback}>
              <Icon icon={looksLikeUrl(q.trim()) ? "🔗" : "🔍"} size={24} />
              <div className="palette-text">
                <div>{looksLikeUrl(q.trim()) ? `Open ${q.trim()}` : `Search the web for “${q.trim()}”`}</div>
              </div>
            </div>
          )}
        </div>
        <div className="palette-foot muted">↑↓ navigate · Enter open · Esc close</div>
      </div>
    </div>
  );
}
