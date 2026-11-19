/* Pure helpers for the gauge widget (value -> fraction, tone and SVG geometry). */

/** A stat value as a number, or null when it is not numeric (text, booleans, "3/4", empty). */
export function numericValue(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && /^\s*-?\d+(\.\d+)?\s*$/.test(value)) return Number(value);
  return null;
}

const num = (v) => (v === "" || v == null || Number.isNaN(Number(v)) ? null : Number(v));

/**
 * {value, fraction (0-1), tone: "ok" | "warn" | "bad" | null, min, max}.
 * Thresholds read "higher is worse" (warn 70, bad 90); when `bad` is below `warn` they read
 * "lower is worse" (warn 20, bad 10, e.g. free space). With no thresholds the tone is null.
 */
export function gaugeModel({ value, min, max, warn, bad } = {}) {
  const lo = num(min) ?? 0;
  let hi = num(max) ?? 100;
  if (hi <= lo) hi = lo + 1;
  const v = numericValue(value);
  const w = num(warn);
  const b = num(bad);
  let tone = null;
  if (v != null && (w != null || b != null)) {
    const lowerIsWorse = w != null && b != null && b < w;
    const past = (limit) => (limit != null && (lowerIsWorse ? v <= limit : v >= limit));
    tone = past(b) ? "bad" : past(w) ? "warn" : "ok";
  }
  const fraction = v == null ? 0 : Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
  return { value: v, fraction, tone, min: lo, max: hi };
}

/** Point on a circle, `deg` clockwise from 12 o'clock. */
export function polar(cx, cy, r, deg) {
  const a = (deg * Math.PI) / 180;
  return [+(cx + r * Math.sin(a)).toFixed(3), +(cy - r * Math.cos(a)).toFixed(3)];
}

export const ARC = { cx: 50, cy: 48, r: 40, from: -120, to: 120 }; // a 240 degree dial in a 100 x 80 box

/** SVG path of the dial's track (the whole arc). The value is drawn with the same path and a dash. */
export function arcPath({ cx, cy, r, from, to } = ARC) {
  const [x1, y1] = polar(cx, cy, r, from);
  const [x2, y2] = polar(cx, cy, r, to);
  return `M ${x1} ${y1} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x2} ${y2}`;
}

/** Text for the value ("42 %", "1,234 MB"), "n/a" for a missing or non-numeric one. */
export function gaugeText(value, unit, format) {
  const v = numericValue(value);
  if (v == null) return "n/a";
  const s = format(v);
  return unit ? `${s} ${unit}` : s;
}
