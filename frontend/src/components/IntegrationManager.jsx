import { useState } from "react";
import Modal from "./Modal.jsx";
import ConfigForm from "./ConfigForm.jsx";
import { api } from "../api.js";

const TYPE_FIELDS = {
  docker: [
    {
      key: "url",
      label: "Docker API",
      type: "text",
      placeholder: "unix:///var/run/docker.sock  or  http://192.168.1.20:2375",
      help: "Socket path works when this backend runs on the Docker host (mount /var/run/docker.sock into the container). Otherwise expose the Docker TCP API or use a socket proxy.",
    },
    { key: "show_all", label: "Include stopped containers", type: "checkbox" },
  ],
  uptime_kuma: [
    { key: "url", label: "Uptime Kuma URL", type: "text", placeholder: "http://192.168.1.20:3001" },
    { key: "slug", label: "Status page slug", type: "text", placeholder: "default", help: "Create a status page in Kuma and add the monitors you want to show; no API key needed." },
  ],
  json: [
    { key: "url", label: "URL", type: "text", placeholder: "http://192.168.1.25:8080/status.json" },
    { key: "method", label: "Method", type: "select", options: [{ value: "GET", label: "GET" }, { value: "POST", label: "POST" }] },
    { key: "headers_text", label: "Headers (one per line: Name: value)", type: "textarea", rows: 3, placeholder: "Authorization: Bearer …" },
  ],
};

const TYPE_LABEL = { docker: "Docker", uptime_kuma: "Uptime Kuma", json: "JSON endpoint" };

const BASE_FIELDS = [
  { key: "name", label: "Name", type: "text" },
  {
    key: "type",
    label: "Type",
    type: "select",
    options: Object.entries(TYPE_LABEL).map(([value, label]) => ({ value, label })),
  },
  { key: "interval", label: "Poll interval (s)", type: "number" },
  { key: "enabled", label: "Enabled", type: "checkbox" },
];

const EMPTY = { name: "", type: "docker", interval: 30, enabled: true, config: { show_all: true, method: "GET" } };

function toForm(i) {
  const cfg = { ...(i.config || {}) };
  if (cfg.headers && !cfg.headers_text) cfg.headers_text = Object.entries(cfg.headers).map(([k, v]) => `${k}: ${v}`).join("\n");
  return { ...i, config: cfg };
}

function fromForm(form) {
  const { id, ...data } = form;
  const cfg = { ...data.config };
  if (cfg.headers_text !== undefined) {
    cfg.headers = Object.fromEntries(
      cfg.headers_text.split("\n").map((l) => l.split(/:(.*)/s)).filter((p) => p[0]?.trim()).map((p) => [p[0].trim(), (p[1] || "").trim()])
    );
    delete cfg.headers_text;
  }
  return { id, data: { ...data, config: cfg } };
}

export default function IntegrationManager({ integrations, integrationData, onChanged, onClose }) {
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setError("");
    const { id, data } = fromForm(editing);
    if (!data.name || !data.config?.url) {
      setError("Name and URL are required.");
      return;
    }
    setBusy(true);
    try {
      if (id) await api.updateIntegration(id, data);
      else await api.createIntegration(data);
      await onChanged();
      setEditing(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (i) => {
    if (!confirm(`Delete integration "${i.name}"? Widgets using it will show an error.`)) return;
    await api.deleteIntegration(i.id);
    await onChanged();
  };

  if (editing) {
    const fields = TYPE_FIELDS[editing.type] || [];
    return (
      <Modal
        title={editing.id ? `Edit ${editing.name}` : "New integration"}
        onClose={() => setEditing(null)}
        footer={
          <>
            {error && <span className="error">{error}</span>}
            <span className="spacer" />
            <button onClick={() => setEditing(null)}>Back</button>
            <button className="btn-primary" disabled={busy} onClick={save}>{busy ? "Testing…" : "Save"}</button>
          </>
        }
      >
        <ConfigForm fields={BASE_FIELDS} value={editing} onChange={setEditing} />
        <div style={{ height: 12 }} />
        <ConfigForm fields={fields} value={editing.config} onChange={(cfg) => setEditing({ ...editing, config: cfg })} />
      </Modal>
    );
  }

  return (
    <Modal
      title="Integrations"
      onClose={onClose}
      wide
      footer={
        <>
          <span className="muted">{integrations.length} integrations</span>
          <span className="spacer" />
          <button className="btn-primary" onClick={() => setEditing({ ...EMPTY })}>+ New integration</button>
        </>
      }
    >
      {integrations.length === 0 && (
        <p className="muted">
          Integrations are data sources the backend polls — Docker, Uptime Kuma status pages, or any JSON endpoint. Widgets like
          “Docker”, “Uptime Kuma” and “Custom status” read from them.
        </p>
      )}
      <table className="svc-table">
        <tbody>
          {integrations.map((i) => {
            const d = integrationData[i.id];
            const st = !i.enabled ? "disabled" : !d ? "unknown" : d.ok ? "online" : "offline";
            return (
              <tr key={i.id}>
                <td><span className={`status-dot status-${st}`} title={d?.error || ""} /></td>
                <td><strong>{i.name}</strong></td>
                <td className="muted">{TYPE_LABEL[i.type] || i.type}</td>
                <td className="muted">{i.config?.url}</td>
                <td className="muted">{d?.error ? <span className="error">{d.error}</span> : d?.fetched_at ? `updated ${Math.round((Date.now() / 1000 - d.fetched_at))}s ago` : ""}</td>
                <td className="right nowrap">
                  <button className="btn-sm" onClick={() => api.refreshIntegration(i.id).then(onChanged)} title="Refresh now">↻</button>{" "}
                  <button className="btn-sm" onClick={() => setEditing(toForm(i))}>Edit</button>{" "}
                  <button className="btn-sm btn-danger" onClick={() => remove(i)}>✕</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Modal>
  );
}
