import { useEffect, useMemo, useRef, useState } from "react";
import { groupWidgetTypes } from "./widgetMenu.js";

/**
 * The "+ Widget" button and its menu: widget types in sections with a filter box.
 * Open it by click, Enter/Space on the button, or from outside through `open`/`onOpenChange` (Shift+W).
 * Keys: type to filter, Enter adds the first match, Arrow Up/Down move between entries, Escape closes.
 */
export default function WidgetMenu({ types, groups, open, onOpenChange, onPick }) {
  const [query, setQuery] = useState("");
  const root = useRef(null);
  const filter = useRef(null);
  const sections = useMemo(() => groupWidgetTypes(types, groups, query), [types, groups, query]);

  useEffect(() => {
    if (!open) return undefined;
    setQuery("");
    filter.current?.focus();
    const away = (e) => { if (!root.current?.contains(e.target)) onOpenChange(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]); // eslint-disable-line

  const pick = (type) => {
    onOpenChange(false);
    onPick(type);
  };
  const move = (e, step) => {
    const stops = [...root.current.querySelectorAll(".widget-menu-filter, .widget-menu-item")];
    const i = stops.indexOf(document.activeElement);
    e.preventDefault();
    stops[Math.min(stops.length - 1, Math.max(0, i + step))]?.focus();
  };
  const onKeyDown = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); onOpenChange(false); root.current?.querySelector(".widget-menu-button")?.focus(); }
    else if (e.key === "ArrowDown") move(e, 1);
    else if (e.key === "ArrowUp") move(e, -1);
  };

  return (
    <div className="widget-menu-wrap" ref={root} onKeyDown={onKeyDown}>
      <button className="btn-primary widget-menu-button" aria-haspopup="true" aria-expanded={open} onClick={() => onOpenChange(!open)}>
        + Widget ▾
      </button>
      {open && (
        <div className="widget-menu" role="region" aria-label="Add a widget">
          <input
            ref={filter}
            className="widget-menu-filter"
            type="search"
            placeholder="Filter widgets…"
            aria-label="Filter widget types"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && sections[0]) { e.preventDefault(); pick(sections[0].items[0].type); } }}
          />
          <div className="widget-menu-list">
            {sections.length === 0 && <div className="muted widget-menu-empty">No widget matches “{query}”.</div>}
            {sections.map((s) => (
              <div key={s.group} role="group" aria-label={s.group}>
                <div className="widget-menu-head">{s.group}</div>
                {s.items.map((it) => (
                  <button key={it.type} className="widget-menu-item" onClick={() => pick(it.type)}>{it.label}</button>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
