import { BlockingBar } from "./actionControls.jsx";
import FitText from "./FitText.jsx";
import { Header, Problem, useIntegration } from "./integrations.jsx";
import { sparklineModel } from "./healthViz.js";
import { dotStatus, formatValue, itemIdentity, pickStats, progressPercent, safeLink, seriesBuckets, seriesSummary, toneClass } from "./integrationViz.js";

/*
 * Widgets for the normalised integration shape: any integration whose type provides `stats` or
 * `items` (see backend/app/integrations/__init__.py) works with them, plus a Pi-hole preset with a
 * blocking switch and a Prometheus preset of the stats widget.
 */

const SPARK_W = 100;
const SPARK_H = 24;

/** Sparkline of a stat's `series` (the same geometry as the uptime latency sparkline). */
export function SeriesSpark({ label, series }) {
  const { segments } = sparklineModel(seriesBuckets(series), SPARK_W, SPARK_H);
  if (!segments.length) return null;
  const text = seriesSummary(label, series);
  return (
    <svg className="spark" viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} preserveAspectRatio="none" role="img" aria-label={text}>
      <title>{text}</title>
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

/** The stats as tiles that scale to the widget (FitText) or as label/value rows. */
export function StatTiles({ stats, layout = "tiles", sparkline = true }) {
  if (layout === "rows") {
    return (
      <div className="scroll">
        {stats.map((s) => (
          <div key={s.key} className="kuma-row istat-row">
            <span className="kuma-name">{s.label}</span>
            {sparkline && s.series?.length > 1 && <span className="istat-spark"><SeriesSpark label={s.label} series={s.series} /></span>}
            <b className={toneClass(s.tone)}>{formatValue(s.value)}{s.unit ? <span className="muted"> {s.unit}</span> : null}</b>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="istats-body">
      <FitText className="istats" min={9}>
        <div className="istat-grid">
          {stats.map((s) => (
            <div key={s.key} className="istat">
              <b className={toneClass(s.tone)}>{formatValue(s.value)}{s.unit ? <small> {s.unit}</small> : null}</b>
              <span>{s.label}</span>
              {sparkline && s.series?.length > 1 && <SeriesSpark label={s.label} series={s.series} />}
            </div>
          ))}
        </div>
      </FitText>
    </div>
  );
}

/** A compact wrapping row of small value/label pairs, for stats shown above a list. */
export function StatStrip({ stats, sparkline = true }) {
  return (
    <div className="istat-strip">
      {stats.map((s) => (
        <div key={s.key} className="istat-mini">
          <b className={toneClass(s.tone)}>{formatValue(s.value)}{s.unit ? <small> {s.unit}</small> : null}</b>
          <span>{s.label}</span>
          {sparkline && s.series?.length > 1 && <SeriesSpark label={s.label} series={s.series} />}
        </div>
      ))}
    </div>
  );
}

export const STATS_FIELDS = [
  { key: "title", label: "Title", type: "text", placeholder: "(integration name)" },
  { key: "stat_keys", label: "Stats to show (keys, in order)", type: "statkeys", placeholder: "(all)", help: "Comma-separated stat keys, e.g. queries, blocked. Leave empty to show every stat." },
  {
    key: "layout",
    label: "Layout",
    type: "select",
    default: "tiles",
    options: [
      { value: "tiles", label: "Tiles (scale to the widget)" },
      { value: "rows", label: "Rows" },
    ],
  },
  { key: "show_sparkline", label: "Show sparklines (when a stat has a series)", type: "checkbox", default: true },
];

function StatsRender({ widget, integrations, integrationData }) {
  const { integ, data } = useIntegration(widget, integrations, integrationData);
  const c = widget.config;
  if (!integ || !data || (!data.ok && !data.data)) return <Problem integ={integ} data={data} type="stats" />;
  const all = data.data?.stats;
  if (!Array.isArray(all)) return <div className="muted pad">{integ.name} does not provide stats.</div>;
  const stats = pickStats(all, c.stat_keys);
  return (
    <div className="group">
      <Header title={c.title || integ.name} right={!data.ok ? <span className="muted">stale</span> : null} />
      {stats.length === 0 ? <div className="muted">No matching stats.</div> : <StatTiles stats={stats} layout={c.layout} sparkline={c.show_sparkline !== false} />}
    </div>
  );
}

// ---------- generic: stats ----------
export const IntegrationStatsWidget = {
  label: "Integration stats",
  size: { w: 6, h: 3 },
  fields: [{ key: "integration_id", label: "Integration (any that provides stats)", type: "integration", capability: "stats" }, ...STATS_FIELDS],
  Render: StatsRender,
};

// ---------- preset: Prometheus ----------
export const PrometheusWidget = {
  label: "Prometheus",
  size: { w: 6, h: 3 },
  fields: [{ key: "integration_id", label: "Prometheus integration", type: "integration", integrationType: "prometheus" }, ...STATS_FIELDS],
  Render: StatsRender,
};

/** The rows of an `items` list. `rowAction(item)` may return a control placed at the end of the row. */
export function ItemRows({ items, newTab, empty = "Nothing to show.", rowAction }) {
  const seen = new Map();
  return (
    <div className="scroll">
      {items.length === 0 && <div className="muted">{empty}</div>}
      {items.map((it) => {
        const pct = progressPercent(it.progress);
        const href = safeLink(it.url);
        // Keyed by identity, not position: a poll that reorders rows must not hand an open row menu to another row.
        const id = itemIdentity(it);
        const nth = (seen.get(id) || 0) + 1;
        seen.set(id, nth);
        return (
          <div key={`${id}#${nth}`} className="irow">
            <div className="kuma-row">
              {it.status && <span className={`status-dot status-${dotStatus(it.status)}`} title={it.status} />}
              {href ? (
                <a className="kuma-name" href={href} target={newTab ? "_blank" : "_self"} rel="noreferrer" draggable={false}>{it.title}</a>
              ) : (
                <span className="kuma-name">{it.title}</span>
              )}
              {it.subtitle && <span className="muted irow-sub">{it.subtitle}</span>}
              {it.meta && <span className="muted nowrap">{it.meta}</span>}
              {rowAction?.(it)}
            </div>
            {pct != null && (
              <div className="iprog" role="progressbar" aria-label={it.title} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
                <i style={{ width: `${pct}%` }} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------- generic: list ----------
export const IntegrationListWidget = {
  label: "Integration list",
  size: { w: 6, h: 5 },
  fields: [
    { key: "integration_id", label: "Integration (any that provides items)", type: "integration", capability: "items" },
    { key: "title", label: "Title", type: "text", placeholder: "(integration name)" },
    { key: "limit", label: "Max rows", type: "number", default: 10, min: 1 },
    { key: "newTab", label: "Open links in new tab", type: "checkbox" },
  ],
  Render: ({ widget, integrations, integrationData }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData);
    const c = widget.config;
    if (!integ || !data || (!data.ok && !data.data)) return <Problem integ={integ} data={data} type="items" />;
    const all = data.data?.items;
    if (!Array.isArray(all)) return <div className="muted pad">{integ.name} does not provide items.</div>;
    const items = all.slice(0, Math.max(1, Number(c.limit) || 10));
    return (
      <div className="group">
        <Header title={c.title || integ.name} right={!data.ok ? <span className="muted">stale</span> : <span className="muted">{all.length}</span>} />
        <ItemRows items={items} newTab={c.newTab} />
      </div>
    );
  },
};

// ---------- Pi-hole ----------
export const PiholeWidget = {
  label: "Pi-hole",
  size: { w: 6, h: 4 },
  fields: [
    { key: "integration_id", label: "Pi-hole integration", type: "integration", integrationType: "pihole" },
    { key: "title", label: "Title", type: "text", placeholder: "Pi-hole" },
    { key: "stat_keys", label: "Stats to show (keys, in order)", type: "statkeys", default: "queries, blocked, percent_blocked, domains", help: "Keys: queries, blocked, percent_blocked, domains, status, clients." },
  ],
  Render: ({ widget, integrations, integrationData, onRefresh }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData, "pihole");
    const c = widget.config;
    if (!integ || !data || (!data.ok && !data.data)) return <Problem integ={integ} data={data} type="Pi-hole" />;
    const d = data.data;
    const stats = pickStats(d.stats, c.stat_keys ?? "queries, blocked, percent_blocked, domains");
    return (
      <div className="group">
        <Header title={c.title || integ.name} right={!data.ok ? <span className="muted">stale</span> : null} />
        <StatTiles stats={stats} layout="tiles" sparkline={false} />
        <BlockingBar integId={integ.id} blocking={d.blocking} onRefresh={onRefresh} action="set_blocking" noun="blocking" />
      </div>
    );
  },
};
