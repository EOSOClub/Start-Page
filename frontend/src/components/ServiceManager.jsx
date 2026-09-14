import { useState } from "react";
import Modal from "./Modal.jsx";
import ConfigForm from "./ConfigForm.jsx";
import Icon from "../widgets/Icon.jsx";
import { api } from "../api.js";

const SERVICE_FIELDS = [
  { key: "name", label: "Name", type: "text" },
  { key: "category", label: "Category", type: "category", placeholder: "e.g. Media, Servers, Network" },
  {
    key: "protocol",
    label: "Protocol",
    type: "select",
    options: [
      { value: "http", label: "http" },
      { value: "https", label: "https" },
    ],
  },
  { key: "host", label: "Host / IP", type: "text", placeholder: "192.168.1.20" },
  { key: "port", label: "Port", type: "number", placeholder: "(default)" },
  { key: "path", label: "Path", type: "text", placeholder: "/" },
  { key: "icon", label: "Icon", type: "icon" },
  { key: "color", label: "Accent color", type: "color" },
  { key: "description", label: "Description", type: "text" },
  { key: "health_enabled", label: "Enable health check", type: "checkbox" },
  {
    key: "health_type",
    label: "Check type",
    type: "select",
    showIf: (v) => v.health_enabled,
    options: [
      { value: "http", label: "HTTP GET (any response < 500 = online)" },
      { value: "tcp", label: "TCP connect to host:port" },
      { value: "ping", label: "ICMP ping host" },
    ],
  },
  { key: "health_url", label: "Health URL override", type: "text", placeholder: "(defaults to service URL)", showIf: (v) => v.health_enabled && (v.health_type ?? "http") === "http" },
  { key: "health_interval", label: "Check interval (s)", type: "number", showIf: (v) => v.health_enabled },
  { key: "depends_on", label: "Depends on (parents)", type: "services", help: "Used by the Topology widget to draw the dependency tree, e.g. Plex depends on Docker host." },
];

const EMPTY = { name: "", category: "", protocol: "http", host: "", port: null, path: "", icon: "", color: "", description: "", health_enabled: true, health_type: "http", health_url: "", health_interval: 30, depends_on: [] };

function StatusDot({ status }) {
  const s = status?.status ?? "unknown";
  if (s === "disabled") return <span className="status-dot status-disabled" />;
  return <span className={`status-dot status-${s}`} title={status?.error || ""} />;
}

export default function ServiceManager({ services, health, onChanged, onClose, title = "Service registry" }) {
  const [editing, setEditing] = useState(null); // null | {…service} (id absent = new)
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");

  const save = async () => {
    setError("");
    const { id, url, ...data } = editing;
    if (!data.name || !data.host) {
      setError("Name and host are required.");
      return;
    }
    try {
      if (id) await api.updateService(id, data);
      else await api.createService(data);
      await onChanged();
      setEditing(null);
    } catch (e) {
      setError(e.message);
    }
  };

  const remove = async (svc) => {
    if (!confirm(`Delete service "${svc.name}"? Widgets using it will lose their link.`)) return;
    await api.deleteService(svc.id);
    await onChanged();
  };

  const q = filter.toLowerCase();
  const rows = services.filter((s) => !q || `${s.name} ${s.category} ${s.host}`.toLowerCase().includes(q));
  const grouped = rows.reduce((acc, s) => ((acc[s.category || ""] ||= []).push(s), acc), {});

  if (editing) {
    return (
      <Modal
        title={editing.id ? `Edit ${editing.name}` : "New service"}
        onClose={() => setEditing(null)}
        footer={
          <>
            {error && <span className="error">{error}</span>}
            <span className="spacer" />
            <button onClick={() => setEditing(null)}>Back</button>
            <button className="btn-primary" onClick={save}>Save</button>
          </>
        }
      >
        <ConfigForm fields={SERVICE_FIELDS.map((f) => (f.key === "depends_on" ? { ...f, exclude: editing.id } : f))} value={editing} onChange={setEditing} services={services} />
        <div className="help">
          Resolved URL: <code>{`${editing.protocol}://${editing.host || "…"}${editing.port ? `:${editing.port}` : ""}${editing.path || ""}`}</code>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      wide
      footer={
        <>
          <span className="muted">{services.length} services</span>
          <span className="spacer" />
          <button className="btn-primary" onClick={() => setEditing({ ...EMPTY })}>+ New service</button>
        </>
      }
    >
      <input className="search" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} autoFocus />
      {rows.length === 0 && <p className="muted">No services yet. Add one — widgets reference services, so changing an IP here updates every widget that uses it.</p>}
      {Object.entries(grouped).sort().map(([cat, list]) => (
        <div key={cat} className="svc-group">
          <div className="svc-cat">{cat || "Uncategorised"}</div>
          <table className="svc-table">
            <tbody>
              {list.map((s) => (
                <tr key={s.id}>
                  <td><StatusDot status={health[s.id]} /></td>
                  <td><Icon icon={s.icon} fallback={s.name} size={22} /></td>
                  <td><strong>{s.name}</strong></td>
                  <td className="muted"><a href={s.url} target="_blank" rel="noreferrer">{s.url}</a></td>
                  <td className="muted right">{health[s.id]?.latency_ms != null ? `${health[s.id].latency_ms} ms` : ""}</td>
                  <td className="right nowrap">
                    <button className="btn-sm" onClick={() => api.checkHealth(s.id).then(onChanged)} title="Check now">↻</button>{" "}
                    <button className="btn-sm" onClick={() => setEditing({ ...EMPTY, ...s })}>Edit</button>{" "}
                    <button className="btn-sm btn-danger" onClick={() => remove(s)}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </Modal>
  );
}
