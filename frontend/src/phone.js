/*
 * Phone layouts, as pure functions (no React, no DOM), so Canvas, App and the tests share them.
 *
 * A page's phone layout is optional. Page settings: `phone_layout` ("auto" = widgets stack
 * automatically, the default; "custom" = use each widget's `layout_sm`) and `columns_sm` (the width
 * of the phone grid, 1-12, default 4). A widget's `layout_sm` is {x, y, w, h, hidden?} in cells of
 * that grid; the rows are as tall as the desktop rows. The server stores null for "no phone position".
 */

/** Below this canvas width the page is shown as a phone. */
export const NARROW_PX = 720;
/** Width of the device frame in edit mode's Phone view. */
export const PHONE_PX = 390;
export const COLS_SM_DEFAULT = 4;
export const COLS_SM_MAX = 12;
/** Tallest a widget gets in the automatic stack / a derived default size (rows). */
export const STACK_MAX_ROWS = 4;

export const isDecor = (w) => w.type === "divider" || w.type === "box";

const num = (v) => typeof v === "number" && Number.isFinite(v);
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

/** The one read path for the two phone page settings (untyped server data: typeof checks only). */
export function phoneOptions(dashboard) {
  const s = dashboard?.settings || {};
  const n = s.columns_sm;
  return {
    colsSm: Number.isInteger(n) ? clamp(n, 1, COLS_SM_MAX) : COLS_SM_DEFAULT,
    custom: s.phone_layout === "custom",
  };
}

/** The automatic stacking order: reading order, without dividers and boxes. */
export const autoOrder = (widgets) => widgets.filter((w) => !isDecor(w)).sort((a, b) => a.y - b.y || a.x - b.x);

/** Default phone size of a widget: full width for wide ones, half for small ones (the automatic stack's rule). */
export function autoSize(widget, cols, colsSm) {
  const half = widget.w / cols <= 0.2 && widget.type !== "heading";
  return {
    w: half ? Math.max(1, Math.floor(colsSm / 2)) : colsSm,
    h: clamp(Math.round(widget.h) || 1, 1, STACK_MAX_ROWS),
  };
}

const overlaps = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

/** First top-left cell at or below `fromY` where a w x h box overlaps none of `taken`. */
export function firstFit(taken, w, h, cols, fromY = 0) {
  for (let y = fromY; y < fromY + 500; y++)
    for (let x = 0; x + w <= cols; x++) if (!taken.some((t) => overlaps({ x, y, w, h }, t))) return { x, y };
  return { x: 0, y: fromY };
}

const bottom = (list) => list.reduce((m, t) => Math.max(m, t.y + t.h), 0);

/** A stored layout_sm made safe for a grid of `colsSm` columns, or null when it is not usable. */
function usable(l, colsSm) {
  if (!l || !num(l.x) || !num(l.y) || !num(l.w) || !num(l.h) || l.w <= 0 || l.h <= 0) return null;
  const w = Math.min(l.w, colsSm);
  return { x: clamp(l.x, 0, colsSm - w), y: Math.max(0, l.y), w, h: l.h, hidden: l.hidden === true };
}

/**
 * Where every widget goes on the phone grid.
 *  - widgets with a layout_sm use it (clamped to the grid), hidden ones are left out unless `withHidden`;
 *  - the others are appended below, in the automatic order, with a default size;
 *  - dividers and boxes without a phone position are appended last as hidden items, so Phone mode can
 *    offer them ("Show on phone") but they never appear on a phone until someone places them.
 * Returns [{id, x, y, w, h, hidden, pinned}]; `pinned` = it has a stored phone position.
 */
export function derivePhoneLayout(widgets, { cols = 24, colsSm = COLS_SM_DEFAULT } = {}, { withHidden = false } = {}) {
  const out = [];
  for (const w of widgets) {
    const l = usable(w.layout_sm, colsSm);
    if (l) out.push({ id: w.id, ...l, pinned: true });
  }
  const occupied = out.filter((i) => !i.hidden);
  const startY = bottom(occupied);
  const taken = [...occupied];
  for (const w of autoOrder(widgets)) {
    if (usable(w.layout_sm, colsSm)) continue;
    const size = autoSize(w, cols, colsSm);
    const at = firstFit(taken, size.w, size.h, colsSm, startY);
    const item = { id: w.id, ...at, ...size, hidden: false, pinned: false };
    taken.push(item);
    out.push(item);
  }
  const decor = widgets.filter((w) => isDecor(w) && !usable(w.layout_sm, colsSm)).sort((a, b) => a.y - b.y || a.x - b.x);
  const decorY = bottom(taken);
  for (const w of decor) {
    const size = { w: colsSm, h: clamp(Math.round(w.h) || 1, 1, STACK_MAX_ROWS) };
    const item = { id: w.id, ...firstFit(taken, size.w, size.h, colsSm, decorY), ...size, hidden: true, pinned: false };
    taken.push(item);
    out.push(item);
  }
  return withHidden ? out : out.filter((i) => !i.hidden);
}

/** Stored phone layouts ([{id, x, y, w, h, hidden?}]) made safe for a grid of `colsSm` columns; unusable ones are dropped. */
export function fitToGrid(items, colsSm) {
  const out = [];
  for (const i of items || []) {
    const u = usable(i, colsSm);
    if (u) out.push({ id: i.id, ...u });
  }
  return out;
}

/**
 * The layout_sm of every widget for "Start from the automatic layout": the current automatic
 * stack, packed onto the grid. Dividers and boxes are not part of the automatic view, so they
 * are seeded hidden (below everything), and can be shown again in Phone mode.
 * Returns [{id, x, y, w, h, hidden}] ready for the layout route.
 */
export function seedPhoneLayout(widgets, { cols = 24, colsSm = COLS_SM_DEFAULT } = {}) {
  const taken = [];
  const items = [];
  for (const w of autoOrder(widgets)) {
    const size = autoSize(w, cols, colsSm);
    const item = { id: w.id, ...firstFit(taken, size.w, size.h, colsSm), ...size, hidden: false };
    taken.push(item);
    items.push(item);
  }
  const decor = widgets.filter(isDecor).sort((a, b) => a.y - b.y || a.x - b.x);
  const startY = bottom(taken);
  for (const w of decor) {
    const size = { w: colsSm, h: clamp(Math.round(w.h) || 1, 1, STACK_MAX_ROWS) };
    const item = { id: w.id, ...firstFit(taken, size.w, size.h, colsSm, startY), ...size, hidden: true };
    taken.push(item);
    items.push(item);
  }
  return items;
}

/**
 * The layout_sm a widget should have after moving to `target` (a page with its widgets).
 * On a custom page it is placed in the first free spot so it never lands on top of another widget;
 * elsewhere the stored value travels unchanged. Null stays null.
 */
export function phoneSpotOn(layout, target) {
  if (!layout) return null;
  const opts = phoneOptions(target);
  if (!opts.custom) return layout;
  const taken = derivePhoneLayout(target.widgets || [], { cols: target.columns || 24, colsSm: opts.colsSm });
  const w = Math.min(layout.w, opts.colsSm);
  return { ...layout, w, ...firstFit(taken, w, layout.h, opts.colsSm) };
}
