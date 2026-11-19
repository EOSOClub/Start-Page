import { marked } from "marked";
import { safeFrameSrc, safeHref, safeImageSrc, isSameOrigin } from "../safeUrl.js";
import { sanitizeHtml } from "../sanitize.js";
import Icon from "./Icon.jsx";
import ErrorBoundary from "../components/ErrorBoundary.jsx";
import { DockerWidget, RssWidget, StatusWidget, SystemWidget, TopologyWidget, UptimeKumaWidget } from "./integrations.jsx";
import { IntegrationListWidget, IntegrationStatsWidget, PiholeWidget, PrometheusWidget } from "./integrationViews.jsx";
import { PRESETS } from "./integrationPresets.jsx";
import { HomeAssistantWidget } from "./homeAssistant.jsx";
import { CalendarWidget } from "./calendarWidget.jsx";
import { CountdownWidget } from "./countdownWidget.jsx";
import { GaugeWidget } from "./gaugeWidget.jsx";
import { BoxWidget, DividerWidget, HeadingWidget } from "./decorations.jsx";
import { CustomWidget } from "./customWidget.jsx";
import FitText from "./FitText.jsx";
import { IncidentsWidget, Sparkline, UptimeWidget } from "./healthWidgets.jsx";
import { formatUptime } from "./healthViz.js";
import { BookmarksWidget, GreetingWidget, NotesWidget, SearchWidget, TRANSPARENT_FIELD, WeatherWidget, useNow } from "./startpage.jsx";

/*
 * Widget registry.
 * Each type declares:
 *   label        - shown in the "Add widget" menu
 *   group        - menu section: Basics | Services | Integrations | Decoration (see widgetMenu.js)
 *   size         - default {w,h} in grid cells
 *   usesService  - whether the widget references a registry service
 *   fields       - config form schema: {key, label, type, options?, placeholder?, help?}
 *   Render       - component receiving {widget, service, services, health, history}
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

function Tile({ href, newTab, icon, name, sub, color, status, showLatency, uptime, layout, align, children }) {
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
          {uptime && <div className="tile-sub">{uptime} · 24 h</div>}
        </div>
      </div>
      {children}
    </>
  );
  const safe = safeHref(href);
  if (!safe) return <div className={cls} style={style}>{body}</div>;
  return (
    <a
      className={cls}
      style={style}
      href={safe}
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
  group: "Basics",
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
  group: "Services",
  size: { w: 4, h: 2 },
  usesService: true,
  fields: [
    { key: "label", label: "Override name", type: "text", placeholder: "(use service name)" },
    { key: "showUrl", label: "Show address", type: "checkbox" },
    { key: "showLatency", label: "Show latency", type: "checkbox" },
    { key: "show_uptime", label: "Show 24 h uptime", type: "checkbox" },
    { key: "show_sparkline", label: "Show latency sparkline (24 h)", type: "checkbox" },
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
  Render: ({ widget, service, health, history = {} }) => {
    const c = widget.config;
    if (!service) {
      return <Tile name={c.label || "No service"} sub="Select a service in widget settings" />;
    }
    const sub = c.showUrl ? service.url.replace(/^https?:\/\//, "") : service.description;
    const past = history[service.id];
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
        uptime={c.show_uptime && past ? formatUptime(past.uptime_pct) : null}
        layout={c.layout}
        align={c.align}
      >
        {c.show_sparkline && past && <Sparkline buckets={past.buckets} />}
      </Tile>
    );
  },
};

// ---------- Group ----------
const GroupWidget = {
  label: "Group",
  group: "Services",
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
              href={safeHref(s.url) ?? undefined}
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
  group: "Basics",
  size: { w: 6, h: 4 },
  fields: [{ key: "content", label: "Markdown", type: "textarea" }],
  Render: ({ widget }) => (
    <div
      className="text-widget"
      dangerouslySetInnerHTML={{ __html: sanitizeHtml(marked.parse(widget.config.content || "*Empty*")) }}
    />
  ),
};

// ---------- Iframe ----------
/**
 * Other sites need allow-same-origin to work (cookies, storage), so they keep it. A frame on the app's OWN
 * origin with allow-scripts + allow-same-origin could reach into this page and take its sandbox off, so for
 * those the same-origin flag is dropped: the page is treated as a foreign origin (static content still works).
 */
export const FRAME_SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups";
export const OWN_ORIGIN_FRAME_SANDBOX = "allow-scripts allow-forms allow-popups";
export const frameSandbox = (src) => (isSameOrigin(src) ? OWN_ORIGIN_FRAME_SANDBOX : FRAME_SANDBOX);

const IframeWidget = {
  label: "Iframe",
  group: "Basics",
  size: { w: 8, h: 6 },
  fields: [
    { key: "url", label: "URL", type: "text" },
    { key: "title", label: "Title", type: "text" },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    if (!c.url) return <div className="muted pad">Set a URL</div>;
    const src = safeFrameSrc(c.url);
    if (!src) return <div className="muted pad">Only http(s) or relative URLs can be shown in a frame.</div>;
    return (
      <div className="iframe-widget">
        {c.title && <div className="group-title">{c.title}</div>}
        <iframe src={src} title={c.title || "frame"} sandbox={frameSandbox(src)} />
      </div>
    );
  },
};

// ---------- Image ----------
const ImageWidget = {
  label: "Image",
  group: "Basics",
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
    if (!c.url) return <div className="muted pad">Set an image URL</div>;
    const src = safeImageSrc(c.url);
    if (!src) return <div className="muted pad">This image URL is not allowed (http, https, relative or a png/jpeg/gif/webp data URL).</div>;
    const img = <img className="image-widget" src={src} alt="" style={{ objectFit: c.fit || "cover" }} draggable={false} />;
    const link = safeHref(c.link);
    return link ? <a href={link} draggable={false}>{img}</a> : img;
  },
};

// ---------- Clock / World Clock ----------
const ClockWidget = {
  label: "Clock",
  group: "Basics",
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
      <FitText className="clock" min={10}>
        {c.label && <div className="tile-sub">{c.label}</div>}
        <div className="clock-time">
          {now.toLocaleTimeString([], { ...opts, timeZone: tz })}
        </div>
        {c.showDate && (
          <div className="tile-sub">
            {now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", timeZone: tz })}
          </div>
        )}
      </FitText>
    );
  },
};

// ---------- Service table ----------
const ServiceTableWidget = {
  label: "Service table",
  group: "Services",
  size: { w: 8, h: 6 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    { key: "category", label: "Filter by category", type: "category", placeholder: "(all)" },
    { key: "show_uptime", label: "Show 24 h uptime", type: "checkbox" },
  ],
  Render: ({ widget, services, health, history = {} }) => {
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
                  <td><a href={safeHref(s.url) ?? undefined} draggable={false}>{s.name}</a></td>
                  <td className="muted">{s.host}{s.port ? `:${s.port}` : ""}</td>
                  <td className="muted right">{h?.latency_ms != null ? `${h.latency_ms} ms` : ""}</td>
                  {c.show_uptime && <td className="muted right">{history[s.id] ? formatUptime(history[s.id].uptime_pct) : ""}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  },
};

const REGISTRY = {
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
  uptime: UptimeWidget,
  incidents: IncidentsWidget,
  status: StatusWidget,
  system: SystemWidget,
  rss: RssWidget,
  integration_stats: IntegrationStatsWidget,
  integration_list: IntegrationListWidget,
  pihole: PiholeWidget,
  prometheus: PrometheusWidget,
  // 15 integration types: one preset per type (the Home Assistant and ICS types have their own widgets below)
  sonarr: PRESETS.sonarr,
  radarr: PRESETS.radarr,
  qbittorrent: PRESETS.qbittorrent,
  sabnzbd: PRESETS.sabnzbd,
  jellyfin: PRESETS.jellyfin,
  plex: PRESETS.plex,
  proxmox: PRESETS.proxmox,
  truenas: PRESETS.truenas,
  portainer: PRESETS.portainer,
  unraid: PRESETS.unraid,
  adguard: PRESETS.adguard,
  unifi: PRESETS.unifi,
  speedtest_tracker: PRESETS.speedtest_tracker,
  home_assistant: HomeAssistantWidget,
  calendar: CalendarWidget,
  gauge: GaugeWidget,
  countdown: CountdownWidget,
  divider: DividerWidget,
  heading: HeadingWidget,
  box: BoxWidget,
  custom: CustomWidget,
};

/** Menu sections in display order. An entry's own `group` wins; the older widgets are placed here. */
export const WIDGET_GROUPS = ["Basics", "Services", "Integrations", "Advanced", "Decoration"];
const GROUP_OF = {
  bookmarks: "Basics", search: "Basics", greeting: "Basics", clock: "Basics", weather: "Basics", notes: "Basics",
  topology: "Services", uptime: "Services", incidents: "Services",
  docker: "Integrations", uptime_kuma: "Integrations", status: "Integrations", system: "Integrations", rss: "Integrations",
  integration_stats: "Integrations", integration_list: "Integrations", pihole: "Integrations", prometheus: "Integrations",
  divider: "Decoration", heading: "Decoration", box: "Decoration",
};

export const WIDGET_TYPES = Object.fromEntries(
  Object.entries(REGISTRY).map(([type, def]) => [type, { ...def, group: def.group ?? GROUP_OF[type] ?? "Basics" }])
);

/** Initial config for a new widget: every field that declares a `default`. */
/** A widget's config as the current catalog declares it (a type may fold an old key into a new one). */
export const migrateConfig = (type, config) => WIDGET_TYPES[type]?.migrate?.(config) ?? config ?? {};

/**
 * The config for an unsaved widget whose type was switched from `fromType`: the new type's defaults, plus the
 * values of keys both types declare, when the value is valid for the new field (a select option that exists, a
 * number, a boolean). Keys the old type had alone, and integration/service pickers for another kind of
 * integration, are not carried over.
 */
export function carryConfig(fromType, toType, config) {
  const next = defaultConfig(toType);
  const oldFields = Object.fromEntries((WIDGET_TYPES[fromType]?.fields || []).map((f) => [f.key, f]));
  for (const f of WIDGET_TYPES[toType]?.fields || []) {
    const old = oldFields[f.key];
    const v = config?.[f.key];
    if (!old || v === undefined || old.type !== f.type) continue;
    let ok;
    if (f.type === "select") ok = (f.options || []).some((o) => (typeof o === "object" ? o.value : o) === v);
    else if (f.type === "checkbox") ok = typeof v === "boolean";
    else if (f.type === "number" || f.type === "range") ok = typeof v === "number" && Number.isFinite(v) && (f.min == null || v >= f.min) && (f.max == null || v <= f.max);
    else if (f.type === "integration") ok = old.integrationType === f.integrationType && old.capability === f.capability && !!(f.integrationType || f.capability);
    else if (f.type === "text" || f.type === "textarea") ok = typeof v === "string";
    else ok = false;
    if (ok) next[f.key] = v;
  }
  return next;
}

export const defaultConfig = (type) =>
  Object.fromEntries((WIDGET_TYPES[type]?.fields || []).filter((f) => f.default !== undefined).map((f) => [f.key, f.default]));

export function RenderWidget({ widget, services, health, history, integrations = [], integrationData = {}, onRefresh }) {
  const def = WIDGET_TYPES[widget.type];
  if (!def) return <div className="muted pad">Unknown widget type: {widget.type}</div>;
  const service = widget.service_id ? services.find((s) => s.id === widget.service_id) : null;
  return (
    <ErrorBoundary
      resetKey={widget}
      fallback={(e) => (
        <div className="muted pad" title={String(e?.message ?? e)}>This widget failed to render ({widget.type})</div>
      )}
    >
      <def.Render
        widget={widget}
        service={service}
        services={services}
        health={health}
        history={history}
        integrations={integrations}
        integrationData={integrationData}
        onRefresh={onRefresh}
      />
    </ErrorBoundary>
  );
}
