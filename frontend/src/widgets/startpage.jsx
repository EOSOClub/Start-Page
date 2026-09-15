/*
 * Everyday start-page widgets: Search, Greeting, Bookmarks, Weather and Notes.
 * They read app-wide data (settings, links, services…) from AppContext.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon.jsx";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { SEARCH_ENGINES, buildItems, hostOf, looksLikeUrl, normalizeUrl, openUrl, queryTarget, rankItems } from "../search.js";

/** Shared field: render the widget without the card background/border. */
export const TRANSPARENT_FIELD = { key: "transparent", label: "No card background", type: "checkbox" };

const newId = () => Math.random().toString(36).slice(2, 10);

const readLS = (key) => {
  try {
    return JSON.parse(localStorage.getItem(key));
  } catch {
    return null;
  }
};
const writeLS = (key, value) => {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {}
};

/** Current time; re-renders at the top of every minute (every second with `seconds`), and on tab refocus. */
export function useNow(seconds = false) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let t;
    const tick = () => {
      const d = new Date();
      setNow(d);
      const ms = seconds ? 1000 - d.getMilliseconds() : 60_000 - (d.getSeconds() * 1000 + d.getMilliseconds());
      clearTimeout(t);
      t = setTimeout(tick, ms + 5);
    };
    tick();
    // Background tabs throttle timers, so catch up as soon as the tab is visible again.
    const onVis = () => !document.hidden && tick();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearTimeout(t);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [seconds]);
  return now;
}

// ====================================================================== Search
function SearchIcon() {
  return (
    <svg className="search-glyph" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M20 20l-3.5-3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SearchRender({ widget }) {
  const c = widget.config;
  const app = useApp();
  const settings = app.settings || {};
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(-1); // -1 = the "search the web / open URL" row
  const [focused, setFocused] = useState(false);
  const [rect, setRect] = useState(null);
  const inputRef = useRef();
  const boxRef = useRef();

  const template = c.engine === "custom" ? c.customUrl : SEARCH_ENGINES[c.engine]?.url || settings.search_url;
  const engineName = template ? Object.values(SEARCH_ENGINES).find((e) => e.url === template)?.label || hostOf(template.replace("{q}", "")) : "";
  const newTab = c.newTab ?? settings.open_new_tab;
  const suggest = c.suggestions !== false;

  const all = useMemo(
    () => (suggest ? buildItems({ services: app.services, links: app.links, dashboards: app.dashboards, health: app.health }) : []),
    [suggest, app.services, app.links, app.dashboards, app.health]
  );
  const matches = useMemo(() => (suggest && q.trim() ? rankItems(all, q, c.maxSuggestions || 6) : []), [all, q, suggest, c.maxSuggestions]);
  useEffect(() => setIdx(-1), [q]);

  useEffect(() => {
    if (c.autofocus && !app.editMode) {
      const active = document.activeElement;
      if (!active || active === document.body) inputRef.current?.focus({ preventScroll: true });
    }
  }, [c.autofocus, app.editMode]);

  const open = focused && suggest && q.trim() !== "";
  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const r = boxRef.current?.getBoundingClientRect();
      if (r) setRect({ left: r.left, top: r.bottom + 6, width: r.width });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  const go = (item, forceNewTab) => {
    const tab = newTab || forceNewTab;
    if (item?.kind === "page") app.goToPage?.(item.page);
    else if (item) openUrl(item.url, tab);
    else if (q.trim()) openUrl(queryTarget(q, template), tab);
    else return;
    setQ("");
  };

  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, matches.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, -1)); }
    else if (e.key === "Enter") { e.preventDefault(); go(matches[idx], e.ctrlKey || e.metaKey); }
    else if (e.key === "Escape") { if (q) setQ(""); else e.currentTarget.blur(); }
  };

  const isUrl = looksLikeUrl(q.trim());
  const dropdown = open && rect && (
    <div className="search-suggest" style={rect} onMouseDown={(e) => e.preventDefault()}>
      <div className={`palette-item ${idx === -1 ? "active" : ""}`} onMouseEnter={() => setIdx(-1)} onClick={() => go(null)}>
        <span className="icon search-suggest-glyph">{isUrl ? "🔗" : <SearchIcon />}</span>
        <div className="palette-text">{isUrl ? `Open ${normalizeUrl(q)}` : <>Search {engineName} for “<b>{q.trim()}</b>”</>}</div>
      </div>
      {matches.map((it, i) => (
        <div key={it.id} className={`palette-item ${i === idx ? "active" : ""}`} onMouseEnter={() => setIdx(i)} onClick={(e) => go(it, e.ctrlKey || e.metaKey)}>
          <Icon icon={it.icon} url={it.url} fallback={it.title} size={20} />
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
    </div>
  );

  return (
    <div className="search-widget">
      <div ref={boxRef} className={`search-box ${focused ? "focused" : ""}`} onClick={() => inputRef.current?.focus()}>
        <SearchIcon />
        <input
          ref={inputRef}
          type="search"
          value={q}
          placeholder={c.placeholder || `Search ${engineName || "the web"} or type a URL`}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          autoComplete="off"
          spellCheck={false}
        />
        {!focused && <kbd className="search-kbd">/</kbd>}
      </div>
      {dropdown && createPortal(dropdown, document.body)}
    </div>
  );
}

export const SearchWidget = {
  label: "Search bar",
  size: { w: 12, h: 2 },
  fields: [
    {
      key: "engine",
      label: "Search engine",
      type: "select",
      options: [
        { value: "", label: "Default (from Settings)" },
        ...Object.entries(SEARCH_ENGINES).map(([value, e]) => ({ value, label: e.label })),
        { value: "custom", label: "Custom URL…" },
      ],
    },
    { key: "customUrl", label: "Custom search URL", type: "text", placeholder: "https://example.com/search?q={q}", showIf: (c) => c.engine === "custom" },
    { key: "placeholder", label: "Placeholder", type: "text", placeholder: "Search … or type a URL" },
    { key: "suggestions", label: "Suggest matching links, services and pages", type: "checkbox", default: true },
    { key: "autofocus", label: "Focus when the page opens", type: "checkbox", help: "Works when this page is your homepage. On a new tab most browsers keep focus in the address bar — press / to jump here." },
    { key: "newTab", label: "Open results in a new tab", type: "checkbox" },
    TRANSPARENT_FIELD,
  ],
  Render: SearchRender,
};

// ====================================================================== Greeting
const greetingFor = (h) => (h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : h < 22 ? "Good evening" : "Good night");

export const GreetingWidget = {
  label: "Greeting",
  size: { w: 12, h: 4 },
  fields: [
    { key: "name", label: "Your name", type: "text", placeholder: "(optional)" },
    { key: "showTime", label: "Show time", type: "checkbox", default: true },
    { key: "h24", label: "24-hour clock", type: "checkbox", showIf: (c) => c.showTime !== false },
    { key: "showDate", label: "Show date", type: "checkbox", default: true },
    {
      key: "align",
      label: "Alignment",
      type: "select",
      options: [
        { value: "center", label: "Centred" },
        { value: "left", label: "Left" },
      ],
    },
    { ...TRANSPARENT_FIELD, default: true },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const now = useNow();
    return (
      <div className={`greeting greeting-${c.align || "center"}`}>
        {c.showTime !== false && (
          <div className="greeting-time">{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: !c.h24 })}</div>
        )}
        <div className="greeting-text">
          {greetingFor(now.getHours())}
          {c.name ? `, ${c.name}` : ""}
        </div>
        {c.showDate !== false && (
          <div className="greeting-date">{now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</div>
        )}
      </div>
    );
  },
};

// ====================================================================== Bookmarks
export const BookmarksWidget = {
  label: "Bookmarks",
  size: { w: 8, h: 5 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    { key: "links", label: "Links", type: "links" },
    {
      key: "display",
      label: "Display",
      type: "select",
      options: [
        { value: "list", label: "List (icon beside name)" },
        { value: "tiles", label: "Tiles (icon above name)" },
        { value: "icons", label: "Icons only" },
      ],
    },
    { key: "columns", label: "Columns", type: "number", placeholder: "auto", min: 1, max: 12 },
    { key: "newTab", label: "Open in new tab", type: "checkbox" },
    TRANSPARENT_FIELD,
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const { settings } = useApp();
    const links = (c.links || []).filter((l) => l.url);
    const display = c.display || "list";
    const newTab = c.newTab ?? settings?.open_new_tab;
    const style = c.columns ? { gridTemplateColumns: `repeat(${c.columns}, minmax(0, 1fr))` } : undefined;
    return (
      <div className={`group bm bm-${display}`}>
        {c.title && <div className="group-title">{c.title}</div>}
        {links.length === 0 && <div className="muted">No links yet. Add some in the widget settings.</div>}
        <div className="group-grid bm-grid" style={style}>
          {links.map((l) => {
            const name = l.name || hostOf(l.url);
            return (
              <a
                key={l.id}
                className="group-item bm-item"
                href={normalizeUrl(l.url)}
                target={newTab ? "_blank" : "_self"}
                rel="noreferrer"
                draggable={false}
                title={display === "icons" ? name : l.url}
              >
                <Icon icon={l.icon} url={l.url} fallback={name} className="bm-icon" />
                {display !== "icons" && <span className="bm-name">{name}</span>}
              </a>
            );
          })}
        </div>
      </div>
    );
  },
};

// ====================================================================== Weather
// WMO weather interpretation codes -> [label, day emoji, night emoji]
const WMO = {
  0: ["Clear", "☀️", "🌙"],
  1: ["Mainly clear", "🌤️", "🌙"],
  2: ["Partly cloudy", "⛅", "☁️"],
  3: ["Overcast", "☁️", "☁️"],
  45: ["Fog", "🌫️", "🌫️"],
  48: ["Freezing fog", "🌫️", "🌫️"],
  51: ["Light drizzle", "🌦️", "🌧️"],
  53: ["Drizzle", "🌦️", "🌧️"],
  55: ["Heavy drizzle", "🌧️", "🌧️"],
  56: ["Freezing drizzle", "🌧️", "🌧️"],
  57: ["Freezing drizzle", "🌧️", "🌧️"],
  61: ["Light rain", "🌦️", "🌧️"],
  63: ["Rain", "🌧️", "🌧️"],
  65: ["Heavy rain", "🌧️", "🌧️"],
  66: ["Freezing rain", "🌧️", "🌧️"],
  67: ["Freezing rain", "🌧️", "🌧️"],
  71: ["Light snow", "🌨️", "🌨️"],
  73: ["Snow", "🌨️", "🌨️"],
  75: ["Heavy snow", "❄️", "❄️"],
  77: ["Snow grains", "🌨️", "🌨️"],
  80: ["Showers", "🌦️", "🌧️"],
  81: ["Showers", "🌧️", "🌧️"],
  82: ["Heavy showers", "🌧️", "🌧️"],
  85: ["Snow showers", "🌨️", "🌨️"],
  86: ["Snow showers", "🌨️", "🌨️"],
  95: ["Thunderstorm", "⛈️", "⛈️"],
  96: ["Thunderstorm, hail", "⛈️", "⛈️"],
  99: ["Thunderstorm, hail", "⛈️", "⛈️"],
};
const wmo = (code, day = true) => {
  const [label, d, n] = WMO[code] || ["—", "🌡️", "🌡️"];
  return { label, emoji: day ? d : n };
};
const deg = (v) => (v == null ? "–" : `${Math.round(v)}°`);
const WEATHER_REFRESH_MS = 15 * 60_000;

function WeatherRender({ widget }) {
  const c = widget.config;
  const location = (c.location || "").trim();
  const units = c.units || "metric";
  const days = c.days ?? 3;
  const cacheKey = `sp.weather.${location.toLowerCase()}|${units}|${days}`;
  const [data, setData] = useState(() => readLS(cacheKey));
  const [error, setError] = useState("");

  useEffect(() => {
    if (!location) return;
    let alive = true;
    const cached = readLS(cacheKey);
    setData(cached);
    setError("");
    const stale = () => {
      const cur = readLS(cacheKey);
      return !cur || !cur.fetched_at || Date.now() - cur.fetched_at * 1000 > WEATHER_REFRESH_MS;
    };
    const load = () => {
      if (document.hidden) return;
      api
        .weather(location, units, days)
        .then((d) => {
          if (!alive) return;
          writeLS(cacheKey, d);
          setData(d);
          setError("");
        })
        .catch((e) => alive && setError(e.message));
    };
    if (stale()) load();
    const t = setInterval(() => stale() && load(), 60_000);
    const onVis = () => !document.hidden && stale() && load();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [cacheKey, location, units, days]);

  if (!location) return <div className="muted pad">Set a location in the widget settings.</div>;
  if (!data?.current || !Array.isArray(data?.daily)) return <div className={`muted pad ${error ? "error" : ""}`}>{error || "Loading weather…"}</div>;

  const cur = data.current;
  const now = wmo(cur.code, cur.is_day);
  const wind = units === "imperial" ? "mph" : "km/h";
  return (
    <div className="weather" title={error ? `Showing cached data: ${error}` : undefined}>
      <div className="weather-now">
        <span className="weather-emoji">{now.emoji}</span>
        <div className="weather-main">
          <div className="weather-temp">{deg(cur.temp)}</div>
          <div className="weather-desc">{now.label}</div>
        </div>
        <div className="weather-meta">
          <div className="weather-loc">{c.label || data.location}</div>
          {c.details !== false && (
            <div className="muted">
              Feels {deg(cur.feels_like)} · {cur.humidity ?? "–"}% · {cur.wind == null ? "–" : Math.round(cur.wind)} {wind}
            </div>
          )}
        </div>
      </div>
      {data.daily.length > 0 && (
        <div className="weather-days">
          {data.daily.map((d, i) => {
            const w = wmo(d.code);
            return (
              <div key={d.date} className="weather-day" title={`${w.label}${d.precip != null ? ` · ${d.precip}% rain` : ""}`}>
                <span className="muted">{i === 0 ? "Today" : new Date(`${d.date}T12:00`).toLocaleDateString([], { weekday: "short" })}</span>
                <span className="weather-day-emoji">{w.emoji}</span>
                <span>
                  {deg(d.max)} <span className="muted">{deg(d.min)}</span>
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export const WeatherWidget = {
  label: "Weather",
  size: { w: 8, h: 4 },
  fields: [
    { key: "location", label: "Location", type: "text", placeholder: "Lisbon, PT", help: "A city name (add a country code to disambiguate), or coordinates like 38.72, -9.14. Data from Open-Meteo." },
    { key: "label", label: "Display name", type: "text", placeholder: "(use the found place name)" },
    {
      key: "units",
      label: "Units",
      type: "select",
      options: [
        { value: "metric", label: "°C, km/h" },
        { value: "imperial", label: "°F, mph" },
      ],
    },
    {
      key: "days",
      label: "Forecast",
      type: "select",
      options: [
        { value: 3, label: "3 days" },
        { value: 5, label: "5 days" },
        { value: 7, label: "7 days" },
        { value: 0, label: "None" },
      ],
    },
    { key: "details", label: "Show feels-like, humidity and wind", type: "checkbox", default: true },
    TRANSPARENT_FIELD,
  ],
  Render: WeatherRender,
};

// ====================================================================== Notes
function NotesRender({ widget }) {
  const c = widget.config;
  const mode = c.mode || "note";
  const { updateWidgetConfig } = useApp();
  const [text, setText] = useState(c.content || "");
  const [items, setItems] = useState(c.items || []);
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState(true);
  const pending = useRef(null);
  const timer = useRef();
  const configRef = useRef(c);
  configRef.current = c;
  const saveSeq = useRef(0);

  const flush = useCallback(
    (keepalive = false) => {
      clearTimeout(timer.current);
      if (!pending.current || !updateWidgetConfig) return;
      const patch = pending.current;
      pending.current = null;
      const seq = ++saveSeq.current;
      Promise.resolve(updateWidgetConfig(widget.id, { ...configRef.current, ...patch }, { keepalive }))
        .then(() => seq === saveSeq.current && !pending.current && setSaved(true))
        .catch(() => seq === saveSeq.current && setSaved(false));
    },
    [widget.id, updateWidgetConfig]
  );

  const schedule = (patch, delay) => {
    pending.current = { ...pending.current, ...patch };
    setSaved(false);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => flush(), delay);
  };

  // Pick up changes made elsewhere (another tab, a restore) unless there are unsaved edits.
  useEffect(() => { if (!pending.current) setText(c.content || ""); }, [c.content]);
  useEffect(() => { if (!pending.current) setItems(c.items || []); }, [c.items]);

  useEffect(() => {
    const onHide = () => document.visibilityState === "hidden" && flush(true);
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onHide);
      flush(true);
    };
  }, [flush]);

  const setList = (next) => {
    setItems(next);
    schedule({ items: next }, 300);
  };
  const add = (e) => {
    e.preventDefault();
    if (!draft.trim()) return;
    setList([...items, { id: newId(), text: draft.trim(), done: false }]);
    setDraft("");
  };
  const doneCount = items.filter((i) => i.done).length;

  return (
    <div className={`notes notes-${mode}`}>
      {(c.title || !saved) && (
        <div className="notes-head">
          <span className="group-title">{c.title}</span>
          {!saved && <span className="notes-saving muted" title="Saving…">●</span>}
        </div>
      )}
      {mode === "note" ? (
        <textarea
          className="notes-text"
          value={text}
          placeholder={c.placeholder || "Write something…"}
          onChange={(e) => {
            setText(e.target.value);
            schedule({ content: e.target.value }, 700);
          }}
          onBlur={() => flush()}
          spellCheck
        />
      ) : (
        <>
          <ul className="todo-list">
            {items.map((it) => (
              <li key={it.id} className={it.done ? "done" : ""}>
                <label className="check">
                  <input type="checkbox" checked={!!it.done} onChange={() => setList(items.map((x) => (x.id === it.id ? { ...x, done: !x.done } : x)))} />
                  <span>{it.text}</span>
                </label>
                <button className="btn-icon todo-del" title="Remove" onClick={() => setList(items.filter((x) => x.id !== it.id))}>✕</button>
              </li>
            ))}
          </ul>
          <form className="todo-add" onSubmit={add}>
            <input value={draft} placeholder="Add a task…" onChange={(e) => setDraft(e.target.value)} />
            {doneCount > 0 && (
              <button type="button" className="btn-sm" onClick={() => setList(items.filter((x) => !x.done))} title="Remove completed tasks">
                Clear {doneCount} done
              </button>
            )}
          </form>
        </>
      )}
    </div>
  );
}

export const NotesWidget = {
  label: "Notes / to-do",
  size: { w: 6, h: 6 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    {
      key: "mode",
      label: "Kind",
      type: "select",
      options: [
        { value: "note", label: "Free-text note" },
        { value: "todo", label: "To-do list" },
      ],
    },
    { key: "placeholder", label: "Placeholder", type: "text", showIf: (c) => (c.mode || "note") === "note" },
  ],
  Render: NotesRender,
};
