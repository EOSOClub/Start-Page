/*
 * Pure helpers for the countdown widget. A target is "YYYY-MM-DD" or "YYYY-MM-DD HH:mm" (also with a
 * "T", seconds, and a Z / +hh:mm offset), read as local time unless it carries an offset.
 */

const TARGET = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:?\d{2})?)?$/i;
const DAY = 86_400_000;

/** {ok: true, y, m, d, hh, mm, ss, hasTime, ms} or {ok: false, error}. `m` is 1-12. */
export function parseTarget(text) {
  const raw = String(text ?? "").trim();
  if (!raw) return { ok: false, error: "Enter a date, e.g. 2026-12-24 or 2026-12-24 18:00" };
  const m = TARGET.exec(raw);
  if (!m) return { ok: false, error: "Use YYYY-MM-DD or YYYY-MM-DD HH:mm" };
  const [y, mo, d, hh = "0", mi = "0", ss = "0", zone] = m.slice(1);
  const n = { y: +y, m: +mo, d: +d, hh: +hh, mm: +mi, ss: +ss };
  // Date.UTC and new Date(y, ...) read years 0-99 as 1900-1999, so "0050-01-01" would silently mean 1950.
  if (n.y < 100) return { ok: false, error: "Use a year of 0100 or later" };
  const probe = new Date(Date.UTC(n.y, n.m - 1, n.d));
  if (probe.getUTCMonth() !== n.m - 1 || probe.getUTCDate() !== n.d) return { ok: false, error: "That date does not exist" };
  if (n.hh > 23 || n.mm > 59 || n.ss > 59) return { ok: false, error: "That time does not exist" };
  const hasTime = m[4] !== undefined;
  let ms;
  if (zone) {
    const iso = `${y}-${mo}-${d}T${m[4]}:${m[5]}:${String(n.ss).padStart(2, "0")}${/^z$/i.test(zone) ? "Z" : zone.replace(/^([+-]\d{2})(\d{2})$/, "$1:$2")}`;
    ms = Date.parse(iso);
    if (Number.isNaN(ms)) return { ok: false, error: "That time does not exist" };
    const local = new Date(ms); // keep the wall-clock fields in the viewer's zone for the yearly rule
    return { ok: true, y: local.getFullYear(), m: local.getMonth() + 1, d: local.getDate(), hh: local.getHours(), mm: local.getMinutes(), ss: local.getSeconds(), hasTime: true, ms };
  }
  ms = new Date(n.y, n.m - 1, n.d, n.hh, n.mm, n.ss).getTime();
  return { ok: true, ...n, hasTime, ms };
}

/** The wall-clock moment of the target in `year` (29 February falls back to 28 February). */
function inYear(t, year) {
  const last = new Date(year, t.m, 0).getDate();
  return new Date(year, t.m - 1, Math.min(t.d, last), t.hh, t.mm, t.ss).getTime();
}

const nextMidnight = (ms) => {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1).getTime();
};

/** Whole calendar days from `a`'s day to `b`'s day (DST-proof). */
const calendarDays = (a, b) => {
  const da = new Date(a);
  const db = new Date(b);
  return Math.round((Date.UTC(db.getFullYear(), db.getMonth(), db.getDate()) - Date.UTC(da.getFullYear(), da.getMonth(), da.getDate())) / DAY);
};

/** Split a duration into days / hours / minutes / seconds. */
export function splitDuration(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  return { days: Math.floor(total / 86400), hours: Math.floor((total % 86400) / 3600), minutes: Math.floor((total % 3600) / 60), seconds: total % 60 };
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * Text for a duration down to `units` (days | hours | minutes | seconds): "12 days", "12d 4h",
 * "4h 5m", "3m 20s". Leading zero units are dropped; the smallest unit is always shown. In whole-day
 * mode `days` (when given) is used as is, otherwise the remainder is rounded up so "tomorrow" is 1 day.
 */
export function formatDuration(ms, units = "minutes", days) {
  if (units === "days") {
    const n = days ?? Math.ceil(Math.max(0, ms) / DAY);
    return plural(n, "day");
  }
  const p = splitDuration(ms);
  const order = ["days", "hours", "minutes", "seconds"];
  const cut = order.indexOf(units) < 0 ? 2 : order.indexOf(units);
  const short = { days: "d", hours: "h", minutes: "m", seconds: "s" };
  const parts = order.slice(0, cut + 1).map((u) => [u, p[u]]);
  while (parts.length > 1 && parts[0][1] === 0) parts.shift();
  return parts.map(([u, v]) => `${v}${short[u]}`).join(" ");
}

/**
 * The state of a countdown at `now` (ms):
 *   {state: "upcoming" | "today" | "passed", target: ms, diff: ms (target - now), text}
 * `spec` = {target: parseTarget result, yearly, units, after: "up" | "done" | "hide"}.
 * Yearly targets roll to the next occurrence. A date without a time is "today" for the whole day.
 */
export function countdownState(spec, now = Date.now()) {
  const t = spec.target;
  const units = spec.units || "minutes";
  let target = t.ms;
  if (spec.yearly) {
    const y = new Date(now).getFullYear();
    target = inYear(t, y);
    const endOfTargetDay = t.hasTime ? target : nextMidnight(target) - 1;
    if (now > endOfTargetDay || (t.hasTime && now >= target)) target = inYear(t, y + 1);
  }
  const diff = target - now;
  if (!t.hasTime && calendarDays(now, target) === 0) return { state: "today", target, diff, text: "Today" };
  // In whole days the answer is a difference of calendar dates, never diff / 24h: a day with a clock change is 23 or 25 hours long.
  if (diff > 0) {
    const days = units === "days" ? calendarDays(now, target) : undefined;
    if (days === 0) return { state: "upcoming", target, diff, text: "Today" }; // a timed target later today
    return { state: "upcoming", target, diff, text: formatDuration(diff, units, days) };
  }
  const since = -diff;
  const days = units === "days" ? calendarDays(target, now) : undefined;
  return { state: "passed", target, diff, text: formatDuration(since, units, days) };
}

/**
 * Milliseconds until the displayed text can change, so the widget re-renders only then
 * (once per day when counting days, once a minute for "d h m"). Capped at an hour so
 * a changed system clock or a long sleep heals itself.
 */
export function msUntilChange(spec, now = Date.now()) {
  const cap = 3_600_000;
  const t = spec.target;
  const units = spec.units || "minutes";
  const s = countdownState(spec, now);
  if (s.state === "passed" && spec.after !== "up") return cap;
  let wait;
  if (units === "days") {
    // the day count changes at midnight; a timed target that is still ahead also flips to "passed" at its moment
    wait = nextMidnight(now) - now;
    if (s.state === "upcoming" && t.hasTime) wait = Math.min(wait, s.diff + 1);
  } else {
    const step = { days: DAY, hours: 3_600_000, minutes: 60_000, seconds: 1000 }[units] ?? 60_000;
    const d = Math.abs(s.diff);
    const counting = s.state === "upcoming";
    // upcoming: the value drops when diff crosses the next lower multiple of `step`; passed: it grows at the next multiple
    const r = d % step;
    wait = units === "days" && counting ? d - (Math.ceil(d / step) - 1) * step : counting ? r + 1 : step - r;
  }
  if (!(wait > 0)) wait = 1000;
  return Math.min(cap, Math.max(50, wait + 5));
}
