import { describe, it, expect } from "vitest";
import { ARC, arcPath, gaugeModel, gaugeText, numericValue, polar } from "./gauge.js";

describe("gaugeModel", () => {
  it("maps the value onto 0-1 between min and max and clamps", () => {
    expect(gaugeModel({ value: 50 }).fraction).toBe(0.5);
    expect(gaugeModel({ value: 150 }).fraction).toBe(1);
    expect(gaugeModel({ value: -5 }).fraction).toBe(0);
    expect(gaugeModel({ value: 5, min: 0, max: 20 }).fraction).toBe(0.25);
    expect(gaugeModel({ value: 15, min: 10, max: 20 }).fraction).toBe(0.5);
    expect(gaugeModel({ value: "75" }).fraction).toBe(0.75);
  });

  it("treats a missing, text or boolean value as no data", () => {
    for (const value of [null, undefined, "n/a", "3/4", true, NaN, ""]) {
      const m = gaugeModel({ value, warn: 10 });
      expect(m).toMatchObject({ value: null, fraction: 0, tone: null });
    }
  });

  it("falls back to 0-100 and survives max <= min", () => {
    expect(gaugeModel({ value: 10, min: null, max: "" })).toMatchObject({ min: 0, max: 100 });
    const m = gaugeModel({ value: 5, min: 10, max: 10 });
    expect(m.max).toBeGreaterThan(m.min);
    expect(Number.isFinite(m.fraction)).toBe(true);
  });

  it("tones: none without thresholds, ok / warn / bad when higher is worse", () => {
    expect(gaugeModel({ value: 99 }).tone).toBeNull();
    const t = (value) => gaugeModel({ value, warn: 70, bad: 90 }).tone;
    expect([t(10), t(70), t(89.9), t(90), t(100)]).toEqual(["ok", "warn", "warn", "bad", "bad"]);
    expect(gaugeModel({ value: 80, warn: 70 }).tone).toBe("warn"); // one threshold is enough
    expect(gaugeModel({ value: 95, bad: 90 }).tone).toBe("bad");
    expect(gaugeModel({ value: 50, bad: 90 }).tone).toBe("ok");
  });

  it("tones when lower is worse (bad below warn)", () => {
    const t = (value) => gaugeModel({ value, warn: 20, bad: 10 }).tone;
    expect([t(50), t(20), t(15), t(10), t(3)]).toEqual(["ok", "warn", "warn", "bad", "bad"]);
  });
});

describe("helpers", () => {
  it("numericValue", () => {
    expect([numericValue(3), numericValue(" 4.5 "), numericValue("-2"), numericValue("1e3"), numericValue(Infinity), numericValue(null)]).toEqual([3, 4.5, -2, null, null, null]);
  });

  it("polar measures clockwise from 12 o'clock", () => {
    expect(polar(50, 50, 10, 0)).toEqual([50, 40]);
    expect(polar(50, 50, 10, 90)).toEqual([60, 50]);
    expect(polar(50, 50, 10, 180)).toEqual([50, 60]);
  });

  it("arcPath draws the 240 degree dial from lower left to lower right through the top", () => {
    const d = arcPath(ARC);
    expect(d).toMatch(/^M [\d.]+ [\d.]+ A 40 40 0 1 1 [\d.]+ [\d.]+$/); // large-arc flag set, sweep clockwise
    const nums = d.match(/-?\d+(\.\d+)?/g).map(Number);
    expect(nums.every(Number.isFinite)).toBe(true);
    expect(nums[0]).toBeCloseTo(50 - 40 * Math.sin(Math.PI / 3), 2); // start x, left of centre
    expect(nums[nums.length - 2]).toBeCloseTo(50 + 40 * Math.sin(Math.PI / 3), 2); // end x, right of centre
  });

  it("gaugeText joins the unit and says n/a when there is no number", () => {
    const fmt = (v) => String(v);
    expect(gaugeText(42, "%", fmt)).toBe("42 %");
    expect(gaugeText(7, undefined, fmt)).toBe("7");
    expect(gaugeText("online", "%", fmt)).toBe("n/a");
    expect(gaugeText(null, null, fmt)).toBe("n/a");
  });
});
