import { useEffect, useRef, useState } from "react";
import Icon from "../widgets/Icon.jsx";
import { api } from "../api.js";
import { hostOf, looksLikeUrl, normalizeUrl, tidyTitle } from "../search.js";

const newId = () => Math.random().toString(36).slice(2, 10);

/** Icon preview that waits until the URL stops changing, so typing doesn't fetch a favicon per keystroke. */
function PreviewIcon({ icon, url, name }) {
  const [settled, setSettled] = useState(url);
  useEffect(() => {
    const t = setTimeout(() => setSettled(url), 800);
    return () => clearTimeout(t);
  }, [url]);
  const ready = settled === url && looksLikeUrl(url || "");
  return <Icon icon={icon} url={ready ? url : undefined} fallback={name || hostOf(url || "?")} size={22} />;
}

/** One link per line: "https://url", "Name | url", "Name url" or a Markdown "[Name](url)". */
export function parseLinks(text) {
  const out = [];
  for (let line of text.split(/\r?\n/)) {
    line = line.trim().replace(/^[-*]\s+/, "");
    if (!line) continue;
    let name = "";
    let url = line;
    const md = line.match(/^\[([^\]]*)\]\(([^)\s]+)\)$/);
    if (md) [, name, url] = md;
    else if (line.includes("|")) [name, url] = line.split("|").map((s) => s.trim());
    else {
      const parts = line.split(/\s+/);
      const last = parts[parts.length - 1];
      if (parts.length > 1 && looksLikeUrl(last)) {
        url = last;
        name = parts.slice(0, -1).join(" ");
      }
    }
    if (looksLikeUrl(url)) out.push({ id: newId(), name, url: normalizeUrl(url), icon: "" });
  }
  return out;
}

/** Editable list of {id, name, url, icon} used by the Bookmarks widget. */
export default function LinksEditor({ value, onChange }) {
  const [bulk, setBulk] = useState(null); // textarea contents while the paste box is open
  const [busy, setBusy] = useState(false);
  // Title lookups finish after further edits, so apply them to the latest list.
  const latest = useRef({ value, onChange });
  latest.current = { value, onChange };

  const update = (id, patch) => onChange(value.map((l) => (l.id === id ? { ...l, ...patch } : l)));
  const move = (i, d) => {
    const next = [...value];
    const [item] = next.splice(i, 1);
    next.splice(i + d, 0, item);
    onChange(next);
  };

  const fillNames = async (ids) => {
    setBusy(true);
    try {
      const results = await Promise.all(
        ids.map((id) => {
          const link = latest.current.value.find((l) => l.id === id);
          if (!link) return [id, ""];
          return api.siteMeta(link.url).then((m) => [id, tidyTitle(m.title)]).catch(() => [id, ""]);
        })
      );
      const names = Object.fromEntries(results.filter(([, t]) => t));
      const cur = latest.current;
      cur.onChange(cur.value.map((l) => (!l.name && names[l.id] ? { ...l, name: names[l.id] } : l)));
    } finally {
      setBusy(false);
    }
  };

  const addBulk = () => {
    const added = parseLinks(bulk || "");
    setBulk(null);
    if (!added.length) return;
    const next = [...value, ...added];
    latest.current.value = next;
    onChange(next);
    fillNames(added.filter((l) => !l.name).map((l) => l.id));
  };

  const unnamed = value.filter((l) => !l.name && l.url);

  return (
    <div className="links-editor">
      {value.length === 0 && <div className="muted">No links yet.</div>}
      {value.map((l, i) => (
        <div className="link-row" key={l.id}>
          <PreviewIcon icon={l.icon} url={l.url} name={l.name} />
          <input type="text" value={l.name} placeholder={l.url ? hostOf(l.url) : "Name"} onChange={(e) => update(l.id, { name: e.target.value })} />
          <input type="text" value={l.url} placeholder="https://…" onChange={(e) => update(l.id, { url: e.target.value })} onBlur={(e) => e.target.value && update(l.id, { url: normalizeUrl(e.target.value) })} />
          <input type="text" className="link-icon-input" value={l.icon} placeholder="icon" title="Emoji, image URL or dashboard-icons name. Empty = site icon." onChange={(e) => update(l.id, { icon: e.target.value })} />
          <div className="link-row-btns">
            <button type="button" className="btn-sm" disabled={i === 0} onClick={() => move(i, -1)} title="Move up">↑</button>
            <button type="button" className="btn-sm" disabled={i === value.length - 1} onClick={() => move(i, 1)} title="Move down">↓</button>
            <button type="button" className="btn-sm btn-danger" onClick={() => onChange(value.filter((x) => x.id !== l.id))} title="Remove">✕</button>
          </div>
        </div>
      ))}
      {bulk !== null ? (
        <div className="links-bulk">
          <textarea rows={5} autoFocus value={bulk} placeholder={"One per line:\nhttps://github.com\nMail | https://mail.proton.me\n[News](https://news.ycombinator.com)"} onChange={(e) => setBulk(e.target.value)} />
          <div className="row">
            <span className="spacer" />
            <button type="button" className="btn-sm" onClick={() => setBulk(null)}>Cancel</button>
            <button type="button" className="btn-sm btn-primary" onClick={addBulk}>Add links</button>
          </div>
        </div>
      ) : (
        <div className="row">
          <button type="button" className="btn-sm" onClick={() => onChange([...value, { id: newId(), name: "", url: "", icon: "" }])}>+ Add link</button>
          <button type="button" className="btn-sm" onClick={() => setBulk("")}>Paste a list…</button>
          {unnamed.length > 0 && (
            <button type="button" className="btn-sm" disabled={busy} onClick={() => fillNames(unnamed.map((l) => l.id))}>
              {busy ? "Fetching names…" : `Fetch ${unnamed.length} name${unnamed.length > 1 ? "s" : ""}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
