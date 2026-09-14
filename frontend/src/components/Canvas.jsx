import { useEffect, useMemo, useRef, useState } from "react";
import RGL, { WidthProvider } from "react-grid-layout";
import { RenderWidget, WIDGET_TYPES } from "../widgets/index.jsx";

const GridLayout = WidthProvider(RGL);

/** Snap modes → how many sub-cells make up one grid cell. "free" is resolved at render time (~1px). */
export const SNAP_MODES = [
  { value: "cell", label: "Grid cell" },
  { value: "half", label: "½ cell" },
  { value: "quarter", label: "¼ cell" },
  { value: "free", label: "Free (no snapping)" },
];
const SNAP_DIV = { cell: 1, half: 2, quarter: 4 };

/** Distance (in cells) an arrow-key nudge moves a widget in each snap mode. */
export const nudgeStep = (dashboard) => 1 / (SNAP_DIV[gridOptions(dashboard).snap] || 4);

/** Below this canvas width, view mode stacks widgets instead of squeezing the grid. */
const NARROW_PX = 720;
const STACK_COLS = 4;

export function gridOptions(dashboard) {
  const s = dashboard.settings || {};
  return {
    cols: dashboard.columns || 24,
    rowHeight: dashboard.row_height || 40,
    gap: s.gap ?? 10,
    snap: s.snap || "cell",
    showGrid: s.show_grid ?? true,
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
 *  - Narrow screens (view mode): widgets are stacked in reading order in a simple
 *    4-column flow — small widgets take half the width, the rest the full width.
 */
export default function Canvas({ dashboard, widgets, services, health, integrations, integrationData, onRefresh, editMode, onLayoutChange, onEditWidget, selectedId, onSelect }) {
  const { cols, rowHeight, gap, snap, showGrid } = gridOptions(dashboard);
  const rowPitch = rowHeight + gap;
  // "free" ≈ one sub-cell per pixel of row pitch.
  const K = snap === "free" ? Math.max(1, Math.round(rowPitch)) : SNAP_DIV[snap] || 1;

  const canvasRef = useRef(null);
  const [guides, setGuides] = useState(null); // {v: px|null, h: px|null} while dragging
  const [narrow, setNarrow] = useState(() => window.innerWidth < NARROW_PX);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < NARROW_PX));
    ro.observe(el);
    return () => ro.disconnect();
  }, [narrow, editMode]); // the stacked and grid views render different root elements

  // Line-like decorations ("h" / "v") are edited as the line itself: see `hug` in decorations.jsx.
  const hugById = useMemo(() => Object.fromEntries(widgets.map((w) => [w.id, WIDGET_TYPES[w.type]?.hug?.(w) || null])), [widgets]);
  const [dragHug, setDragHug] = useState(null); // hug axis of the item being dragged/resized

  const layout = useMemo(
    () =>
      widgets.map((w) => {
        const hug = hugById[w.id];
        return {
          i: w.id,
          x: Math.round(w.x * K),
          y: Math.round(w.y * K),
          w: Math.max(1, Math.round(w.w * K)),
          h: Math.max(1, Math.round(w.h * K)),
          minW: 1,
          minH: 1,
          // A line only has a length: one knob at its far end.
          ...(hug && { resizeHandles: [hug === "h" ? "e" : "s"] }),
        };
      }),
    [widgets, K, hugById]
  );

  const commit = (next) => {
    const changed = next
      .map((l) => ({ id: l.i, x: l.x / K, y: l.y / K, w: l.w / K, h: l.h / K }))
      .filter((l) => {
        const cur = widgets.find((w) => w.id === l.id);
        const diff = (a, b) => Math.abs(a - b) > 1e-6;
        return cur && (diff(cur.x, l.x) || diff(cur.y, l.y) || diff(cur.w, l.w) || diff(cur.h, l.h));
      });
    if (changed.length) onLayoutChange(changed);
  };

  // px per sub-cell unit on each axis (x depends on the live container width)
  const scales = () => {
    const width = canvasRef.current?.clientWidth ?? 1280;
    return { xs: (width - gap) / (cols * K), ys: rowPitch / K };
  };

  /** Compute the magnetic snap for item `l` against everything else. */
  const magnet = (l, lay, mode, e) => {
    if (e?.altKey) return null; // Alt = move freely
    const { xs, ys } = scales();
    const others = lay.filter((o) => o.i !== l.i);
    // A horizontal line lives on its cell's top edge (a vertical one on its left edge),
    // so across the line only that edge counts, both as a target and when moving it.
    const xPts = (o) => (hugById[o.i] === "v" ? [o.x] : [o.x, o.x + o.w, o.x + o.w / 2]);
    const yPts = (o) => (hugById[o.i] === "h" ? [o.y] : [o.y, o.y + o.h, o.y + o.h / 2]);
    const xT = others.map(xPts);
    const yT = others.map(yPts);
    xT.push([0, cols * K, (cols * K) / 2]); // page edges + centre line
    yT.push([0]); // top of the page
    // While resizing (se/e/s handles) only the right/bottom edges move.
    const ownX = mode === "resize" ? [l.x + l.w] : xPts(l);
    const ownY = mode === "resize" ? [l.y + l.h] : yPts(l);
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
    setDragHug(null);
    commit(lay);
  };

  const onResize = (lay, oldItem, l, placeholder, e) => {
    const m = magnet(l, lay, "resize", e);
    if (m?.sx) placeholder.w = Math.max(1, l.w + m.sx.d);
    if (m?.sy) placeholder.h = Math.max(1, l.h + m.sy.d);
    showGuides(m);
  };

  const onResizeStop = (lay, oldItem, l, placeholder, e) => {
    const m = magnet(l, lay, "resize", e);
    if (m?.sx) l.w = Math.max(1, l.w + m.sx.d);
    if (m?.sy) l.h = Math.max(1, l.h + m.sy.d);
    setGuides(null);
    setDragHug(null);
    commit(lay);
  };

  // Grid-paper background sized to the cells (approximate; cell width depends on container width).
  const bgStyle = {
    "--widget-gap": `${gap}px`,
    ...(editMode && showGrid
      ? {
          backgroundSize: `calc((100% - ${gap}px) / ${cols}) ${rowPitch}px`,
          backgroundPosition: `${gap / 2}px ${gap / 2}px`,
        }
      : { backgroundImage: "none" }),
  };

  const renderWidget = (w) => {
    const def = WIDGET_TYPES[w.type];
    return (
      <div className={`widget widget-${w.type} ${def?.decoration || w.config?.transparent ? "widget-bare" : ""} ${def?.flush ? "widget-flush" : ""} ${editMode && w.id === selectedId ? "widget-selected" : ""}`}>
        <div className="widget-body">
          <RenderWidget widget={w} services={services} health={health} integrations={integrations} integrationData={integrationData} onRefresh={onRefresh} />
        </div>
        {editMode && (
          <>
            <div
              className={`widget-overlay ${hugById[w.id] ? `widget-hit widget-hit-${hugById[w.id]}` : ""}`}
              onMouseDown={() => onSelect?.(w.id)}
              onDoubleClick={() => onEditWidget(w)}
              title="Drag to move · double-click to edit"
            />
            <button className="widget-gear" onClick={() => onEditWidget(w)} title="Widget settings">⚙</button>
          </>
        )}
      </div>
    );
  };

  if (narrow && !editMode) {
    // Reading order; lines and frames only make sense on the full grid.
    const stacked = widgets
      .filter((w) => w.type !== "divider" && w.type !== "box")
      .sort((a, b) => a.y - b.y || a.x - b.x);
    return (
      <div ref={canvasRef} className="canvas canvas-stacked" style={{ "--widget-gap": `${gap}px`, padding: gap / 2 }}>
        {stacked.map((w) => {
          const half = w.w / cols <= 0.2 && w.type !== "heading";
          return (
            <div
              key={w.id}
              className="widget-cell"
              style={{ gridColumn: `span ${half ? STACK_COLS / 2 : STACK_COLS}`, height: Math.max(rowPitch, Math.round(w.h * rowPitch)) }}
            >
              {renderWidget(w)}
            </div>
          );
        })}
        {stacked.length === 0 && <div className="canvas-empty"><p>This dashboard is empty.</p></div>}
      </div>
    );
  }

  return (
    <div
      ref={canvasRef}
      className={`canvas ${editMode ? "canvas-edit" : ""} ${dragHug ? `canvas-drag-hug-${dragHug}` : ""}`}
      style={bgStyle}
      onMouseDown={(e) => editMode && !e.target.closest(".widget-cell") && onSelect?.(null)}
    >
      <GridLayout
        className="layout"
        layout={layout}
        cols={cols * K}
        rowHeight={rowPitch / K}
        margin={[0, 0]}
        containerPadding={[gap / 2, gap / 2]}
        compactType={null}
        preventCollision={false}
        allowOverlap
        isDraggable={editMode}
        isResizable={editMode}
        isBounded={false}
        resizeHandles={["se", "e", "s"]}
        draggableCancel=".widget-gear"
        onDragStart={(lay, oldItem, l) => { onSelect?.(l.i); setDragHug(hugById[l.i]); }}
        onResizeStart={(lay, oldItem, l) => { onSelect?.(l.i); setDragHug(hugById[l.i]); }}
        onDrag={onDrag}
        onDragStop={onDragStop}
        onResize={onResize}
        onResizeStop={onResizeStop}
        useCSSTransforms
      >
        {widgets.map((w) => {
          const def = WIDGET_TYPES[w.type];
          const cellCls = [
            "widget-cell",
            def?.decoration ? "widget-cell-deco" : "",
            def?.under ? "widget-cell-under" : "",
            editMode && w.id === selectedId ? "widget-cell-selected" : "",
            hugById[w.id] ? `widget-cell-hug widget-cell-hug-${hugById[w.id]}` : "",
          ].join(" ");
          return (
            <div key={w.id} className={cellCls}>
              {renderWidget(w)}
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
}
