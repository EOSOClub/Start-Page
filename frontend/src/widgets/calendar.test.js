import { describe, it, expect } from "vitest";
import { addDays, dayKeyOf, dayLabel, groupAgenda, validZone } from "./calendar.js";

const ev = (title, start, end, extra = {}) => ({ title, start, end, all_day: false, location: "", description: "", ...extra });
const agenda = (events, now, opts = {}) => groupAgenda(events, { now: new Date(now), tz: "UTC", locale: "en-GB", ...opts });
const titles = (groups) => groups.map((g) => [g.label, g.events.map((e) => e.title)]);

describe("day keys", () => {
  it("uses the calendar day in the zone, not UTC", () => {
    const t = new Date("2026-11-18T23:30:00Z");
    expect(dayKeyOf(t, "UTC")).toBe("2026-11-18");
    expect(dayKeyOf(t, "Asia/Tokyo")).toBe("2026-11-19");
    expect(dayKeyOf(t, "America/Los_Angeles")).toBe("2026-11-18");
  });

  it("adds calendar days across month ends and leap days", () => {
    expect(addDays("2026-11-30", 1)).toBe("2026-12-01");
    expect(addDays("2028-02-28", 1)).toBe("2028-02-29");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });

  it("falls back to the browser zone for an unknown zone name", () => {
    expect(validZone("Mars/Olympus")).toBeUndefined();
    expect(validZone("Europe/London")).toBe("Europe/London");
    expect(() => dayKeyOf(new Date(), "Mars/Olympus")).not.toThrow();
  });

  it("labels Today, Tomorrow, then weekday and date", () => {
    expect(dayLabel("2026-11-18", "2026-11-18", "en-US")).toBe("Today");
    expect(dayLabel("2026-11-19", "2026-11-18", "en-US")).toBe("Tomorrow");
    expect(dayLabel("2026-11-20", "2026-11-18", "en-US")).toBe("Friday, Nov 20");
  });
});

describe("groupAgenda", () => {
  const NOW = "2026-11-18T10:00:00Z";

  it("groups by day, sorts by time, marks the event in progress and drops finished ones", () => {
    const groups = agenda(
      [
        ev("Later", "2026-11-18T14:00:00+00:00", "2026-11-18T15:00:00+00:00", { location: "Room 2" }),
        ev("Tomorrow thing", "2026-11-19T09:00:00+00:00", "2026-11-19T09:30:00+00:00"),
        ev("Done", "2026-11-18T08:00:00+00:00", "2026-11-18T09:00:00+00:00"),
        ev("Now", "2026-11-18T09:30:00+00:00", "2026-11-18T10:30:00+00:00"),
      ],
      NOW
    );
    expect(titles(groups)).toEqual([["Today", ["Now", "Later"]], ["Tomorrow", ["Tomorrow thing"]]]);
    const [now, later] = groups[0].events;
    expect(now).toMatchObject({ time: "9:30", inProgress: true, allDay: false });
    expect(later).toMatchObject({ time: "14:00", inProgress: false, location: "Room 2" });
  });

  it("lists all-day events first and spreads a multi-day one over each day (end is exclusive)", () => {
    const groups = agenda(
      [
        ev("Meeting", "2026-11-19T10:00:00+00:00", "2026-11-19T11:00:00+00:00"),
        ev("Trip", "2026-11-19T00:00:00+00:00", "2026-11-22T00:00:00+00:00", { all_day: true }),
      ],
      NOW,
      { days: 7 }
    );
    expect(groups.map((g) => g.key)).toEqual(["2026-11-19", "2026-11-20", "2026-11-21"]); // not the 22nd
    expect(groups[0].events.map((e) => e.title)).toEqual(["Trip", "Meeting"]);
    expect(groups.map((g) => g.events[0].part)).toEqual([{ index: 1, count: 3 }, { index: 2, count: 3 }, { index: 3, count: 3 }]);
    expect(groups[1].events[0]).toMatchObject({ time: "All day", allDay: true, inProgress: false });
  });

  it("a single all-day event covers one day, today included", () => {
    const groups = agenda([ev("Holiday", "2026-11-18T00:00:00+00:00", "2026-11-19T00:00:00+00:00", { all_day: true })], NOW);
    expect(titles(groups)).toEqual([["Today", ["Holiday"]]]);
    expect(groups[0].events[0].part).toBeUndefined();
  });

  it("an event crossing midnight shows on both days: start time, then Until", () => {
    const groups = agenda([ev("Party", "2026-11-18T22:00:00+00:00", "2026-11-19T02:00:00+00:00")], NOW);
    expect(titles(groups)).toEqual([["Today", ["Party"]], ["Tomorrow", ["Party"]]]);
    expect(groups.map((g) => g.events[0].time)).toEqual(["22:00", "Until 2:00"]);
  });

  it("an event that ends exactly at midnight stays on its own day", () => {
    const groups = agenda([ev("Late", "2026-11-18T20:00:00+00:00", "2026-11-19T00:00:00+00:00")], NOW);
    expect(titles(groups)).toEqual([["Today", ["Late"]]]);
  });

  it("an event started yesterday and still running is listed first today as in progress", () => {
    const groups = agenda([ev("Overnight", "2026-11-17T23:00:00+00:00", "2026-11-18T12:00:00+00:00"), ev("Early", "2026-11-18T10:30:00+00:00", "2026-11-18T11:00:00+00:00")], NOW);
    expect(groups[0].events.map((e) => [e.title, e.time, e.inProgress])).toEqual([["Overnight", "Until 12:00", true], ["Early", "10:30", false]]);
  });

  it("respects days and limit, and omits empty days", () => {
    const events = [
      ev("A", "2026-11-18T12:00:00+00:00", "2026-11-18T13:00:00+00:00"),
      ev("B", "2026-11-18T13:00:00+00:00", "2026-11-18T14:00:00+00:00"),
      ev("C", "2026-11-20T13:00:00+00:00", "2026-11-20T14:00:00+00:00"),
    ];
    expect(titles(agenda(events, NOW, { days: 1 }))).toEqual([["Today", ["A", "B"]]]);
    expect(titles(agenda(events, NOW, { days: 7, limit: 2 }))).toEqual([["Today", ["A", "B"]]]);
    expect(titles(agenda(events, NOW, { days: 7, limit: 3 })).map(([l]) => l)).toEqual(["Today", "Friday 20 Nov"]);
    expect(agenda([], NOW)).toEqual([]);
  });

  it("groups in the display zone", () => {
    // 23:30 UTC on the 18th is already the 19th in Tokyo
    const e = ev("Tokyo", "2026-11-18T23:30:00+00:00", "2026-11-19T00:30:00+00:00");
    const groups = groupAgenda([e], { now: new Date("2026-11-18T10:00:00Z"), tz: "Asia/Tokyo", locale: "en-GB" });
    expect(titles(groups)).toEqual([["Tomorrow", ["Tokyo"]]]); // it is 19:00 on the 18th in Tokyo
    expect(groups[0].events[0].time).toBe("8:30");
  });

  it("skips events with an unreadable start", () => {
    expect(agenda([ev("Bad", "nonsense", "nonsense")], NOW)).toEqual([]);
  });
});

describe("groupAgenda on DST days", () => {
  const NY = { tz: "America/New_York", locale: "en-US" };

  it("spring forward: a 23 hour day still ends at its own midnight", () => {
    // 2026-03-08 has 23 hours in New York. 00:30 on the 9th is 23h20m after now, yet it is Tomorrow.
    const groups = groupAgenda(
      [ev("Late", "2026-03-08T23:30:00-04:00", "2026-03-09T00:00:00-04:00"), ev("After midnight", "2026-03-09T00:30:00-04:00", "2026-03-09T01:00:00-04:00")],
      { now: new Date("2026-03-08T00:10:00-05:00"), ...NY }
    );
    expect(titles(groups)).toEqual([["Today", ["Late"]], ["Tomorrow", ["After midnight"]]]);
    expect(groups[0].events[0].time).toBe("11:30 PM");
  });

  it("an all-day event on the DST day is one day", () => {
    const groups = groupAgenda([ev("Clocks", "2026-03-08T00:00:00-05:00", "2026-03-09T00:00:00-04:00", { all_day: true })], { now: new Date("2026-03-08T12:00:00-04:00"), ...NY });
    expect(titles(groups)).toEqual([["Today", ["Clocks"]]]);
  });

  it("fall back: a 25 hour day keeps both 1:30 AM events on that day, in order", () => {
    const groups = groupAgenda(
      [ev("Second 1:30", "2026-11-01T01:30:00-05:00", "2026-11-01T02:00:00-05:00"), ev("First 1:30", "2026-11-01T01:30:00-04:00", "2026-11-01T02:00:00-04:00"), ev("Next day", "2026-11-02T00:00:00-05:00", "2026-11-02T01:00:00-05:00")],
      { now: new Date("2026-11-01T00:30:00-04:00"), ...NY }
    );
    expect(titles(groups)).toEqual([["Today", ["First 1:30", "Second 1:30"]], ["Tomorrow", ["Next day"]]]);
  });
});

describe("groupAgenda with odd feed data", () => {
  const NOW = "2026-11-19T10:00:00Z";

  it("does not throw on an all-day event with an unparsable end or start, and treats it as one day", () => {
    const events = [
      ev("Bad end", "2026-11-19", "garbage!!", { all_day: true }),
      ev("Impossible end", "2026-11-19", "2026-02-31", { all_day: true }),
      ev("Odd start", "2026-11-20T00:00:00Z", "2026-11-21", { all_day: true }),
    ];
    let groups;
    expect(() => { groups = agenda(events, NOW); }).not.toThrow();
    expect(groups[0].events.map((e) => e.title).sort()).toEqual(["Bad end", "Impossible end"]);
  });

  it("copes with a non-string title or location", () => {
    const events = [
      ev({ evil: 1 }, "2026-11-19T12:00:00Z", "2026-11-19T13:00:00Z", { location: { x: 1 } }),
      ev(42, "2026-11-19T14:00:00Z", "2026-11-19T15:00:00Z"),
      ev(null, "2026-11-19T16:00:00Z", "2026-11-19T17:00:00Z"),
    ];
    let groups;
    expect(() => { groups = agenda(events, NOW); }).not.toThrow();
    const rows = groups[0].events;
    expect(rows.map((r) => r.title)).toEqual(["(no title)", "42", "(no title)"]);
    expect(typeof rows[0].location).toBe("string");
  });

  it("marks only today's row of an ongoing multi-day event as in progress", () => {
    const groups = agenda([ev("Conference", "2026-11-18T09:00:00Z", "2026-11-21T17:00:00Z")], NOW, { days: 4 });
    expect(groups.map((g) => [g.key, g.events[0].inProgress])).toEqual([
      ["2026-11-19", true],
      ["2026-11-20", false],
      ["2026-11-21", false],
    ]);
  });
});
