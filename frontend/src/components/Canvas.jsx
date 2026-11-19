import { useEffect, useMemo, useRef, useState } from "react";
import RGL, { WidthProvider } from "react-grid-layout";
import { RenderWidget, WIDGET_TYPES } from "../widgets/index.jsx";
import { NARROW_PX, PHONE_PX, STACK_MAX_ROWS, derivePhoneLayout, phoneOptions } from "../phone.js";

const GridLayout = WidthProvider(RGL);

/** The class list of a widget's frame: decorations and transparent widgets are "bare" (no card background). */
export function widgetClassName(w, def, { selected = false, hidden = false } = {}) {
  return [
    "widget",
    `widget-${w.type}`,
    def?.decoration || w.config?.transparent ? "widget-bare" : "",
    def?.flush ? "widget-flush" : "",
    selected ? "widget-selected" : "",
    hidden ? "widget-phone-hidden" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

/** Snap modes → how many sub-cells make up one grid cell. "free" is resolved at render time (~1px). */
export const SNAP_MODES = [
  { value: "cell", label: "Grid cell" },
  { value: "half", label: "½ cell" },
  { value: "quarter", label: "¼ cell" },
  { value: "free", label: "Free (no snapping)" },
];
const SNAP_DIV = { cell: 1, half: 2, quarter: 4 };

/** Distance (in cells) an arrow-key nudge moves a widget in each snap mode. */
export const nudgeStep = (dashboard) => {
  const o = gridOptions(dashboard);
  // "free" has no SNAP_DIV entry; resolve it like the render grid does (K ≈ 1 sub-cell per px),
  // so the arrow keys move exactly 1px while cell/half/quarter keep their grid-unit steps.
  if (o.snap === "free") return 1 / Math.max(1, Math.round(o.rowHeight + o.gap));
  return 1 / (SNAP_DIV[o.snap] || 4);
};

/* Below NARROW_PX (phone.js) view mode shows the phone layout: stacked automatically, or the
 * page's custom phone grid. STACK_MAX_ROWS (phone.js) caps how tall a stacked card gets;
 * taller widgets scroll inside the card. */
const STACK_COLS = 4;

/**
 * The single read path for dashboard.settings, which is untyped server data. typeof checks only:
 * Number()/String() throw on hostile objects. Page backgrounds are undefined when absent or invalid,
 * so App's page → global → default fallback still applies.
 */
export function gridOptions(dashboard) {
  const s = dashboard.settings || {};
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
  return {
    cols: dashboard.columns || 24,
    rowHeight: dashboard.row_height || 40,
    gap: num(s.gap) ?? 10,
    snap: SNAP_MODES.some((m) => m.value === s.snap) ? s.snap : "cell",
    showGrid: typeof s.show_grid === "boolean" ? s.show_grid : true,
    background_image: typeof s.background_image === "string" ? s.background_image : undefined,
    background_dim: num(s.background_dim),
    background_blur: num(s.background_blur),
  };
}

/* ---------- magnetic widget-to-widget snapping ----------
 * On top of the grid snap, a dragged/resized widget is attracted to the edges
 * and centre lines of every other widget (and the page edges / centre) when it
 * gets within MAGNET_PX. Grid positions are integers (sub-cells), so a snap is
 * only applied when a whole-unit shift actually lands on the target — in coarse
 * snap modes an off-grid target simply doesn't magnetise. Hold Alt to disable.
 */
const MAGNET_PX = 8; // attraction range
const MAGNET_TOL_PX = 2; // max residual misalignment after rounding to grid units
const GRID_BIAS_PX = 2; // grid lines beat widget edges unless the edge is clearly closer

// Every widget resizes from any side or corner; a line only along its length, from either end.
const ALL_HANDLES = ["se", "e", "s", "sw", "w", "nw", "n", "ne"];
const LINE_HANDLES = { h: ["w", "e"], v: ["n", "s"] };
/** Which handle ("e", "nw", …) a resize callback's handle element is. */
const handleOf = (node) => node?.className?.match?.(/react-resizable-handle-(\w+)/)?.[1] || "se";

/** Best snap on one axis. All points are in grid units; `scale` is px per unit.
 *  `gridStep` (sub-cells per cell) adds the grid lines themselves as targets;
 *  they take priority over widget edges on anything close to a tie.
 *  Returns {d: integer unit shift, guide: unit coordinate of the matched line}. */
function snapAxis(ownPts, targetPtLists, scale, gridStep) {
  let widget = null;
  for (const pts of targetPtLists)
    for (const tp of pts)
      for (const op of ownPts) {
        const dPx = (tp - op) * scale;
        if (Math.abs(dPx) <= MAGNET_PX && (!widget || Math.abs(dPx) < Math.abs(widget.dPx)))
          widget = { dPx, tp, op };
      }
  let grid = null;
  if (gridStep > 1)
    for (const op of ownPts) {
      const tp = Math.round(op / gridStep) * gridStep;
      const dPx = (tp - op) * scale;
      if (Math.abs(dPx) <= MAGNET_PX && (!grid || Math.abs(dPx) < Math.abs(grid.dPx)))
        grid = { dPx, tp, op };
    }
  const best = grid && (!widget || Math.abs(grid.dPx) <= Math.abs(widget.dPx) + GRID_BIAS_PX) ? grid : widget;
  if (!best) return null;
  const d = Math.round(best.tp - best.op);
  if (Math.abs((best.op + d - best.tp) * scale) > MAGNET_TOL_PX) return null;
  return { d, guide: best.tp };
}

/**
 * Freeform grid canvas.
 *  - compactType={null}: nothing auto-stacks; widgets stay exactly where you drop them.
 *  - allowOverlap: dragging never pushes neighbours around.
 *  - Sub-cell precision: react-grid-layout only understands integer cells, so the grid is
 *    rendered at K sub-cells per cell and coordinates are scaled by K on the way in and out.
 *    Widget geometry is stored in (fractional) cells, so changing the snap mode never moves anything.
 *  - The gap between widgets is applied with CSS (inset on the item) instead of RGL's margin, so
 *    the pitch of a cell is exactly (row_height + gap) regardless of K.
 *  - Dragging/resizing only in edit mode; in edit mode a transparent overlay
 *    captures the pointer so link tiles don't navigate while you drag.
 *  - Magnetic snapping: while dragging/resizing, edges and centres attract to those of
 *    other widgets; the placeholder previews the snapped spot and guide lines show the
 *    matched alignment. The snap itself is applied when the drag/resize ends.
 *  - Selection (edit mode): clicking a widget selects it for keyboard nudging; App owns the keys.
 *  - Narrow screens (view mode): the phone layout. By default ("auto") widgets are stacked in reading
 *    order in a simple 4-column flow — small widgets take half the width, the rest the full width.
 *    With the page's phone_layout "custom" it is a real grid of `columns_sm` columns that follows each
 *    widget's layout_sm (see phone.js); widgets without one are appended below, hidden ones are skipped.
 *  - Edit mode, device="phone": the same phone grid at a fixed phone width inside a device frame;
 *    dragging/resizing edits layout_sm (onPhoneLayoutChange) and the desktop geometry is never touched.
 *    On an "auto" page it previews the stack and offers to start a custom layout from it.
 */
export default function Canvas({
  dashboard, widgets, services, health, history, integrations, integrationData, onRefresh, editMode,
  onLayoutChange, onEditWidget, selectedId, onSelect,
  device = "desktop", onPhoneLayoutChange, onTogglePhoneHidden, onStartPhoneLayout,
}) {
  const { cols, rowHeight, gap, snap, showGrid } = gridOptions(dashboard);
  const { colsSm, custom } = phoneOptions(dashboard);
  const rowPitch = rowHeight + gap;
  // "free" ≈ one sub-cell per pixel of row pitch.
  const K = snap === "free" ? Math.max(1, Math.round(rowPitch)) : SNAP_DIV[snap] || 1;

  const canvasRef = useRef(null);
  const [guides, setGuides] = useState(null); // {v: px|null, h: px|null} while dragging
  const [narrow, setNarrow] = useState(() => window.innerWidth < NARROW_PX);

  const phoneEdit = editMode && device === "phone";
  const phonePreview = phoneEdit && !custom; // an "auto" page: the stack, read-only, with the offer to customise
  const phoneGrid = custom && (phoneEdit || (narrow && !editMode)); // a real grid of columns_sm columns
  const editing = editMode && !phonePreview; // drag handles, selection and the widget overlays
  const gridCols = phoneGrid ? colsSm : cols;

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    // In the phone frame the canvas is phone-wide whatever the window is: that width says nothing about the
    // window, so it must not flip `narrow` (it would stay true for a frame after leaving phone edit).
    const ro = new ResizeObserver(([entry]) => {
      if (!phoneEdit) setNarrow(entry.contentRect.width < NARROW_PX);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [narrow, editMode, device, custom, phoneEdit]); // the stacked, grid and framed views render different root elements

  // Line decorations ("h" / "v"): see `line` in decorations.jsx.
  const lineById = useMemo(() => Object.fromEntries(widgets.map((w) => [w.id, WIDGET_TYPES[w.type]?.line?.(w) || null])), [widgets]);

  // What the grid shows: each widget with the geometry of the layout being displayed or edited.
  const items = useMemo(() => {
    if (!phoneGrid) return widgets.map((w) => ({ widget: w, x: w.x, y: w.y, w: w.w, h: w.h, hidden: false }));
    const byId = new Map(widgets.map((w) => [w.id, w]));
    return derivePhoneLayout(widgets, { cols, colsSm }, { withHidden: editMode }).map((i) => ({ ...i, widget: byId.get(i.id) }));
  }, [widgets, phoneGrid, cols, colsSm, editMode]);

  const layout = useMemo(
    () =>
      items.map(({ widget, x, y, w, h }) => {
        const line = lineById[widget.id];
        return {
          i: widget.id,
          x: Math.round(x * K),
          y: Math.round(y * K),
          w: Math.max(1, Math.round(w * K)),
          h: Math.max(1, Math.round(h * K)),
          minW: 1,
          minH: 1,
          resizeHandles: line ? LINE_HANDLES[line] : ALL_HANDLES,
        };
      }),
    [items, K, lineById]
  );

  // Only ever called with the item that was dragged/resized. Nothing else can move (no compaction,
  // overlap allowed), and diffing the whole layout would rewrite every widget whose stored
  // fractional geometry merely rounds differently in the current snap mode.
  const commit = (next) => {
    const diff = (a, b) => Math.abs(a - b) > 1e-6;
    const changed = next
      .map((l) => ({ id: l.i, x: l.x / K, y: l.y / K, w: l.w / K, h: l.h / K }))
      .map((l) => {
        if (!phoneGrid) return l;
        const w = Math.min(l.w, colsSm); // the phone grid has no room outside its columns
        return { ...l, w, x: Math.min(Math.max(l.x, 0), colsSm - w) };
      })
      .filter((l) => {
        const cur = items.find((i) => i.widget.id === l.id);
        return cur && (diff(cur.x, l.x) || diff(cur.y, l.y) || diff(cur.w, l.w) || diff(cur.h, l.h));
      });
    if (!changed.length) return;
    if (phoneGrid) onPhoneLayoutChange?.(changed);
    else onLayoutChange(changed);
  };

  // px per sub-cell unit on each axis (x depends on the live container width)
  const scales = () => {
    const width = canvasRef.current?.clientWidth ?? 1280;
    return { xs: (width - gap) / (gridCols * K), ys: rowPitch / K };
  };

  /** Compute the magnetic snap for item `l` against everything else. */
  const magnet = (l, lay, handle, e) => {
    if (e?.altKey) return null; // Alt = move freely
    const { xs, ys } = scales();
    const others = lay.filter((o) => o.i !== l.i);
    // Only the stroke of a line is visible, and it runs through the middle of its
    // cell, so across the line only that middle counts, as a target and when moving it.
    const xPts = (o) => (lineById[o.i] === "v" ? [o.x + o.w / 2] : [o.x, o.x + o.w, o.x + o.w / 2]);
    const yPts = (o) => (lineById[o.i] === "h" ? [o.y + o.h / 2] : [o.y, o.y + o.h, o.y + o.h / 2]);
    const xT = others.map(xPts);
    const yT = others.map(yPts);
    xT.push([0, gridCols * K, (gridCols * K) / 2]); // page edges + centre line
    yT.push([0]); // top of the page
    // While resizing only the edges the handle drags move.
    const edge = (side, pt) => (handle.includes(side) ? [pt] : []);
    const ownX = handle === "move" ? xPts(l) : [...edge("w", l.x), ...edge("e", l.x + l.w)];
    const ownY = handle === "move" ? yPts(l) : [...edge("n", l.y), ...edge("s", l.y + l.h)];
    const sx = snapAxis(ownX, xT, xs, K);
    const sy = snapAxis(ownY, yT, ys, K);
    return sx || sy ? { sx, sy, xs, ys } : null;
  };

  const showGuides = (m) => {
    const g = m
      ? {
          v: m.sx ? gap / 2 + m.sx.guide * m.xs : null,
          h: m.sy ? gap / 2 + m.sy.guide * m.ys : null,
        }
      : null;
    setGuides((cur) => (cur?.v === g?.v && cur?.h === g?.h ? cur : g));
  };

  const onDrag = (lay, oldItem, l, placeholder, e) => {
    const m = magnet(l, lay, "move", e);
    placeholder.x = Math.max(0, l.x + (m?.sx?.d ?? 0));
    placeholder.y = Math.max(0, l.y + (m?.sy?.d ?? 0));
    showGuides(m);
  };

  const onDragStop = (lay, oldItem, l, placeholder, e) => {
    const m = magnet(l, lay, "move", e);
    if (m?.sx) l.x = Math.max(0, l.x + m.sx.d);
    if (m?.sy) l.y = Math.max(0, l.y + m.sy.d);
    setGuides(null);
    commit([l]);
  };

  /** Apply a resize snap to `t`: a left/top edge moves the position and gives the size back. */
  const snapResize = (t, l, m, handle) => {
    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
    if (m?.sx && handle.includes("w")) {
      t.x = clamp(l.x + m.sx.d, 0, l.x + l.w - 1);
      t.w = l.x + l.w - t.x;
    } else if (m?.sx) t.w = Math.max(1, l.w + m.sx.d);
    if (m?.sy && handle.includes("n")) {
      t.y = clamp(l.y + m.sy.d, 0, l.y + l.h - 1);
      t.h = l.y + l.h - t.y;
    } else if (m?.sy) t.h = Math.max(1, l.h + m.sy.d);
  };

  const onResize = (lay, oldItem, l, placeholder, e, node) => {
    const handle = handleOf(node);
    const m = magnet(l, lay, handle, e);
    snapResize(placeholder, { ...l }, m, handle);
    showGuides(m);
  };

  const onResizeStop = (lay, oldItem, l, placeholder, e, node) => {
    const handle = handleOf(node);
    const m = magnet(l, lay, handle, e);
    snapResize(l, { ...l }, m, handle);
    setGuides(null);
    commit([l]);
  };

  // Grid-paper background sized to the cells (approximate; cell width depends on container width).
  const bgStyle = {
    "--widget-gap": `${gap}px`,
    ...(editMode && showGrid
      ? {
          backgroundSize: `calc((100% - ${gap}px) / ${gridCols}) ${rowPitch}px`,
          backgroundPosition: `${gap / 2}px ${gap / 2}px`,
        }
      : { backgroundImage: "none" }),
  };

  const renderWidget = (w, hidden = false) => {
    const def = WIDGET_TYPES[w.type];
    return (
      <div data-widget-id={w.id} className={widgetClassName(w, def, { selected: editing && w.id === selectedId, hidden })}>
        <div className="widget-body">
          <RenderWidget widget={w} services={services} health={health} history={history} integrations={integrations} integrationData={integrationData} onRefresh={onRefresh} />
        </div>
        {editing && (
          <>
            <div
              className="widget-overlay"
              onMouseDown={() => onSelect?.(w.id)}
              onDoubleClick={() => onEditWidget(w)}
              title="Drag to move · double-click to edit"
            />
            <button className="widget-gear" onClick={() => onEditWidget(w)} title="Widget settings">⚙</button>
            {phoneGrid && (
              <button
                className="widget-phone-toggle"
                onClick={() => onTogglePhoneHidden?.(w, !hidden)}
                title={hidden ? "Show this widget on phones" : "Hide this widget on phones"}
              >
                {hidden ? "Show on phone" : "Hide on phone"}
              </button>
            )}
          </>
        )}
      </div>
    );
  };

  // Reading order; lines and frames only make sense on the full grid.
  const stackedView = () => {
    const stacked = widgets
      .filter((w) => w.type !== "divider" && w.type !== "box")
      .sort((a, b) => a.y - b.y || a.x - b.x);
    return (
      <div ref={canvasRef} className={`canvas canvas-stacked ${phonePreview ? "canvas-preview" : ""}`} style={{ "--widget-gap": `${gap}px`, padding: gap / 2 }}>
        {stacked.map((w) => {
          const half = w.w / cols <= 0.2 && w.type !== "heading";
          return (
            <div
              key={w.id}
              className="widget-cell"
              style={{ gridColumn: `span ${half ? STACK_COLS / 2 : STACK_COLS}`, height: Math.min(Math.max(rowPitch, Math.round(w.h * rowPitch)), STACK_MAX_ROWS * rowPitch) }}
            >
              {renderWidget(w)}
            </div>
          );
        })}
        {stacked.length === 0 && <div className="canvas-empty"><p>This dashboard is empty.</p></div>}
      </div>
    );
  };

  const gridView = () => (
    <div
      ref={canvasRef}
      className={`canvas ${editing ? "canvas-edit" : ""}`}
      style={bgStyle}
      onMouseDown={(e) => editing && !e.target.closest(".widget-cell") && onSelect?.(null)}
    >
      <GridLayout
        className="layout"
        layout={layout}
        cols={gridCols * K}
        rowHeight={rowPitch / K}
        margin={[0, 0]}
        containerPadding={[gap / 2, gap / 2]}
        compactType={null}
        preventCollision={false}
        allowOverlap
        isDraggable={editing}
        isResizable={editing}
        isBounded={false}
        resizeHandles={ALL_HANDLES}
        draggableCancel=".widget-gear, .widget-phone-toggle"
        onDragStart={(lay, oldItem, l) => onSelect?.(l.i)}
        onResizeStart={(lay, oldItem, l) => onSelect?.(l.i)}
        onDrag={onDrag}
        onDragStop={onDragStop}
        onResize={onResize}
        onResizeStop={onResizeStop}
        useCSSTransforms
      >
        {items.map(({ widget: w, hidden }) => {
          const def = WIDGET_TYPES[w.type];
          const cellCls = [
            "widget-cell",
            def?.decoration ? "widget-cell-deco" : "",
            def?.under ? "widget-cell-under" : "",
            lineById[w.id] ? `widget-cell-line widget-cell-line-${lineById[w.id]}` : "",
            editing && w.id === selectedId ? "widget-cell-selected" : "",
          ].join(" ");
          return (
            <div key={w.id} className={cellCls}>
              {renderWidget(w, hidden)}
            </div>
          );
        })}
      </GridLayout>
      {guides?.v != null && <div className="snap-guide snap-guide-v" style={{ left: guides.v }} />}
      {guides?.h != null && <div className="snap-guide snap-guide-h" style={{ top: guides.h }} />}
      {widgets.length === 0 && (
        <div className="canvas-empty">
          <p>This dashboard is empty.</p>
          <p className="muted">{editMode ? "Use “+ Widget” in the toolbar to add something." : "Click “Edit” to start building."}</p>
        </div>
      )}
    </div>
  );

  // One wrapper structure for the phone frame and the plain canvas, so the widgets stay mounted when the
  // device toggle flips (outside the phone frame the wrappers are `display: contents`, see styles.css).
  const showStack = phonePreview || (!phoneEdit && narrow && !editMode && !custom);
  return (
    <div className={phoneEdit ? "phone-stage" : "canvas-shell"}>
      <div
        className={phoneEdit ? "phone-frame" : "canvas-shell"}
        style={phoneEdit ? { width: `min(${PHONE_PX}px, 100%)` } : undefined}
        data-testid={phoneEdit ? "phone-frame" : undefined}
      >
        {phonePreview ? (
          <div className="phone-offer">
            <p>Phones currently use the automatic layout (widgets stack in reading order).</p>
            <button className="btn-primary" onClick={() => onStartPhoneLayout?.()}>Start from the automatic layout</button>
          </div>
        ) : null}
        {showStack ? stackedView() : gridView()}
      </div>
    </div>
  );
}
