/* Pure helpers for the health-history widgets (uptime bars, sparklines, incident log). */

/** "99.8%" for a 0-100 percentage; floors so only a perfect record reads "100%". */
export function formatUptime(pct) {
  if (pct == null || Number.isNaN(pct)) return "";
  if (pct >= 100) return "100%";
  return `${(Math.floor(pct * 10 + 1e-9) / 10).toFixed(1)}%`;
}

/** Heartbeat-bar class suffix for a bucket: "" = no data (gap), 1 = all up, 0 = all down, 2 = mixed. */
export function beatClass(bucket) {
  if (!bucket || !bucket.total) return "";
  if (bucket.up >= bucket.total) return "1";
  if (bucket.up === 0) return "0";
  return "2";
}

export const BAR_WIDTH = 4;
export const BAR_GAP = 2;

/** How many heartbeat bars fit in `width` px (bars are BAR_WIDTH wide with BAR_GAP between). */
export function barCapacity(width, bar = BAR_WIDTH, gap = BAR_GAP) {
  if (!Number.isFinite(width) || width <= 0) return 0;
  return Math.floor((width + gap) / (bar + gap));
}

/** The newest `capacity` buckets (oldest dropped). `null`/`undefined` capacity means "not measured": all. */
export function visibleBars(buckets, capacity) {
  const list = buckets ?? [];
  if (capacity == null || capacity >= list.length) return list;
  return capacity <= 0 ? [] : list.slice(-capacity);
}

/** Screen-reader summary of an uptime bar strip. */
export function uptimeSummary(name, history, shown = history?.buckets) {
  if (!history || !history.checks) return `${name}: no uptime data`;
  const failed = (shown ?? []).filter((b) => b.total > b.up).length;
  return (
    `${name}: ${formatUptime(history.uptime_pct)} uptime over ${history.checks} checks` +
    (failed ? `, ${failed} of the last ${(shown ?? []).length} periods had failed checks` : ", no failed checks")
  );
}

/** Text alternative for a latency sparkline. */
export function sparklineSummary(buckets) {
  const lat = (buckets ?? []).map((b) => b.latency_ms).filter((v) => v != null);
  if (!lat.length) return "Latency: no data";
  const failed = buckets.filter((b) => b.total > b.up).length;
  return (
    `Latency ${Math.round(Math.min(...lat))} to ${Math.round(Math.max(...lat))} ms over ${buckets.length} periods` +
    (failed ? `, ${failed} with failed checks` : "")
  );
}

/**
 * Geometry for a latency sparkline over `buckets` ({up,total,latency_ms}, oldest first).
 * - segments: runs of [x, y] points; the line breaks at buckets with no latency (gaps, offline).
 * - marks: x of every bucket that had a failed check, drawn as a red tick.
 * y grows downward (SVG); 0 ms sits on the bottom edge.
 */
export function sparklineModel(buckets, width = 100, height = 24) {
  const n = buckets?.length ?? 0;
  const segments = [];
  const marks = [];
  if (!n) return { segments, marks, max: 0 };
  const max = Math.max(1, ...buckets.map((b) => b.latency_ms ?? 0));
  const pad = 2;
  const step = width / n;
  let run = null;
  buckets.forEach((b, i) => {
    const x = +(step * (i + 0.5)).toFixed(2);
    if (b.total > b.up) marks.push(x);
    if (b.latency_ms == null) {
      run = null;
      return;
    }
    const y = +(height - pad - (b.latency_ms / max) * (height - pad * 2)).toFixed(2);
    if (!run) {
      run = [];
      segments.push(run);
    }
    run.push([x, y]);
  });
  return { segments, marks, max };
}

/** "just now", "5m ago", "3h ago", "2d ago" for unix seconds. */
export function relativeTime(ts, now = Date.now() / 1000) {
  const s = Math.max(0, now - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
