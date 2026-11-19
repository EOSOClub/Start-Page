import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { CalendarWidget } from "./calendarWidget.jsx";
import { CountdownWidget } from "./countdownWidget.jsx";
import { GaugeWidget } from "./gaugeWidget.jsx";
import { WIDGET_TYPES, defaultConfig } from "./index.jsx";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const widget = (type, config) => ({ id: "w", type, config: { ...defaultConfig(type), ...config } });

describe("calendar widget", () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date("2026-11-18T10:00:00Z"), toFake: ["Date", "setTimeout", "clearTimeout"] }));
  const feed = (id, name) => ({ id, name, type: "ics", config: {} });
  const data = (events, timezone = "UTC", extra = {}) => ({ ok: true, error: null, data: { timezone, events, stats: [], items: [] }, ...extra });
  const ev = (title, start, end, x = {}) => ({ title, start, end, all_day: false, location: "", description: "", ...x });
  const show = (config, integrations, integrationData) =>
    render(<CalendarWidget.Render widget={widget("calendar", config)} integrations={integrations} integrationData={integrationData} />);

  it("is a multi-select ICS widget with days, limit and location options", () => {
    expect(WIDGET_TYPES.calendar.fields[0]).toMatchObject({ key: "integration_ids", type: "integration", integrationType: "ics", multiple: true });
    expect(defaultConfig("calendar")).toEqual({ days: 7, limit: 10, show_location: true });
  });

  it("groups events by day with times, All day, locations and an in-progress marker", () => {
    const { container } = show(
      { integration_ids: ["a"] },
      [feed("a", "Family")],
      {
        a: data([
          ev("Standup", "2026-11-18T09:30:00+00:00", "2026-11-18T10:30:00+00:00", { location: "Room 4" }),
          ev("Dentist", "2026-11-19T15:00:00+00:00", "2026-11-19T16:00:00+00:00"),
          ev("Holiday", "2026-11-20T00:00:00+00:00", "2026-11-21T00:00:00+00:00", { all_day: true }),
        ]),
      }
    );
    expect(screen.getByText("Family")).toBeTruthy();
    expect([...container.querySelectorAll(".cal-day-head")].map((e) => e.textContent)).toEqual(["Today", "Tomorrow", expect.stringMatching(/Friday/)]);
    expect(screen.getByText("Room 4")).toBeTruthy();
    expect(screen.getByText("All day")).toBeTruthy();
    const live = container.querySelectorAll(".cal-live");
    expect(live.length).toBe(1);
    expect(live[0].textContent).toContain("Standup");
    expect(live[0].querySelector("[aria-label='In progress']")).toBeTruthy();
  });

  it("merges several feeds and marks one that is down", () => {
    show(
      { integration_ids: ["a", "b"] },
      [feed("a", "Family"), feed("b", "Work")],
      {
        a: data([ev("Lunch", "2026-11-18T12:00:00+00:00", "2026-11-18T13:00:00+00:00")]),
        b: { ok: false, error: "timeout", data: null },
      }
    );
    expect(screen.getByText("Lunch")).toBeTruthy();
    expect(screen.getByText("1 feed unavailable")).toBeTruthy();
  });

  it("states: no feed selected, waiting, error, empty", () => {
    show({}, [], {});
    expect(screen.getByText("Select an ICS integration in widget settings.")).toBeTruthy();
    cleanup();
    show({ integration_ids: ["a"] }, [feed("a", "F")], {});
    expect(screen.getByText("Waiting for first fetch…")).toBeTruthy();
    cleanup();
    show({ integration_ids: ["a"] }, [feed("a", "F")], { a: { ok: false, error: "HTTP 404 Not Found (h:80)", data: null } });
    expect(screen.getByText("Error: HTTP 404 Not Found (h:80)")).toBeTruthy();
    cleanup();
    show({ integration_ids: ["a"], days: 3 }, [feed("a", "F")], { a: data([]) });
    expect(screen.getByText("No events in the next 3 days.")).toBeTruthy();
  });

  it("still reads the single-feed key of an older config", () => {
    show({ integration_id: "a" }, [feed("a", "Old")], { a: data([ev("Thing", "2026-11-18T12:00:00+00:00", "2026-11-18T13:00:00+00:00")]) });
    expect(screen.getByText("Thing")).toBeTruthy();
  });
});

describe("countdown widget", () => {
  const show = (config) => render(<CountdownWidget.Render widget={widget("countdown", config)} />);
  const at = (...a) => new Date(...a);

  it("is a no-integration Basics widget", () => {
    expect(WIDGET_TYPES.countdown.group).toBe("Basics");
    expect(WIDGET_TYPES.countdown.fields.some((f) => f.type === "integration")).toBe(false);
    expect(WIDGET_TYPES.countdown.fields.map((f) => f.key)).toEqual(["label", "target", "yearly", "units", "after"]);
  });

  it("asks for a date, and explains an invalid one", () => {
    show({});
    expect(screen.getByText("Set a date in widget settings.")).toBeTruthy();
    cleanup();
    show({ target: "2026-02-30" });
    expect(screen.getByText("Countdown: That date does not exist")).toBeTruthy();
    cleanup();
    show({ target: "soon" });
    expect(screen.getByText(/Use YYYY-MM-DD/)).toBeTruthy();
  });

  it("counts down in days and then flips to Today", () => {
    vi.useFakeTimers({ now: at(2026, 11, 20, 12, 0), toFake: ["Date", "setTimeout", "clearTimeout"] });
    show({ target: "2026-12-24", label: "Christmas" });
    expect(screen.getByText("Christmas")).toBeTruthy();
    expect(screen.getByRole("timer").textContent).toBe("4 days");
  });

  it("re-renders on schedule, not every second, when counting days", () => {
    vi.useFakeTimers({ now: at(2026, 11, 20, 23, 50), toFake: ["Date", "setTimeout", "clearTimeout"] });
    const spy = vi.spyOn(globalThis, "setTimeout");
    show({ target: "2026-12-24" });
    const delays = spy.mock.calls.map((c) => c[1]).filter((d) => typeof d === "number" && d > 100);
    expect(delays.length).toBeGreaterThan(0);
    expect(Math.max(...delays)).toBeGreaterThan(5 * 60_000); // ~10 minutes to midnight, not 1000
    expect(screen.getByRole("timer").textContent).toBe("4 days");
    act(() => vi.advanceTimersByTime(11 * 60_000)); // past midnight
    expect(screen.getByRole("timer").textContent).toBe("3 days");
    spy.mockRestore();
  });

  it("after the target: counts up, says Done, or hides", () => {
    vi.useFakeTimers({ now: at(2027, 0, 2, 12, 0), toFake: ["Date", "setTimeout", "clearTimeout"] });
    const up = show({ target: "2026-12-30", after: "up" });
    expect(screen.getByRole("timer").textContent).toBe("3 days ago");
    up.unmount();
    const done = show({ target: "2026-12-30", after: "done" });
    expect(screen.getByRole("timer").textContent).toBe("Done");
    done.unmount();
    const hidden = show({ target: "2026-12-30", after: "hide" });
    expect(hidden.container.textContent).toBe("");
  });

  it("yearly never finishes: it counts to the next birthday", () => {
    vi.useFakeTimers({ now: at(2026, 5, 1, 12, 0), toFake: ["Date", "setTimeout", "clearTimeout"] });
    show({ target: "1990-03-15", yearly: true, after: "hide" });
    expect(screen.getByRole("timer").textContent).toBe("287 days");
  });

  it("stops ticking while the tab is hidden and catches up when it returns", () => {
    vi.useFakeTimers({ now: at(2026, 11, 24, 17, 55, 30), toFake: ["Date", "setTimeout", "clearTimeout"] });
    let state = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => state);
    show({ target: "2026-12-24 18:00", units: "minutes" });
    expect(screen.getByRole("timer").textContent).toBe("4m");
    state = "hidden";
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(vi.getTimerCount()).toBe(0); // nothing scheduled in a hidden tab
    act(() => vi.advanceTimersByTime(3 * 60_000));
    expect(screen.getByRole("timer").textContent).toBe("4m"); // untouched while hidden
    state = "visible";
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(screen.getByRole("timer").textContent).toBe("1m");
    vi.restoreAllMocks();
  });
});

describe("gauge widget", () => {
  const INTEG = [{ id: "i1", name: "NAS", type: "host", config: {} }];
  const stats = [
    { key: "cpu", label: "CPU", value: 80, unit: "%", series: [10, 40, 80] },
    { key: "state", label: "State", value: "ok" },
    { key: "free", label: "Free", value: 15, unit: "GB" },
  ];
  const ok = (s = stats) => ({ i1: { ok: true, error: null, data: { stats: s } } });
  const show = (config, data = ok()) => render(<GaugeWidget.Render widget={widget("gauge", { integration_id: "i1", ...config })} integrations={INTEG} integrationData={data} />);

  it("is registered for any integration that provides stats", () => {
    expect(WIDGET_TYPES.gauge.fields[0]).toMatchObject({ key: "integration_id", type: "integration", capability: "stats" });
    expect(defaultConfig("gauge")).toEqual({ style: "arc", min: 0, max: 100, show_sparkline: true });
  });

  it("arc: a meter with aria values, text and a tone from the thresholds", () => {
    const { container } = show({ stat_key: "cpu", warn: 70, bad: 90 });
    const meter = screen.getByRole("meter", { name: "CPU" });
    expect(meter.getAttribute("aria-valuemin")).toBe("0");
    expect(meter.getAttribute("aria-valuemax")).toBe("100");
    expect(meter.getAttribute("aria-valuenow")).toBe("80");
    expect(meter.getAttribute("aria-valuetext")).toBe("80 %");
    expect(container.querySelector("svg.gauge-svg").getAttribute("aria-hidden")).toBe("true");
    const fill = container.querySelector("path.gauge-fill");
    expect(fill.getAttribute("class")).toContain("gauge-fill-warn");
    expect(fill.getAttribute("stroke-dasharray")).toBe("80.00 100");
    expect(container.querySelector("svg.spark")).toBeTruthy(); // the stat has a series
    expect(container.querySelector("text.gauge-value").textContent).toBe("80 %");
  });

  it("bar and number styles are meters too; a custom range and title apply", () => {
    const bar = show({ stat_key: "cpu", style: "bar", min: 0, max: 200, title: "Processor", show_sparkline: false });
    const meter = screen.getByRole("meter", { name: "Processor" });
    expect(meter.getAttribute("aria-valuemax")).toBe("200");
    expect(bar.container.querySelector(".gauge-bar i").style.width).toBe("40%");
    expect(bar.container.querySelector("svg.spark")).toBeNull();
    bar.unmount();
    show({ stat_key: "cpu", style: "number" });
    expect(screen.getByRole("meter", { name: "CPU" }).textContent).toContain("80 %");
  });

  it("low-is-bad thresholds, and no aria-valuenow for a non-numeric stat", () => {
    const low = show({ stat_key: "free", unit: "GB", warn: 20, bad: 10 });
    expect(low.container.querySelector("path.gauge-fill").getAttribute("class")).toContain("gauge-fill-warn");
    low.unmount();
    show({ stat_key: "state" });
    const meter = screen.getByRole("meter", { name: "State" });
    expect(meter.hasAttribute("aria-valuenow")).toBe(false);
    expect(meter.getAttribute("aria-valuetext")).toBe("n/a");
  });

  it("states: no stat chosen, unknown stat, no integration, error", () => {
    show({});
    expect(screen.getByText("Pick a stat in widget settings.")).toBeTruthy();
    cleanup();
    show({ stat_key: "nope" });
    expect(screen.getByText("NAS has no stat “nope”.")).toBeTruthy();
    cleanup();
    show({ stat_key: "cpu", integration_id: null }, {});
    expect(screen.getByText(/Select a stats integration/)).toBeTruthy();
    cleanup();
    show({ stat_key: "cpu" }, { i1: { ok: false, error: "timeout (h:1)", data: null } });
    expect(screen.getByText("Error: timeout (h:1)")).toBeTruthy();
  });
});
