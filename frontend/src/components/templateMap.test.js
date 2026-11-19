import { describe, it, expect } from "vitest";
import { exportFileName, miniMap, withoutWarnings } from "./templateMap.js";

describe("miniMap", () => {
  const t = { columns: 24, rows: 8, layout: [{ type: "heading", x: 0, y: 0, w: 24, h: 1 }, { type: "clock", x: 4, y: 1, w: 8, h: 4 }] };
  it("draws one rectangle per widget inside the viewBox", () => {
    const m = miniMap(t, 0.1);
    expect(m.viewBox).toBe("0 0 24 8");
    expect(m.rects).toHaveLength(2);
    expect(m.rects[1]).toMatchObject({ x: 4.1, y: 1.1, kind: "widget" });
    expect(m.rects[1].width).toBeCloseTo(7.8);
    expect(m.rects[1].height).toBeCloseTo(3.8);
    expect(m.rects[0].kind).toBe("deco");
    for (const r of m.rects) expect(r.x + r.width).toBeLessThanOrEqual(24);
  });
  it("derives the row count when missing and survives bad input", () => {
    expect(miniMap({ columns: 12, layout: [{ type: "x", x: 0, y: 2, w: 3, h: 5 }] }).viewBox).toBe("0 0 12 7");
    expect(miniMap(null).rects).toEqual([]);
    expect(miniMap({}).viewBox).toBe("0 0 24 1");
  });
});

describe("export helpers", () => {
  it("names the file after the page, safely", () => {
    expect(exportFileName("Media")).toBe("Media.startpage-page.json");
    expect(exportFileName('a/b:c*"d')).toBe("a-b-c-d.startpage-page.json");
    expect(exportFileName("")).toBe("page.startpage-page.json");
    expect(exportFileName("..hidden")).toBe("hidden.startpage-page.json");
  });
  it("drops warnings from the file", () => {
    expect(withoutWarnings({ a: 1, warnings: ["x"] })).toEqual({ a: 1 });
  });
});
