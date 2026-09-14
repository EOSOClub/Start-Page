import { useEffect, useState } from "react";
import Icon from "../widgets/Icon.jsx";
import { api } from "../api.js";

/** Inline icon search over the dashboard-icons set. Calls onPick(name). */
export default function IconPicker({ onPick, onClose, initial = "" }) {
  const [q, setQ] = useState(initial);
  const [res, setRes] = useState({ total: 0, icons: [] });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true);
    const t = setTimeout(() => {
      api.searchIcons(q, 80)
        .then((r) => live && setRes(r))
        .catch(() => live && setRes({ total: 0, icons: [] }))
        .finally(() => live && setLoading(false));
    }, 150);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q]);

  return (
    <div className="icon-picker">
      <div className="row">
        <input className="search" autoFocus placeholder="Search icons…" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 0 }} />
        <button type="button" className="btn-sm" onClick={onClose}>Close</button>
      </div>
      <div className="icon-grid">
        {res.icons.map((n) => (
          <button type="button" key={n} className="icon-cell" title={n} onClick={() => onPick(n)}>
            <Icon icon={n} fallback={n} size={28} />
            <span>{n}</span>
          </button>
        ))}
      </div>
      <div className="help">
        {loading ? "Searching…" : res.total === 0 ? "No icons found (or icon list unavailable offline). You can still type an emoji or image URL." : `${res.total} matches${res.total > res.icons.length ? ", showing first " + res.icons.length : ""}`}
      </div>
    </div>
  );
}
