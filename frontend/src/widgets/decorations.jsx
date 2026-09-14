/*
 * Decoration widgets: divider lines, headings and boxes/frames.
 * They declare `decoration: true`, which makes the canvas drop the usual widget
 * card chrome (background, border) so only the decoration itself is visible.
 * `under: true` additionally stacks the widget beneath normal widgets and lets
 * clicks pass through it outside edit mode, so a frame can sit around tiles
 * without stealing their clicks.
 */

const LINE_STYLES = [
  { value: "solid", label: "Solid" },
  { value: "dashed", label: "Dashed" },
  { value: "dotted", label: "Dotted" },
];

/** Whether a divider draws vertically (explicitly, or in auto mode because its cell is taller than wide). */
export const dividerVertical = (widget) => {
  const o = widget.config?.orientation;
  return o === "v" || (o !== "h" && widget.h > widget.w);
};

// ---------- Divider line ----------
export const DividerWidget = {
  label: "Divider line",
  size: { w: 6, h: 1 },
  decoration: true,
  // flush: the widget ignores the usual gap inset and the line is drawn on the
  // cell's top/left edge — i.e. exactly on a grid line — bleeding to the cell
  // edges so adjacent divider segments join seamlessly.
  flush: true,
  // hug: in edit mode the widget is only the line — a thin grab strip along it,
  // one length knob at its end, and snapping by the line — not the empty cell
  // it sits on the edge of. Returns the line's axis.
  hug: (widget) => (dividerVertical(widget) ? "v" : "h"),
  fields: [
    {
      key: "orientation",
      label: "Orientation",
      type: "select",
      options: [
        { value: "auto", label: "Auto (follows widget shape)" },
        { value: "h", label: "Horizontal" },
        { value: "v", label: "Vertical" },
      ],
    },
    { key: "style", label: "Line style", type: "select", options: LINE_STYLES },
    { key: "thickness", label: "Thickness (px)", type: "number", placeholder: "2", min: 1, max: 16 },
    { key: "color", label: "Color", type: "color" },
    {
      key: "caps",
      label: "End caps",
      type: "select",
      options: [
        { value: "none", label: "None" },
        { value: "dots", label: "Dots" },
        { value: "arrow", label: "Arrow (end)" },
        { value: "arrows", label: "Arrows (both ends)" },
      ],
    },
    { key: "label", label: "Label", type: "text", help: "Shown in the middle of horizontal lines." },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const vertical = dividerVertical(widget);
    const caps = c.caps || "none";
    const startCap = caps === "dots" ? "dot" : caps === "arrows" ? "arrow" : null;
    const endCap = caps === "dots" ? "dot" : caps === "arrows" || caps === "arrow" ? "arrow" : null;
    const style = {
      "--line-color": c.color || "var(--muted)",
      "--line-size": `${c.thickness || 2}px`,
      "--line-style": c.style || "solid",
    };
    return (
      <div className={`divider ${vertical ? "divider-v" : "divider-h"}`} style={style}>
        {startCap && <span className={`cap cap-start cap-${startCap}`} />}
        <span className="line-seg" />
        {c.label && !vertical && (
          <>
            <span className="divider-label">{c.label}</span>
            <span className="line-seg" />
          </>
        )}
        {endCap && <span className={`cap cap-end cap-${endCap}`} />}
      </div>
    );
  },
};

// ---------- Heading ----------
export const HeadingWidget = {
  label: "Heading",
  size: { w: 8, h: 1 },
  decoration: true,
  fields: [
    { key: "text", label: "Text", type: "text" },
    {
      key: "size",
      label: "Size",
      type: "select",
      options: [
        { value: "s", label: "Small" },
        { value: "m", label: "Medium" },
        { value: "l", label: "Large" },
        { value: "xl", label: "Extra large" },
      ],
    },
    {
      key: "align",
      label: "Alignment",
      type: "select",
      options: [
        { value: "left", label: "Left" },
        { value: "center", label: "Centred" },
        { value: "right", label: "Right" },
      ],
    },
    { key: "color", label: "Color", type: "color" },
    { key: "uppercase", label: "Uppercase", type: "checkbox" },
    { key: "bar", label: "Accent bar underneath", type: "checkbox" },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const cls = [
      "deco-heading",
      `heading-${c.size || "m"}`,
      `heading-align-${c.align || "left"}`,
      c.uppercase ? "heading-upper" : "",
      c.bar ? "heading-bar" : "",
    ].join(" ");
    return (
      <div className={cls} style={c.color ? { color: c.color } : undefined}>
        <span>{c.text || "Heading"}</span>
      </div>
    );
  },
};

// ---------- Box / frame ----------
export const BoxWidget = {
  label: "Box / frame",
  size: { w: 10, h: 6 },
  decoration: true,
  under: true,
  // flush: the frame's border is centred on the cell boundary, i.e. drawn on
  // the grid lines themselves, so it aligns exactly with divider lines.
  flush: true,
  fields: [
    { key: "title", label: "Title", type: "text" },
    {
      key: "style",
      label: "Border style",
      type: "select",
      options: [...LINE_STYLES, { value: "none", label: "None" }],
    },
    { key: "color", label: "Border color", type: "color" },
    { key: "borderWidth", label: "Border width (px)", type: "number", placeholder: "1", min: 0, max: 12 },
    { key: "fill", label: "Fill color", type: "color" },
    { key: "fillOpacity", label: "Fill opacity", type: "range", min: 0, max: 100, unit: "%" },
    { key: "radius", label: "Corner radius (px)", type: "number", placeholder: "10", min: 0, max: 40 },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const borderStyle = c.style || "solid";
    const style = {
      "--box-bw": borderStyle === "none" ? "0px" : `${c.borderWidth ?? 1}px`,
      borderStyle,
      borderColor: c.color || "var(--border)",
      borderWidth: "var(--box-bw)",
      borderRadius: `${c.radius ?? 10}px`,
      background: c.fill ? `color-mix(in srgb, ${c.fill} ${c.fillOpacity ?? 15}%, transparent)` : "transparent",
    };
    return (
      <div className="deco-box" style={style}>
        {c.title && <div className="deco-box-title">{c.title}</div>}
      </div>
    );
  },
};
