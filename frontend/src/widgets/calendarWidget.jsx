import { useMemo } from "react";
import { Header } from "./integrations.jsx";
import { groupAgenda } from "./calendar.js";
import { useNow } from "./startpage.jsx";

/* Calendar agenda: upcoming events of one or more ICS integrations, grouped by day. */

/**
 * Older widgets (and hand-written configs) may carry a single `integration_id`, which this widget's catalog does not
 * declare. It is folded into `integration_ids` on read, and the editor saves the result without it.
 */
export function migrateCalendar(config) {
  const c = config || {};
  if (c.integration_id === undefined) return c;
  const { integration_id, ...rest } = c;
  const ids = Array.isArray(rest.integration_ids) ? rest.integration_ids : [];
  return { ...rest, integration_ids: typeof integration_id === "string" && integration_id && !ids.includes(integration_id) ? [...ids, integration_id] : ids };
}

export const CalendarWidget = {
  migrate: migrateCalendar,
  label: "Calendar agenda",
  group: "Integrations",
  size: { w: 6, h: 6 },
  fields: [
    { key: "integration_ids", label: "Calendar feeds (ICS integrations)", type: "integration", integrationType: "ics", multiple: true },
    { key: "title", label: "Title", type: "text", placeholder: "(feed name)" },
    { key: "days", label: "Days to show", type: "number", default: 7, min: 1, max: 60, help: "The feed itself only looks ahead as far as its own “days ahead” setting." },
    { key: "limit", label: "Max events", type: "number", default: 10, min: 1 },
    { key: "show_location", label: "Show location", type: "checkbox", default: true },
  ],
  Render: ({ widget, integrations, integrationData }) => {
    const c = migrateCalendar(widget.config);
    const now = useNow(); // re-renders on the minute, which is as fine as "in progress" needs
    const ids = Array.isArray(c.integration_ids) ? c.integration_ids : [];
    const feeds = ids.map((id) => integrations.find((i) => i.id === id && i.type === "ics")).filter(Boolean);
    const datas = feeds.map((f) => integrationData[f.id]);
    const usable = datas.filter((d) => d && d.data);
    const tz = usable.find((d) => d.data.timezone)?.data.timezone;
    const days = Math.max(1, Number(c.days) || 7);
    const limit = Math.max(1, Number(c.limit) || 10);
    const idsKey = ids.join(",");
    const events = useMemo(() => usable.flatMap((d) => (Array.isArray(d.data.events) ? d.data.events : [])), [integrationData, integrations, idsKey]); // eslint-disable-line react-hooks/exhaustive-deps
    const groups = useMemo(() => groupAgenda(events, { now, tz, days, limit }), [events, now, tz, days, limit]);

    if (feeds.length === 0) return <div className="muted pad">Select an ICS integration in widget settings.</div>;
    if (usable.length === 0) {
      const failed = datas.find((d) => d && !d.ok);
      return failed ? <div className="pad error">Error: {failed.error}</div> : <div className="muted pad">Waiting for first fetch…</div>;
    }
    const stale = usable.filter((d) => !d.ok).length + (datas.length - usable.length);
    return (
      <div className="group">
        <Header
          title={c.title || (feeds.length === 1 ? feeds[0].name : "Calendar")}
          right={stale ? <span className="muted">{stale === datas.length ? "stale" : `${stale} feed${stale === 1 ? "" : "s"} unavailable`}</span> : null}
        />
        <div className="scroll">
          {groups.length === 0 && <div className="muted">No events in the next {days} day{days === 1 ? "" : "s"}.</div>}
          {groups.map((g) => (
            <section key={g.key} className="cal-day" aria-label={g.label}>
              <div className="cal-day-head">{g.label}</div>
              {g.events.map((e, i) => (
                <div key={i} className={`cal-row${e.inProgress ? " cal-live" : ""}`}>
                  <span className="cal-time muted nowrap">{e.time}</span>
                  <span className="cal-title">
                    {e.inProgress && <span className="cal-dot" role="img" aria-label="In progress" title="In progress" />}
                    {e.title}
                    {e.part && <span className="muted"> ({e.part.index}/{e.part.count})</span>}
                  </span>
                  {c.show_location !== false && e.location && <span className="muted cal-loc" title={e.location}>{e.location}</span>}
                </div>
              ))}
            </section>
          ))}
        </div>
      </div>
    );
  },
};
