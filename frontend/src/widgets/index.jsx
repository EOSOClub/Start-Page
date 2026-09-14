import { marked } from "marked";
import DOMPurify from "dompurify";
import Icon from "./Icon.jsx";
import { DockerWidget, StatusWidget, TopologyWidget, UptimeKumaWidget } from "./integrations.jsx";
import { BoxWidget, DividerWidget, HeadingWidget } from "./decorations.jsx";
import { BookmarksWidget, GreetingWidget, NotesWidget, SearchWidget, TRANSPARENT_FIELD, WeatherWidget, useNow } from "./startpage.jsx";

/*
 * Widget registry.
 * Each type declares:
 *   label        - shown in the "Add widget" menu
 *   size         - default {w,h} in grid cells
 *   usesService  - whether the widget references a registry service
 *   fields       - config form schema: {key, label, type, options?, placeholder?, help?}
 *   Render       - component receiving {widget, service, services, health}
 */

// ---------- helpers ----------
const STATUS_LABEL = { online: "Online", offline: "Offline", degraded: "Degraded", disabled: "", unknown: "Checking…" };

function StatusDot({ status }) {
  const s = status?.status ?? "unknown";
  if (s === "disabled") return null;
  const title = [
    STATUS_LABEL[s],
    status?.http_status ? `HTTP ${status.http_status}` : null,
    status?.latency_ms != null ? `${status.latency_ms} ms` : null,
    status?.error,
  ]
    .filter(Boolean)
    .join(" · ");
  return <span className={`status-dot status-${s}`} title={title} />;
}

function Tile({ href, newTab, icon, name, sub, color, status, showLatency, layout, align, children }) {
  layout = layout || "auto";
  align = align || "auto";
  const style = color ? { "--accent": color } : undefined;
  const cls = `tile tile-l-${layout} tile-align-${align}`;
  const body = (
    <>
      <div className="tile-head">
        <Icon icon={icon} url={href} fallback={name} className="tile-icon" />
        <div className="tile-text">
          <div className="tile-name">
            {status && <StatusDot status={status} />}
            <span className="tile-name-text">{name || "Untitled"}</span>
          </div>
          {sub && <div className="tile-sub">{sub}</div>}
          {showLatency && status?.latency_ms != null && (
            <div className="tile-sub">{status.latency_ms} ms</div>
          )}
        </div>
      </div>
      {children}
    </>
  );
  if (!href) return <div className={cls} style={style}>{body}</div>;
  return (
    <a
      className={cls}
      style={style}
      href={href}
      target={newTab ? "_blank" : "_self"}
      rel="noreferrer"
      draggable={false}
    >
      {body}
    </a>
  );
}

// ---------- Link ----------
const LinkWidget = {
  label: "Link",
  size: { w: 4, h: 2 },
  fields: [
    { key: "name", label: "Name", type: "text" },
    { key: "url", label: "URL", type: "text", placeholder: "http://192.168.1.20:8080" },
    { key: "icon", label: "Icon", type: "icon" },
    { key: "description", label: "Description", type: "text" },
    { key: "color", label: "Accent color", type: "color" },
    {
      key: "layout",
      label: "Layout",
      type: "select",
      options: [
        { value: "auto", label: "Auto (adapts to widget size)" },
        { value: "row", label: "Icon beside text" },
        { value: "stack", label: "Icon above text" },
        { value: "icon", label: "Icon only" },
        { value: "text", label: "Text only" },
      ],
    },
    {
      key: "align",
      label: "Alignment",
      type: "select",
      options: [
        { value: "auto", label: "Auto (rows left, stacked centred)" },
        { value: "center", label: "Centred" },
        { value: "start", label: "Left" },
      ],
    },
    { key: "newTab", label: "Open in new tab", type: "checkbox" },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    return <Tile href={c.url} newTab={c.newTab} icon={c.icon} name={c.name} sub={c.description} color={c.color} layout={c.layout} align={c.align} />;
  },
};

// ---------- Service ----------
const ServiceWidget = {
  label: "Service",
  size: { w: 4, h: 2 },
  usesService: true,
  fields: [
    { key: "label", label: "Override name", type: "text", placeholder: "(use service name)" },
    { key: "showUrl", label: "Show address", type: "checkbox" },
    { key: "showLatency", label: "Show latency", type: "checkbox" },
    {
      key: "layout",
      label: "Layout",
      type: "select",
      options: [
        { value: "auto", label: "Auto (adapts to widget size)" },
        { value: "row", label: "Icon beside text" },
        { value: "stack", label: "Icon above text" },
        { value: "icon", label: "Icon only" },
        { value: "text", label: "Text only" },
      ],
    },
    {
      key: "align",
      label: "Alignment",
      type: "select",
      options: [
        { value: "auto", label: "Auto (rows left, stacked centred)" },
        { value: "center", label: "Centred" },
        { value: "start", label: "Left" },
      ],
    },
    { key: "newTab", label: "Open in new tab", type: "checkbox" },
  ],
  Render: ({ widget, service, health }) => {
    const c = widget.config;
    if (!service) {
      return <Tile name={c.label || "No service"} sub="Select a service in widget settings" />;
    }
    const sub = c.showUrl ? service.url.replace(/^https?:\/\//, "") : service.description;
    return (
      <Tile
        href={service.url}
        newTab={c.newTab}
        icon={service.icon}
        name={c.label || service.name}
        sub={sub}
        color={service.color}
        status={health[service.id] ?? { status: service.health_enabled ? "unknown" : "disabled" }}
        showLatency={c.showLatency}
        layout={c.layout}
        align={c.align}
      />
    );
  },
};

// ---------- Group ----------
const GroupWidget = {
  label: "Group",
  size: { w: 8, h: 4 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    {
      key: "mode",
      label: "Members",
      type: "select",
      options: [
        { value: "category", label: "All services in a category" },
        { value: "ids", label: "Pick services" },
      ],
    },
    { key: "category", label: "Category", type: "category", showIf: (c) => (c.mode ?? "category") === "category" },
    { key: "service_ids", label: "Services", type: "services", showIf: (c) => c.mode === "ids" },
    { key: "columns", label: "Columns", type: "number", placeholder: "auto" },
    { key: "newTab", label: "Open in new tab", type: "checkbox" },
  ],
  Render: ({ widget, services, health }) => {
    const c = widget.config;
    const mode = c.mode ?? "category";
    const members =
      mode === "ids"
        ? (c.service_ids ?? []).map((id) => services.find((s) => s.id === id)).filter(Boolean)
        : services.filter((s) => (s.category || "") === (c.category || ""));
    const style = c.columns ? { gridTemplateColumns: `repeat(${c.columns}, minmax(0, 1fr))` } : undefined;
    return (
      <div className="group">
        {c.title && <div className="group-title">{c.title}</div>}
        <div className="group-grid" style={style}>
          {members.length === 0 && <div className="muted">No services</div>}
          {members.map((s) => (
            <a
              key={s.id}
              className="group-item"
              href={s.url}
              target={c.newTab ? "_blank" : "_self"}
              rel="noreferrer"
              draggable={false}
              style={s.color ? { "--accent": s.color } : undefined}
            >
              <Icon icon={s.icon} url={s.url} fallback={s.name} size={24} />
              <span className="group-item-name">
                <StatusDot status={health[s.id] ?? { status: s.health_enabled ? "unknown" : "disabled" }} />
                {s.name}
              </span>
            </a>
          ))}
        </div>
      </div>
    );
  },
};

// ---------- Text / Markdown ----------
const TextWidget = {
  label: "Text / Markdown",
  size: { w: 6, h: 4 },
  fields: [{ key: "content", label: "Markdown", type: "textarea" }],
  Render: ({ widget }) => (
    <div
      className="text-widget"
      dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(widget.config.content || "*Empty*")) }}
    />
  ),
};

// ---------- Iframe ----------
const IframeWidget = {
  label: "Iframe",
  size: { w: 8, h: 6 },
  fields: [
    { key: "url", label: "URL", type: "text" },
    { key: "title", label: "Title", type: "text" },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    if (!c.url) return <div className="muted pad">Set a URL</div>;
    return (
      <div className="iframe-widget">
        {c.title && <div className="group-title">{c.title}</div>}
        <iframe src={c.url} title={c.title || "frame"} sandbox="allow-scripts allow-same-origin allow-forms allow-popups" />
      </div>
    );
  },
};

// ---------- Image ----------
const ImageWidget = {
  label: "Image",
  size: { w: 4, h: 4 },
  fields: [
    { key: "url", label: "Image URL", type: "text" },
    { key: "link", label: "Link to", type: "text" },
    {
      key: "fit",
      label: "Fit",
      type: "select",
      options: [
        { value: "cover", label: "Cover" },
        { value: "contain", label: "Contain" },
      ],
    },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const img = <img className="image-widget" src={c.url} alt="" style={{ objectFit: c.fit || "cover" }} draggable={false} />;
    if (!c.url) return <div className="muted pad">Set an image URL</div>;
    return c.link ? <a href={c.link} draggable={false}>{img}</a> : img;
  },
};

// ---------- Clock / World Clock ----------
const ClockWidget = {
  label: "Clock",
  size: { w: 4, h: 2 },
  fields: [
    { key: "timezone", label: "Timezone", type: "text", placeholder: "(local)", help: 'IANA timezone name, e.g. America/New_York or Europe/London. Abbreviations like EST/PST are not valid. Empty = your local time.' },
    { key: "showSeconds", label: "Show seconds", type: "checkbox" },
    { key: "h24", label: "24-hour", type: "checkbox" },
    { key: "showDate", label: "Show date", type: "checkbox" },
    { key: "label", label: "Label", type: "text" },
    TRANSPARENT_FIELD,
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const now = useNow(c.showSeconds);
    let tz;
    if (c.timezone) {
      try { now.toLocaleTimeString("en", { timeZone: c.timezone }); tz = c.timezone; }
      catch { tz = undefined; }
    }
    const opts = { hour: "2-digit", minute: "2-digit", hour12: !c.h24 };
    if (c.showSeconds) opts.second = "2-digit";
    return (
      <div className="clock">
        {c.label && <div className="tile-sub">{c.label}</div>}
        <div className="clock-time">
          {now.toLocaleTimeString([], { ...opts, timeZone: tz })}
        </div>
        {c.showDate && (
          <div className="tile-sub">
            {now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", timeZone: tz })}
          </div>
        )}
      </div>
    );
  },
};

// ---------- Service table ----------
const ServiceTableWidget = {
  label: "Service table",
  size: { w: 8, h: 6 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    { key: "category", label: "Filter by category", type: "category", placeholder: "(all)" },
  ],
  Render: ({ widget, services, health }) => {
    const c = widget.config;
    const rows = c.category ? services.filter((s) => s.category === c.category) : services;
    return (
      <div className="group">
        {c.title && <div className="group-title">{c.title}</div>}
        <table className="svc-table">
          <tbody>
            {rows.map((s) => {
              const h = health[s.id];
              return (
                <tr key={s.id}>
                  <td><StatusDot status={h ?? { status: s.health_enabled ? "unknown" : "disabled" }} /></td>
                  <td><a href={s.url} draggable={false}>{s.name}</a></td>
                  <td className="muted">{s.host}{s.port ? `:${s.port}` : ""}</td>
                  <td className="muted right">{h?.latency_ms != null ? `${h.latency_ms} ms` : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  },
};

export const WIDGET_TYPES = {
  link: LinkWidget,
  bookmarks: BookmarksWidget,
  search: SearchWidget,
  greeting: GreetingWidget,
  clock: ClockWidget,
  weather: WeatherWidget,
  notes: NotesWidget,
  service: ServiceWidget,
  group: GroupWidget,
  text: TextWidget,
  iframe: IframeWidget,
  image: ImageWidget,
  service_table: ServiceTableWidget,
  topology: TopologyWidget,
  docker: DockerWidget,
  uptime_kuma: UptimeKumaWidget,
  status: StatusWidget,
  divider: DividerWidget,
  heading: HeadingWidget,
  box: BoxWidget,
};

/** Initial config for a new widget: every field that declares a `default`. */
export const defaultConfig = (type) =>
  Object.fromEntries((WIDGET_TYPES[type]?.fields || []).filter((f) => f.default !== undefined).map((f) => [f.key, f.default]));

export function RenderWidget({ widget, services, health, integrations = [], integrationData = {}, onRefresh }) {
  const def = WIDGET_TYPES[widget.type];
  if (!def) return <div className="muted pad">Unknown widget type: {widget.type}</div>;
  const service = widget.service_id ? services.find((s) => s.id === widget.service_id) : null;
  return (
    <def.Render
      widget={widget}
      service={service}
      services={services}
      health={health}
      integrations={integrations}
      integrationData={integrationData}
      onRefresh={onRefresh}
    />
  );
}
