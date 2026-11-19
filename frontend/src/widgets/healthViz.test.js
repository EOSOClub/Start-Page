import { describe, it, expect } from "vitest";
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

const b = (up, total, latency_ms = null) => ({ t: 0, up, total, latency_ms });

describe("formatUptime", () => {
  it("formats to one decimal and only rounds a perfect record up to 100%", () => {
    expect(formatUptime(99.8)).toBe("99.8%");
    expect(formatUptime(99.96)).toBe("99.9%");
    expect(formatUptime(100)).toBe("100%");
    expect(formatUptime(0)).toBe("0.0%");
    expect(formatUptime(null)).toBe("");
  });
});

describe("beatClass", () => {
  it("maps buckets to heartbeat classes", () => {
    expect(beatClass(b(0, 0))).toBe("");
    expect(beatClass(b(3, 3))).toBe("1");
    expect(beatClass(b(0, 2))).toBe("0");
    expect(beatClass(b(1, 2))).toBe("2");
    expect(beatClass(undefined)).toBe("");
  });
});

describe("sparklineModel", () => {
  it("breaks the line at gaps and offline buckets and marks failures", () => {
    const m = sparklineModel([b(1, 1, 10), b(1, 1, 20), b(0, 0), b(0, 1), b(1, 1, 5)], 100, 24);
    expect(m.segments.map((s) => s.length)).toEqual([2, 1]);
    expect(m.marks).toEqual([70]); // only the offline bucket (index 3 of 5)
    expect(m.segments[0][0][0]).toBe(10);
    // The slowest bucket is highest (smallest y); a faster one sits lower.
    expect(m.segments[0][1][1]).toBeLessThan(m.segments[0][0][1]);
  });

  it("copes with empty input and all-gap input", () => {
    expect(sparklineModel([])).toEqual({ segments: [], marks: [], max: 0 });
    expect(sparklineModel([b(0, 0), b(0, 0)]).segments).toEqual([]);
  });
});

describe("barCapacity / visibleBars", () => {
  it("counts the 4px bars with 2px gaps that fit", () => {
    expect(barCapacity(0)).toBe(0);
    expect(barCapacity(NaN)).toBe(0);
    expect(barCapacity(3)).toBe(0);
    expect(barCapacity(4)).toBe(1);
    expect(barCapacity(10)).toBe(2); // 4 + 2 + 4
    expect(barCapacity(9)).toBe(1);
    expect(barCapacity(286)).toBe(48);
  });

  it("keeps the newest buckets and drops the oldest", () => {
    const list = [1, 2, 3, 4, 5].map((t) => ({ t }));
    expect(visibleBars(list, 2).map((x) => x.t)).toEqual([4, 5]);
    expect(visibleBars(list, 99)).toBe(list);
    expect(visibleBars(list, 0)).toEqual([]);
    expect(visibleBars(list, null)).toBe(list); // not measured yet: show everything
    expect(visibleBars(undefined, 3)).toEqual([]);
  });
});

describe("accessible summaries", () => {
  it("describes the uptime strip", () => {
    expect(uptimeSummary("Nas", undefined)).toBe("Nas: no uptime data");
    expect(uptimeSummary("Nas", { checks: 10, uptime_pct: 99.9, buckets: [b(1, 1), b(0, 1), b(1, 1)] })).toBe(
      "Nas: 99.9% uptime over 10 checks, 1 of the last 3 periods had failed checks"
    );
    expect(uptimeSummary("Nas", { checks: 2, uptime_pct: 100, buckets: [b(1, 1)] })).toContain("no failed checks");
  });

  it("describes the sparkline", () => {
    expect(sparklineSummary([])).toBe("Latency: no data");
    expect(sparklineSummary([b(1, 1, 10.4), b(0, 1), b(1, 1, 30)])).toBe("Latency 10 to 30 ms over 3 periods, 1 with failed checks");
  });
});

describe("relativeTime", () => {
  it("buckets elapsed time", () => {
    expect(relativeTime(1000, 1030)).toBe("just now");
    expect(relativeTime(1000, 1000 + 5 * 60)).toBe("5m ago");
    expect(relativeTime(1000, 1000 + 3 * 3600)).toBe("3h ago");
    expect(relativeTime(1000, 1000 + 2 * 86400)).toBe("2d ago");
  });
});
