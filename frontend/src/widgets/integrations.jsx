import { useState } from "react";
import DOMPurify from "dompurify";
import Icon from "./Icon.jsx";
import { api } from "../api.js";
import { TRANSPARENT_FIELD } from "./startpage.jsx";

/* Widgets backed by Integrations (Docker, Uptime Kuma, JSON) and service dependencies. */

function useIntegration(widget, integrations, integrationData, type) {
  const id = widget.config.integration_id;
  const integ = integrations.find((i) => i.id === id && (!type || i.type === type));
  const data = id ? integrationData[id] : null;
  return { integ, data };
}

function Header({ title, right }) {
  return (
    <div className="group-title row">
      <span>{title}</span>
      <span className="spacer" />
      {right}
    </div>
  );
}

function Problem({ integ, data, type }) {
  if (!integ) return <div className="muted pad">Select a {type} integration in widget settings.</div>;
  if (!data) return <div className="muted pad">Waiting for first fetch…</div>;
  if (!data.ok && !data.data) return <div className="pad error">Error: {data.error}</div>;
  return null;
}

// ---------- Docker ----------
export const DockerWidget = {
  label: "Docker",
  size: { w: 8, h: 6 },
  fields: [
    { key: "integration_id", label: "Docker integration", type: "integration", integrationType: "docker" },
    { key: "title", label: "Title", type: "text", placeholder: "(host name)" },
    { key: "project", label: "Filter by compose project", type: "text", placeholder: "(all)" },
    { key: "compact", label: "Compact (counts only)", type: "checkbox" },
    { key: "allowActions", label: "Show start/stop/restart buttons", type: "checkbox" },
  ],
  Render: ({ widget, integrations, integrationData, onRefresh }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData, "docker");
    const [busy, setBusy] = useState("");
    const problem = <Problem integ={integ} data={data} type="Docker" />;
    if (!integ || !data || (!data.ok && !data.data)) return problem;
    const d = data.data;
    const c = widget.config;
    const rows = c.project ? d.containers.filter((x) => x.project === c.project) : d.containers;
    const act = async (id, action) => {
      if (action !== "start" && !confirm(`${action} container ${id}?`)) return;
      setBusy(id + action);
      try {
        await api.dockerAction(integ.id, id, action);
        await onRefresh?.();
      } catch (e) {
        alert(e.message);
      } finally {
        setBusy("");
      }
    };
    const running = rows.filter((r) => r.state === "running").length;
    return (
      <div className="group">
        <Header
          title={c.title || `🐳 ${d.host || integ.name}`}
          right={<span className="muted">{running}/{rows.length} running{!data.ok ? " · stale" : ""}</span>}
        />
        {!c.compact && (
          <div className="scroll">
            <table className="svc-table">
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td><span className={`status-dot ${r.state === "running" ? "status-online" : r.state === "paused" ? "status-degraded" : "status-offline"}`} /></td>
                    <td><strong>{r.name}</strong>{r.project && <span className="muted"> · {r.project}</span>}</td>
                    <td className="muted">{r.status}</td>
                    <td className="muted nowrap">{r.ports.join(", ")}</td>
                    {c.allowActions && (
                      <td className="right nowrap">
                        {r.state === "running" ? (
                          <>
                            <button className="btn-sm" disabled={!!busy} onClick={() => act(r.id, "restart")} title="Restart">↻</button>{" "}
                            <button className="btn-sm" disabled={!!busy} onClick={() => act(r.id, "stop")} title="Stop">■</button>
                          </>
                        ) : (
                          <button className="btn-sm" disabled={!!busy} onClick={() => act(r.id, "start")} title="Start">▶</button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {c.compact && (
          <div className="stats">
            <div><b>{running}</b><span>running</span></div>
            <div><b>{rows.length - running}</b><span>not running</span></div>
            <div><b>{rows.length}</b><span>total</span></div>
          </div>
        )}
      </div>
    );
  },
};

// ---------- Uptime Kuma ----------
export const UptimeKumaWidget = {
  label: "Uptime Kuma",
  size: { w: 6, h: 5 },
  fields: [
    { key: "integration_id", label: "Uptime Kuma integration", type: "integration", integrationType: "uptime_kuma" },
    { key: "title", label: "Title", type: "text", placeholder: "(status page title)" },
    { key: "group", label: "Filter by group", type: "text", placeholder: "(all)" },
    { key: "showHistory", label: "Show heartbeat bars", type: "checkbox" },
  ],
  Render: ({ widget, integrations, integrationData }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData, "uptime_kuma");
    const problem = <Problem integ={integ} data={data} type="Uptime Kuma" />;
    if (!integ || !data || (!data.ok && !data.data)) return problem;
    const d = data.data;
    const c = widget.config;
    const rows = c.group ? d.monitors.filter((m) => m.group === c.group) : d.monitors;
    return (
      <div className="group">
        <Header title={c.title || d.title || integ.name} right={<span className="muted">{rows.filter((m) => m.status === "online").length}/{rows.length} up</span>} />
        <div className="scroll">
          {rows.map((m) => (
            <div key={m.id} className="kuma-row">
              <span className={`status-dot status-${m.status === "maintenance" ? "degraded" : m.status}`} />
              <span className="kuma-name">{m.name}</span>
              {c.showHistory && (
                <span className="beats">
                  {m.history.map((s, i) => <i key={i} className={`beat beat-${s}`} />)}
                </span>
              )}
              <span className="muted nowrap">{m.ping != null ? `${m.ping} ms` : ""}</span>
              <span className="muted nowrap">{m.uptime_24h != null ? `${(m.uptime_24h * 100).toFixed(1)}%` : ""}</span>
            </div>
          ))}
        </div>
      </div>
    );
  },
};

// ---------- JSON status ----------
function getPath(obj, path) {
  if (!path) return obj;
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

export const StatusWidget = {
  label: "Custom status (JSON)",
  size: { w: 4, h: 3 },
  fields: [
    { key: "integration_id", label: "JSON integration", type: "integration", integrationType: "json" },
    { key: "title", label: "Title", type: "text" },
    { key: "icon", label: "Icon", type: "icon" },
    { key: "link", label: "Link to", type: "text" },
    {
      key: "fields",
      label: "Fields (one per line: Label = json.path)",
      type: "textarea",
      placeholder: "Players = players.online\nTPS = tps",
      help: "Dot-separated path into the JSON response. Leave path empty to show the whole response.",
    },
    { key: "onlineIf", label: "Online if path is truthy", type: "text", placeholder: "e.g. online" },
  ],
  Render: ({ widget, integrations, integrationData }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData, "json");
    const c = widget.config;
    const problem = <Problem integ={integ} data={data} type="JSON" />;
    if (!integ || !data || (!data.ok && !data.data)) return problem;
    const d = data.data;
    const lines = (c.fields || "")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        const [label, ...rest] = l.split("=");
        return { label: label.trim(), path: rest.join("=").trim() };
      });
    const fmt = (v) => (v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
    const online = c.onlineIf ? !!getPath(d, c.onlineIf) : data.ok;
    const body = (
      <div className="tile">
        <div className="tile-head">
          <Icon icon={c.icon} fallback={c.title || integ.name} size={32} />
          <div className="tile-name"><span className={`status-dot ${online ? "status-online" : "status-offline"}`} />{c.title || integ.name}</div>
        </div>
        <div className="kv">
          {lines.length === 0 && <pre className="muted">{fmt(d).slice(0, 400)}</pre>}
          {lines.map((f) => (
            <div key={f.label}><span className="muted">{f.label}</span><b>{fmt(getPath(d, f.path))}</b></div>
          ))}
        </div>
      </div>
    );
    return c.link ? <a href={c.link} draggable={false} style={{ display: "block", height: "100%" }}>{body}</a> : body;
  },
};

// ---------- System stats (host) ----------
function fmtBytes(b) {
  if (b == null) return "—";
  if (b >= 1e12) return `${(b / 1e12).toFixed(1)} TB`;
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(1)} MB`;
  return `${(b / 1e3).toFixed(0)} KB`;
}

export const SystemWidget = {
  label: "System stats",
  size: { w: 4, h: 3 },
  fields: [
    { key: "integration_id", label: "Host integration", type: "integration", integrationType: "host" },
    { key: "title", label: "Title", type: "text", placeholder: "(hostname)" },
  ],
  Render: ({ widget, integrations, integrationData }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData, "host");
    const c = widget.config;
    const problem = <Problem integ={integ} data={data} type="System" />;
    if (!integ || !data || (!data.ok && !data.data)) return problem;
    const d = data.data;
    return (
      <div className="group">
        <Header title={c.title || (d.hostname ?? "—")} right={!data.ok ? <span className="muted">stale</span> : null} />
        <div className="stats">
          <div><b>{d.cpu_percent != null ? `${d.cpu_percent.toFixed(1)}%` : "—"}</b><span>CPU</span></div>
          <div><b>{d.mem_used != null ? `${fmtBytes(d.mem_used)} / ${fmtBytes(d.mem_total)}` : "—"}</b><span>RAM</span></div>
          <div><b>{d.disk_used != null ? `${fmtBytes(d.disk_used)} / ${fmtBytes(d.disk_total)}` : "—"}</b><span>Disk</span></div>
        </div>
      </div>
    );
  },
};

// ---------- RSS / Atom ----------
const RSS_LINK = /^https?:/i; // only these may become <a href> — anything else renders as plain text

export const RssWidget = {
  label: "RSS / Atom",
  size: { w: 6, h: 5 },
  fields: [
    { key: "integration_id", label: "RSS/Atom integration", type: "integration", integrationType: "rss" },
    { key: "title", label: "Title", type: "text", placeholder: "(feed title)" },
    { key: "max_items", label: "Max items", type: "number", default: 10 },
    { key: "show_description", label: "Show description", type: "checkbox" },
    { key: "newTab", label: "Open links in new tab", type: "checkbox" },
    TRANSPARENT_FIELD,
  ],
  Render: ({ widget, integrations, integrationData }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData, "rss");
    const c = widget.config;
    const problem = <Problem integ={integ} data={data} type="RSS" />;
    if (!integ || !data || (!data.ok && !data.data)) return problem;
    const d = data.data;
    const entries = (d.entries || []).slice(0, Math.max(1, c.max_items ?? 10));
    return (
      <div className="group">
        <Header title={c.title || d.title || integ.name} right={!data.ok ? <span className="muted">stale</span> : null} />
        <div className="scroll">
          {entries.length === 0 && <div className="muted">No items yet.</div>}
          {entries.map((e, i) => (
            <div key={e.link || i}>
              <div className="kuma-row">
                {RSS_LINK.test(e.link || "") ? (
                  <a className="kuma-name" href={e.link} target={c.newTab ? "_blank" : "_self"} rel="noreferrer" draggable={false}>
                    {e.title || "(untitled)"}
                  </a>
                ) : (
                  <span className="kuma-name">{e.title || "(untitled)"}</span>
                )}
                <span className="muted nowrap">{e.published_parsed != null ? new Date(e.published_parsed * 1000).toLocaleDateString() : ""}</span>
              </div>
              {c.show_description && e.summary && (
                <div className="muted" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(e.summary) }} />
              )}
            </div>
          ))}
        </div>
      </div>
    );
  },
};

// ---------- Topology (service dependencies) ----------
export const TopologyWidget = {
  label: "Topology (dependencies)",
  size: { w: 6, h: 6 },
  fields: [
    { key: "title", label: "Title", type: "text" },
    { key: "root", label: "Root service", type: "service", placeholder: "(all roots)" },
    { key: "newTab", label: "Open in new tab", type: "checkbox" },
  ],
  Render: ({ widget, services, health }) => {
    const c = widget.config;
    const children = (id) => services.filter((s) => (s.depends_on || []).includes(id));
    const roots = c.root
      ? services.filter((s) => s.id === c.root)
      : services.filter((s) => !(s.depends_on || []).some((p) => services.some((x) => x.id === p)));
    const Node = ({ s, depth, seen }) => {
      if (seen.has(s.id) || depth > 8) return null;
      const next = new Set(seen).add(s.id);
      const h = health[s.id] ?? { status: s.health_enabled ? "unknown" : "disabled" };
      return (
        <li>
          <a href={s.url} target={c.newTab ? "_blank" : "_self"} rel="noreferrer" draggable={false} className="topo-node">
            <span className={`status-dot status-${h.status}`} />
            <Icon icon={s.icon} fallback={s.name} size={18} />
            <span>{s.name}</span>
            <span className="muted">{s.host}</span>
          </a>
          {children(s.id).length > 0 && (
            <ul>{children(s.id).map((k) => <Node key={k.id} s={k} depth={depth + 1} seen={next} />)}</ul>
          )}
        </li>
      );
    };
    return (
      <div className="group">
        {c.title && <Header title={c.title} />}
        <div className="scroll">
          {roots.length === 0 && <div className="muted">No services. Set “Depends on” in the service registry to build a tree.</div>}
          <ul className="topo">{roots.map((r) => <Node key={r.id} s={r} depth={0} seen={new Set()} />)}</ul>
        </div>
      </div>
    );
  },
};
