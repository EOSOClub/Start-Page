import { describe, expect, it } from "vitest";
import { autoOrder, autoSize, derivePhoneLayout, firstFit, fitToGrid, phoneOptions, phoneSpotOn, seedPhoneLayout } from "./phone.js";

const w = (id, type, x, y, ww, h, layout_sm = null) => ({ id, type, x, y, w: ww, h, layout_sm });
const OPTS = { cols: 24, colsSm: 4 };

describe("phoneOptions", () => {
  it("defaults to automatic with 4 columns", () => {
    expect(phoneOptions({ settings: {} })).toEqual({ colsSm: 4, custom: false });
    expect(phoneOptions({})).toEqual({ colsSm: 4, custom: false });
  });
  it("reads the settings and ignores garbage", () => {
    expect(phoneOptions({ settings: { columns_sm: 6, phone_layout: "custom" } })).toEqual({ colsSm: 6, custom: true });
    expect(phoneOptions({ settings: { columns_sm: 99 } }).colsSm).toBe(12);
    expect(phoneOptions({ settings: { columns_sm: 0 } }).colsSm).toBe(1);
    expect(phoneOptions({ settings: { columns_sm: "5", phone_layout: "tablet" } })).toEqual({ colsSm: 4, custom: false });
    expect(phoneOptions({ settings: { columns_sm: 2.5 } }).colsSm).toBe(4);
  });
});

describe("autoSize / autoOrder", () => {
  it("gives small widgets half the width and wide ones the full width", () => {
    expect(autoSize(w("a", "link", 0, 0, 4, 1), 24, 4)).toEqual({ w: 2, h: 1 });
    expect(autoSize(w("b", "text", 0, 0, 12, 3), 24, 4)).toEqual({ w: 4, h: 3 });
    expect(autoSize(w("c", "heading", 0, 0, 3, 1), 24, 4).w).toBe(4); // headings are always full width
    expect(autoSize(w("d", "link", 0, 0, 4, 1), 24, 1).w).toBe(1);
    expect(autoSize(w("e", "text", 0, 0, 12, 30), 24, 4).h).toBe(4); // capped like the stack
  });
  it("sorts by y then x and drops dividers and boxes, without touching the input", () => {
    const list = [w("b", "link", 5, 0, 2, 1), w("a", "link", 0, 0, 2, 1), w("d", "divider", 0, 1, 24, 1), w("c", "link", 0, 2, 2, 1)];
    expect(autoOrder(list).map((x) => x.id)).toEqual(["a", "b", "c"]);
    expect(list.map((x) => x.id)).toEqual(["b", "a", "d", "c"]);
  });
});

describe("firstFit", () => {
  it("finds the first free cell, row by row", () => {
    const taken = [{ x: 0, y: 0, w: 2, h: 1 }];
    expect(firstFit(taken, 2, 1, 4)).toEqual({ x: 2, y: 0 });
    expect(firstFit([...taken, { x: 2, y: 0, w: 2, h: 1 }], 2, 1, 4)).toEqual({ x: 0, y: 1 });
    expect(firstFit([], 2, 1, 4, 3)).toEqual({ x: 0, y: 3 });
  });
});

describe("seedPhoneLayout", () => {
  const widgets = [
    w("t", "text", 0, 4, 12, 3), // wide, later
    w("l1", "link", 0, 0, 4, 1),
    w("l2", "link", 4, 0, 4, 1),
    w("l3", "link", 8, 0, 4, 1),
    w("hd", "heading", 0, 2, 24, 1),
    w("dv", "divider", 0, 3, 24, 1),
    w("bx", "box", 0, 0, 10, 8),
  ];
  const seeded = seedPhoneLayout(widgets, OPTS);
  const by = Object.fromEntries(seeded.map((i) => [i.id, i]));

  it("covers every widget exactly once", () => {
    expect(seeded.map((i) => i.id).sort()).toEqual(widgets.map((x) => x.id).sort());
  });
  it("follows the automatic stacking order and packs small widgets side by side", () => {
    expect([by.l1.x, by.l1.y]).toEqual([0, 0]);
    expect([by.l2.x, by.l2.y]).toEqual([2, 0]);
    expect([by.l3.x, by.l3.y]).toEqual([0, 1]);
    expect(by.hd.y).toBeGreaterThanOrEqual(2); // y order: after the links
    expect(by.t.y).toBeGreaterThan(by.hd.y);
    expect(by.hd.w).toBe(4);
  });
  it("seeds dividers and boxes hidden, below everything", () => {
    expect(by.dv.hidden).toBe(true);
    expect(by.bx.hidden).toBe(true);
    const lowest = Math.max(...["l1", "l2", "l3", "hd", "t"].map((id) => by[id].y + by[id].h));
    expect(by.dv.y).toBeGreaterThanOrEqual(lowest);
    expect(by.l1.hidden).toBe(false);
  });
  it("never leaves the grid", () => {
    for (const i of seeded) expect(i.x + i.w).toBeLessThanOrEqual(4);
  });
  it("matches what the unseeded page derives, apart from the hidden extras", () => {
    const derived = derivePhoneLayout(widgets, OPTS);
    for (const d of derived) expect(by[d.id]).toMatchObject({ x: d.x, y: d.y, w: d.w, h: d.h });
  });
});

describe("derivePhoneLayout", () => {
  it("appends widgets without a phone position below the placed ones, in automatic order", () => {
    const list = [
      w("a", "link", 0, 5, 4, 1), // no phone position, later in reading order
      w("b", "link", 0, 0, 4, 1), // no phone position, first in reading order
      w("p", "text", 0, 9, 12, 3, { x: 0, y: 0, w: 4, h: 3 }),
    ];
    const out = Object.fromEntries(derivePhoneLayout(list, OPTS).map((i) => [i.id, i]));
    expect(out.p).toMatchObject({ x: 0, y: 0, w: 4, h: 3, pinned: true });
    expect(out.b).toMatchObject({ x: 0, y: 3, w: 2, h: 1, pinned: false });
    expect(out.a).toMatchObject({ x: 2, y: 3, pinned: false });
  });
  it("leaves hidden widgets out, or includes them for editing", () => {
    const list = [w("a", "link", 0, 0, 4, 1, { x: 0, y: 0, w: 2, h: 1, hidden: true }), w("b", "link", 0, 1, 4, 1, { x: 2, y: 0, w: 2, h: 1 })];
    expect(derivePhoneLayout(list, OPTS).map((i) => i.id)).toEqual(["b"]);
    const all = derivePhoneLayout(list, OPTS, { withHidden: true });
    expect(all.map((i) => [i.id, i.hidden])).toEqual([["a", true], ["b", false]]);
  });
  it("a hidden widget does not hold space for the appended ones", () => {
    const list = [w("a", "link", 0, 0, 4, 1, { x: 0, y: 0, w: 4, h: 6, hidden: true }), w("b", "link", 0, 1, 4, 1)];
    expect(derivePhoneLayout(list, OPTS)[0]).toMatchObject({ id: "b", y: 0 });
  });
  it("shows dividers and boxes only when they have a phone position", () => {
    const list = [w("d", "divider", 0, 0, 24, 1), w("b", "box", 0, 1, 10, 4, { x: 0, y: 0, w: 4, h: 4 }), w("l", "link", 0, 2, 4, 1)];
    expect(derivePhoneLayout(list, OPTS).map((i) => i.id).sort()).toEqual(["b", "l"]);
  });
  it("clamps stored positions into the grid and ignores unusable ones", () => {
    const list = [w("a", "link", 0, 0, 4, 1, { x: 3, y: 1, w: 3, h: 1 }), w("b", "link", 0, 1, 4, 1, { x: "x", y: 0, w: 2, h: 1 }), w("c", "link", 0, 2, 4, 1, { x: 0, y: 0, w: 0, h: 1 })];
    const out = Object.fromEntries(derivePhoneLayout(list, OPTS).map((i) => [i.id, i]));
    expect(out.a).toMatchObject({ x: 1, w: 3 }); // 3 + 3 > 4: pushed left
    expect(out.b.pinned).toBe(false);
    expect(out.c.pinned).toBe(false);
    const narrow = derivePhoneLayout([w("a", "link", 0, 0, 4, 1, { x: 0, y: 0, w: 4, h: 1 })], { cols: 24, colsSm: 2 });
    expect(narrow[0].w).toBe(2);
  });
  it("is empty for an empty page", () => {
    expect(derivePhoneLayout([], OPTS)).toEqual([]);
  });
});

describe("phoneSpotOn", () => {
  const target = (settings, widgets) => ({ columns: 24, settings, widgets });
  it("keeps null as null and travels unchanged to an automatic page", () => {
    expect(phoneSpotOn(null, target({}, []))).toBeNull();
    const l = { x: 1, y: 1, w: 2, h: 2, hidden: false };
    expect(phoneSpotOn(l, target({}, []))).toBe(l);
  });
  it("lands in a free spot on a custom page", () => {
    const placed = w("o", "link", 0, 0, 4, 1, { x: 0, y: 0, w: 4, h: 2 });
    const spot = phoneSpotOn({ x: 0, y: 0, w: 2, h: 2, hidden: true }, target({ phone_layout: "custom" }, [placed]));
    expect(spot).toEqual({ x: 0, y: 2, w: 2, h: 2, hidden: true });
  });
});

describe("decorations without a phone position", () => {
  const list = [w("a", "link", 0, 0, 4, 1, { x: 0, y: 0, w: 4, h: 1 }), w("d", "divider", 0, 2, 24, 1), w("b", "box", 0, 4, 12, 3)];
  it("are offered as hidden, unpinned items in edit (withHidden) and never shown otherwise", () => {
    const all = Object.fromEntries(derivePhoneLayout(list, OPTS, { withHidden: true }).map((i) => [i.id, i]));
    expect(all.d).toMatchObject({ hidden: true, pinned: false });
    expect(all.b).toMatchObject({ hidden: true, pinned: false });
    expect(all.d.x + all.d.w).toBeLessThanOrEqual(OPTS.colsSm);
    expect(all.d.y).toBeGreaterThanOrEqual(1); // below the placed widget
    expect(derivePhoneLayout(list, OPTS).map((i) => i.id)).toEqual(["a"]);
  });
  it("a decoration the user placed is an ordinary pinned item", () => {
    const placed = [...list.slice(0, 1), w("d", "divider", 0, 2, 24, 1, { x: 0, y: 5, w: 4, h: 1 })];
    expect(derivePhoneLayout(placed, OPTS).map((i) => i.id).sort()).toEqual(["a", "d"]);
  });
});

describe("fitToGrid", () => {
  it("clamps positions to the grid and drops unusable ones", () => {
    expect(fitToGrid([{ id: "a", x: 2, y: 1, w: 4, h: 2, hidden: true }, { id: "b", x: 0, y: 0, w: 0, h: 1 }, { id: "c" }], 2)).toEqual([
      { id: "a", x: 0, y: 1, w: 2, h: 2, hidden: true },
    ]);
    expect(fitToGrid(null, 4)).toEqual([]);
  });
});
