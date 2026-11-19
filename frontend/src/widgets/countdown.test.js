import { afterEach, describe, it, expect, vi } from "vitest";
import { countdownState, formatDuration, msUntilChange, parseTarget, splitDuration } from "./countdown.js";

const local = (...a) => new Date(...a).getTime(); // (y, monthIndex, d, h, m, s) in local time
const spec = (text, extra = {}) => ({ target: parseTarget(text), units: "minutes", after: "up", ...extra });

describe("parseTarget", () => {
  it("reads a date, a date with time, and ISO forms", () => {
    expect(parseTarget("2026-12-24")).toMatchObject({ ok: true, y: 2026, m: 12, d: 24, hasTime: false, ms: local(2026, 11, 24) });
    expect(parseTarget("2026-12-24 18:30")).toMatchObject({ ok: true, hasTime: true, ms: local(2026, 11, 24, 18, 30) });
    expect(parseTarget(" 2026-12-24T18:30:15 ")).toMatchObject({ ok: true, ms: local(2026, 11, 24, 18, 30, 15) });
    expect(parseTarget("2026-12-24T18:00:00+01:00").ms).toBe(Date.parse("2026-12-24T17:00:00Z"));
    expect(parseTarget("2026-12-24T18:00Z").ms).toBe(Date.parse("2026-12-24T18:00:00Z"));
  });

  it("rejects text that is not a date, impossible dates and impossible times", () => {
    for (const bad of ["", "tomorrow", "24/12/2026", "2026-13-01", "2026-02-30", "2025-02-29", "2026-12-24 24:00", "2026-12-24 12:60"]) {
      expect(parseTarget(bad).ok, bad).toBe(false);
      expect(parseTarget(bad).error).toBeTruthy();
    }
    expect(parseTarget("2028-02-29").ok).toBe(true); // a real leap day
  });
});

describe("formatDuration", () => {
  it("drops leading zero units and keeps the smallest unit asked for", () => {
    const ms = ((12 * 24 + 4) * 3600 + 5 * 60 + 3) * 1000;
    expect(formatDuration(ms, "seconds")).toBe("12d 4h 5m 3s");
    expect(formatDuration(ms, "minutes")).toBe("12d 4h 5m");
    expect(formatDuration(ms, "hours")).toBe("12d 4h");
    expect(formatDuration(ms, "days")).toBe("13 days"); // whole days round up: 12 days and a bit is 13 days to go
    expect(formatDuration(5 * 60_000 + 3000, "seconds")).toBe("5m 3s");
    expect(formatDuration(0, "minutes")).toBe("0m");
    expect(formatDuration(86_400_000, "days")).toBe("1 day");
    expect(splitDuration(90_061_000)).toEqual({ days: 1, hours: 1, minutes: 1, seconds: 1 });
  });
});

describe("countdownState", () => {
  it("counts down to a moment, then up", () => {
    const s = spec("2026-12-24 18:00");
    expect(countdownState(s, local(2026, 11, 24, 12, 0))).toMatchObject({ state: "upcoming", text: "6h 0m" });
    expect(countdownState(s, local(2026, 11, 24, 17, 59, 30))).toMatchObject({ state: "upcoming", text: "0m" });
    expect(countdownState(s, local(2026, 11, 24, 18, 5))).toMatchObject({ state: "passed", text: "5m" });
    expect(countdownState(s, local(2026, 11, 26, 20, 0))).toMatchObject({ state: "passed", text: "2d 2h 0m" });
  });

  it("a date alone counts calendar days and is Today for the whole day", () => {
    const s = spec("2026-12-24", { units: "days" });
    expect(countdownState(s, local(2026, 11, 20, 23, 59))).toMatchObject({ state: "upcoming", text: "4 days" });
    expect(countdownState(s, local(2026, 11, 23, 0, 1))).toMatchObject({ state: "upcoming", text: "1 day" });
    expect(countdownState(s, local(2026, 11, 24, 0, 0, 1))).toMatchObject({ state: "today", text: "Today" });
    expect(countdownState(s, local(2026, 11, 24, 23, 59))).toMatchObject({ state: "today" });
    expect(countdownState(s, local(2026, 11, 25, 0, 1))).toMatchObject({ state: "passed", text: "1 day" });
  });

  it("whole days to a moment round up", () => {
    const s = spec("2026-12-24 09:00", { units: "days" });
    expect(countdownState(s, local(2026, 11, 23, 10, 0)).text).toBe("1 day");
    expect(countdownState(s, local(2026, 11, 22, 10, 0)).text).toBe("2 days");
    expect(countdownState(s, local(2026, 11, 25, 10, 0)).text).toBe("1 day"); // elapsed time rounds down
  });

  it("repeats yearly: rolls to the next occurrence once passed", () => {
    const s = spec("1990-03-15", { yearly: true, units: "days" });
    expect(countdownState(s, local(2026, 2, 10, 12)).text).toBe("5 days");
    expect(countdownState(s, local(2026, 2, 15, 12)).state).toBe("today");
    const after = countdownState(s, local(2026, 2, 16, 12));
    expect(after.state).toBe("upcoming");
    expect(new Date(after.target).getFullYear()).toBe(2027);
    expect(after.text).toBe("364 days");
  });

  it("yearly with a time rolls over at that time; 29 February falls back to the 28th in other years", () => {
    const s = spec("2000-06-01 08:00", { yearly: true });
    expect(new Date(countdownState(s, local(2026, 5, 1, 7, 0)).target).getFullYear()).toBe(2026);
    expect(new Date(countdownState(s, local(2026, 5, 1, 9, 0)).target).getFullYear()).toBe(2027);
    const leap = spec("2024-02-29", { yearly: true, units: "days" });
    const t = new Date(countdownState(leap, local(2026, 0, 10)).target);
    expect([t.getFullYear(), t.getMonth(), t.getDate()]).toEqual([2026, 1, 28]);
  });
});

describe("msUntilChange (the cadence)", () => {
  it("counting days wakes at most hourly and exactly at the next local midnight", () => {
    const s = spec("2026-12-24", { units: "days" });
    const now = local(2026, 11, 20, 10, 0);
    expect(msUntilChange(s, now)).toBe(3_600_000); // 14 h to midnight: capped at an hour so a changed system clock heals itself
    const wait = msUntilChange(s, local(2026, 11, 20, 23, 40));
    expect(wait).toBeGreaterThan(19 * 60_000);
    expect(wait).toBeLessThan(21 * 60_000); // 20 minutes to midnight: wakes then
  });

  it("is not once a second unless seconds are shown", () => {
    const now = local(2026, 11, 20, 10, 0, 20);
    expect(msUntilChange(spec("2026-12-24 18:00", { units: "minutes" }), now)).toBeGreaterThan(1000);
    expect(msUntilChange(spec("2026-12-24 18:00", { units: "seconds" }), now)).toBeLessThanOrEqual(1100);
    expect(msUntilChange(spec("2026-12-24 18:00", { units: "hours" }), now)).toBeGreaterThan(60_000);
  });

  it("minutes: the next change is when the remaining time crosses a whole minute", () => {
    const now = local(2026, 11, 24, 17, 58, 20); // 1m 40s to go: shows 1m, becomes 0m after 40s
    const wait = msUntilChange(spec("2026-12-24 18:00", { units: "minutes" }), now);
    expect(wait).toBeGreaterThan(39_000);
    expect(wait).toBeLessThan(41_000);
  });

  it("idles (an hour) once passed and not counting up", () => {
    const now = local(2027, 0, 5);
    expect(msUntilChange(spec("2026-12-24 18:00", { after: "done" }), now)).toBe(3_600_000);
    expect(msUntilChange(spec("2026-12-24 18:00", { after: "up", units: "seconds" }), now)).toBeLessThan(1100);
  });
});

afterEach(() => vi.useRealTimers());

describe("across a clock change (Europe/Dublin, clocks go back on 2026-10-25)", () => {
  const prevTz = process.env.TZ;
  const setTz = (tz) => { process.env.TZ = tz; };
  afterEach(() => { if (prevTz === undefined) delete process.env.TZ; else process.env.TZ = prevTz; });

  it("counts calendar days, not 24-hour blocks, for a timed target", () => {
    setTz("Europe/Dublin");
    const now = new Date(2026, 9, 24, 12, 0).getTime();
    expect(new Date(2026, 9, 26, 12, 0).getTime() - now).toBe(49 * 3_600_000); // the extra hour is really there
    const s = countdownState({ target: parseTarget("2026-10-26 12:00"), units: "days", after: "up" }, now);
    expect(s.text).toBe("2 days");
    expect(countdownState({ target: parseTarget("2026-10-26"), units: "days", after: "up" }, now).text).toBe("2 days");
  });

  it("a timed target later today reads Today, and a passed one counts calendar days too", () => {
    setTz("Europe/Dublin");
    const now = new Date(2026, 9, 24, 12, 0).getTime();
    expect(countdownState({ target: parseTarget("2026-10-24 18:00"), units: "days", after: "up" }, now).text).toBe("Today");
    const past = countdownState({ target: parseTarget("2026-10-23 12:00"), units: "days", after: "up" }, new Date(2026, 9, 26, 12, 0).getTime());
    expect(past.text).toBe("3 days");
  });

  it("schedules the next refresh at midnight when counting days", () => {
    const now = new Date(2026, 9, 24, 12, 0).getTime();
    const wait = msUntilChange({ target: parseTarget("2026-10-30 12:00"), units: "days", after: "up" }, now);
    expect(wait).toBeLessThanOrEqual(3_600_000);
    expect(wait).toBeGreaterThan(50);
  });
});

describe("years below 100", () => {
  it("are rejected instead of becoming 19xx", () => {
    const r = parseTarget("0050-01-01");
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/year/i);
    expect(parseTarget("0099-12-31 10:00").ok).toBe(false);
    expect(parseTarget("0100-01-01").ok).toBe(true);
  });
});
