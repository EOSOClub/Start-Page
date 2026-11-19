import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api.js";
import {
  barCapacity,
  beatClass,
  formatUptime,
  relativeTime,
  sparklineModel,
  sparklineSummary,
  uptimeSummary,
  visibleBars,
} from "./healthViz.js";

/* Widgets and pieces driven by the health history (/api/health/history and /events). */

const EVENTS_POLL_MS = 60_000;
const SPARK_W = 100;
const SPARK_H = 24;

/** Inline latency sparkline: gaps stay empty, buckets with failed checks get a red tick. */
export function Sparkline({ buckets }) {
  const { segments, marks } = sparklineModel(buckets, SPARK_W, SPARK_H);
  if (!segments.length && !marks.length) return null;
  const label = sparklineSummary(buckets);
  return (
    <svg className="spark" viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <title>{label}</title>
      {marks.map((x) => (
        <line key={`m${x}`} className="spark-down" x1={x} x2={x} y1={0} y2={SPARK_H} />
      ))}
      {segments.map((seg, i) =>
        seg.length > 1 ? (
          <polyline key={i} className="spark-line" points={seg.map((p) => p.join(",")).join(" ")} />
        ) : (
          <circle key={i} className="spark-dot" cx={seg[0][0]} cy={seg[0][1]} r={1.2} />
        )
      )}
    </svg>
  );
}

// ---------- Uptime (heartbeat bars from our own checks) ----------
/** One row: name (ellipsised), as many bars as fit (newest on the right), uptime %. */
function UptimeRow({ service, h, status }) {
  const ref = useRef(null);
  const [capacity, setCapacity] = useState(null); // null until measured (or no ResizeObserver)
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const measure = () => setCapacity(barCapacity(el.clientWidth));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const shown = visibleBars(h?.buckets, capacity);
  return (
    <div className="kuma-row uptime-row">
      {status !== "disabled" && <span className={`status-dot status-${status}`} />}
      <span className="kuma-name" title={service.name}>{service.name}</span>
      {/* DOM order is newest first and the strip is row-reverse: if CSS has to clip, the oldest goes. */}
      <span ref={ref} className="beats uptime-beats" role="img" aria-label={uptimeSummary(service.name, h, shown)}>
        {[...shown].reverse().map((b, i) => (
          <i key={b.t ?? i} className={`beat beat-${beatClass(b)}`} title={b.total ? `${b.up}/${b.total} checks up` : "no data"} />
        ))}
      </span>
      <span className="muted nowrap uptime-pct">{h ? formatUptime(h.uptime_pct) : ""}</span>
    </div>
  );
}

export const UptimeWidget = {
  label: "Uptime",
  size: { w: 6, h: 5 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    { key: "service_ids", label: "Services", type: "services", help: "Leave empty to use the category below." },
    { key: "category", label: "Category", type: "category", placeholder: "(all)" },
  ],
  Render: ({ widget, services, health, history = {} }) => {
    const c = widget.config;
    const picked = c.service_ids ?? [];
    const rows = picked.length
      ? picked.map((id) => services.find((s) => s.id === id)).filter(Boolean)
      : services.filter((s) => !c.category || (s.category || "") === c.category);
    return (
      <div className="group">
        {c.title && <div className="group-title">{c.title}</div>}
        <div className="scroll">
          {rows.length === 0 && <div className="muted">No services</div>}
          {rows.map((s) => (
            <UptimeRow
              key={s.id}
              service={s}
              h={history[s.id]}
              status={health[s.id]?.status ?? (s.health_enabled ? "unknown" : "disabled")}
            />
          ))}
        </div>
      </div>
    );
  },
};

// ---------- Incidents (state-change log) ----------
export const IncidentsWidget = {
  label: "Incidents",
  size: { w: 6, h: 5 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    { key: "limit", label: "Number of incidents", type: "number", default: 10, min: 1 },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const limit = Math.max(1, Number(c.limit) || 10);
    const [events, setEvents] = useState(null);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
      let live = true;
      const load = () => {
        if (document.hidden) return;
        api
          .healthEvents({ limit })
          .then((e) => live && (setEvents(e || []), setFailed(false)))
          .catch(() => live && setFailed(true));
      };
      load();
      const t = setInterval(load, EVENTS_POLL_MS);
      const onVisibility = () => !document.hidden && load();
      document.addEventListener("visibilitychange", onVisibility);
      return () => {
        live = false;
        clearInterval(t);
        document.removeEventListener("visibilitychange", onVisibility);
      };
    }, [limit]);
    return (
      <div className="group">
        {c.title && <div className="group-title">{c.title}</div>}
        <div className="scroll">
          {failed && <div className="muted" role="alert">Could not load incidents{events ? " (showing the last result)" : ""}.</div>}
          {!events && !failed && <div className="muted">Loading…</div>}
          {events && events.length === 0 && !failed && <div className="muted">No incidents</div>}
          {(events ?? []).map((e) => (
            <div key={e.id} className="kuma-row" title={e.error || undefined}>
              <span className={`status-dot status-${e.to_status}`} />
              <span className="kuma-name">{e.service_name}</span>
              <span className="muted nowrap">{e.from_status} → {e.to_status}</span>
              <span className="muted nowrap">{relativeTime(e.at)}</span>
            </div>
          ))}
        </div>
      </div>
    );
  },
};
