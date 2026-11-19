/*
 * Pure helpers for the calendar agenda widget: turn the events of one or more `ics` integrations
 * ({title, start, end, all_day, location, description}, ISO 8601 with offset, all-day end exclusive)
 * into day groups. A "day" is a calendar day in the display time zone, never 24 hours, so a DST day
 * of 23 or 25 hours still groups correctly.
 */

const fmtCache = new Map();
const formatter = (locale, options) => {
  const key = `${locale ?? ""}|${JSON.stringify(options)}`;
  let f = fmtCache.get(key);
  if (!f) fmtCache.set(key, (f = new Intl.DateTimeFormat(locale, options)));
  return f;
};

/** The zone name when Intl knows it, else undefined (the browser's own zone). */
export function validZone(tz) {
  if (!tz) return undefined;
  try {
    formatter("en-CA", { timeZone: tz });
    return tz;
  } catch {
    return undefined;
  }
}

/** "YYYY-MM-DD" of an instant in `tz`. */
export function dayKeyOf(date, tz) {
  const parts = formatter("en-CA", { timeZone: validZone(tz), year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const get = (t) => parts.find((p) => p.type === t).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** Calendar arithmetic on "YYYY-MM-DD" keys (UTC maths, so DST cannot skew it). */
export function addDays(key, n) {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** Hour:minute of an instant in `tz`, e.g. "09:30" or "9:30 AM" depending on the locale. */
export function timeOf(date, tz, locale) {
  return formatter(locale, { timeZone: validZone(tz), hour: "numeric", minute: "2-digit" }).format(date);
}

/** True when `date` is exactly 00:00:00 on its day in `tz`. */
function atMidnight(date, tz) {
  const parts = formatter("en-GB", { timeZone: validZone(tz), hourCycle: "h23", hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(date);
  return parts.filter((p) => ["hour", "minute", "second"].includes(p.type)).every((p) => Number(p.value) === 0);
}

/** A real "YYYY-MM-DD" calendar date (the shape and the date both: "2026-02-31" is not one). */
function validKey(k) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(k)) return false;
  const [y, m, d] = k.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
}

/** Event text from remote data: a string or number, else the fallback (an object must never reach a React child or localeCompare). */
const textOf = (v, fallback = "") => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : fallback);

/** "Today", "Tomorrow", else "Friday, Nov 20". */
export function dayLabel(key, todayKey, locale) {
  if (key === todayKey) return "Today";
  if (key === addDays(todayKey, 1)) return "Tomorrow";
  const [y, m, d] = key.split("-").map(Number);
  return formatter(locale, { timeZone: "UTC", weekday: "long", month: "short", day: "numeric" }).format(new Date(Date.UTC(y, m - 1, d, 12)));
}

/**
 * The first and last calendar day an event touches, plus its instants.
 * All-day events carry their own local dates in the ISO text (midnight to exclusive midnight).
 */
function span(e, tz) {
  const start = new Date(e.start);
  const end = new Date(e.end ?? e.start);
  if (Number.isNaN(start.getTime())) return null;
  if (e.all_day) {
    // all-day dates are the feed's own local dates; anything that is not a real date falls back instead of throwing
    const first = validKey(String(e.start).slice(0, 10)) ? String(e.start).slice(0, 10) : dayKeyOf(start, tz);
    const endKey = e.end ? String(e.end).slice(0, 10) : "";
    const lastExclusive = validKey(endKey) ? endKey : addDays(first, 1);
    const last = lastExclusive > first ? addDays(lastExclusive, -1) : first;
    return { start, end: Number.isNaN(end.getTime()) ? start : end, first, last };
  }
  const first = dayKeyOf(start, tz);
  const safeEnd = Number.isNaN(end.getTime()) || end < start ? start : end;
  let last = dayKeyOf(safeEnd, tz);
  if (safeEnd > start && atMidnight(safeEnd, tz)) last = addDays(last, -1); // ends at midnight: nothing on the next day
  return { start, end: safeEnd, first, last: last < first ? first : last };
}

/**
 * Agenda groups for the next `days` days from `now`:
 *   [{key, label, events: [{title, location, allDay, time, inProgress, part?: {index, count}}]}]
 * Events that already ended are left out; a multi-day event appears on each day it covers (as "All day",
 * or "Until 02:00" on its last day); at most `limit` rows in total; days without events are omitted.
 */
export function groupAgenda(events, { now = new Date(), tz, days = 7, limit = 10, locale } = {}) {
  const zone = validZone(tz);
  const todayKey = dayKeyOf(now, zone);
  const lastKey = addDays(todayKey, Math.max(1, Math.floor(days)) - 1);
  const byDay = new Map();
  for (const e of events ?? []) {
    const s = span(e, zone);
    if (!s) continue;
    if (e.all_day ? s.last < todayKey : s.end <= now && !(s.end.getTime() === s.start.getTime() && s.start >= now)) continue; // finished
    const count = Math.round((Date.parse(s.last) - Date.parse(s.first)) / 86_400_000) + 1;
    for (let key = s.first < todayKey ? todayKey : s.first; key <= s.last && key <= lastKey; key = addDays(key, 1)) {
      const index = Math.round((Date.parse(key) - Date.parse(s.first)) / 86_400_000) + 1;
      const first = key === s.first;
      let time = "All day";
      if (!e.all_day) time = first ? timeOf(s.start, zone, locale) : key === s.last ? `Until ${timeOf(s.end, zone, locale)}` : "All day";
      const row = {
        title: textOf(e.title, "(no title)"),
        location: textOf(e.location),
        allDay: !!e.all_day,
        time,
        // only today's row of an ongoing multi-day event carries the marker
        inProgress: !e.all_day && key === todayKey && s.start <= now && now < s.end,
        // sorted before timed events of the day: all-day, then events carried over from yesterday
        order: e.all_day ? 0 : first ? 2 : 1,
        at: first ? s.start.getTime() : 0,
      };
      if (count > 1) row.part = { index, count };
      if (!byDay.has(key)) byDay.set(key, []);
      byDay.get(key).push(row);
    }
  }
  const groups = [];
  let left = Math.max(1, Math.floor(limit));
  for (const key of [...byDay.keys()].sort()) {
    if (left <= 0) break;
    const rows = byDay.get(key).sort((a, b) => a.order - b.order || a.at - b.at || a.title.localeCompare(b.title)).slice(0, left);
    left -= rows.length;
    groups.push({ key, label: dayLabel(key, todayKey, locale), events: rows.map(({ order, at, ...row }) => row) });
  }
  return groups;
}
