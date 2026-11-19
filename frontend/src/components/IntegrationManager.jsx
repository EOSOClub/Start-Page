import { useState } from "react";
import Modal from "./Modal.jsx";
import ConfigForm from "./ConfigForm.jsx";
import { api } from "../api.js";
import { useIntegrationTypes } from "../integrationTypes.js";

/*
 * The form is built from the backend's type specs (GET /api/integrations/types), so a new
 * integration type needs no change here. Secret fields are write-only: the server sends back the
 * mask for a saved value, and the form sends the mask unchanged to keep it.
 */
const MASK = "********";

const BASE_FIELDS = [
  { key: "name", label: "Name", type: "text" },
  { key: "interval", label: "Poll interval (s)", type: "number", min: 5 },
  { key: "enabled", label: "Enabled", type: "checkbox" },
];

const EMPTY = { name: "", type: "", interval: 30, enabled: true, config: {}, saved: [] };

const headersToText = (h) => Object.entries(h || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
const textToHeaders = (t) =>
  Object.fromEntries(
    t.split("\n").map((l) => l.split(/:(.*)/s)).filter((p) => p[0]?.trim()).map((p) => [p[0].trim(), (p[1] || "").trim()])
  );

/** Form fields for a type spec. `saved` lists the secret keys that already hold a value. */
export function formFields(spec, saved = []) {
  return (spec?.fields || []).map((f) => {
    if (f.type === "headers") {
      // A {name: value} dict is edited as "Name: value" lines (a secret as a whole).
      return { key: "headers_text", label: f.label, type: "secret", multiline: true, rows: 3, mask: f.mask ?? MASK, saved: saved.includes(f.key), placeholder: f.placeholder, help: f.help, noun: "headers" };
    }
    if (f.secret) return { ...f, type: "secret", multiline: f.type === "textarea", saved: saved.includes(f.key), noun: "value" };
    return f;
  });
}

function toForm(i, spec) {
  const config = { ...(i.config || {}) };
  const saved = [];
  for (const f of spec?.fields || []) {
    if (f.secret && config[f.key] === (f.mask ?? MASK)) saved.push(f.key);
    if (f.type === "headers") {
      config.headers_text = config[f.key]; // the mask, or "" when none is saved
      delete config[f.key];
    }
  }
  return { ...i, config, saved };
}

function fromForm(form, spec) {
  const { id, saved, ...data } = form;
  const config = { ...data.config };
  for (const f of spec?.fields || []) {
    if (f.type !== "headers") continue;
    const text = config.headers_text;
    delete config.headers_text;
    if (text === undefined) continue;
    config[f.key] = text === "" || text === (f.mask ?? MASK) ? text : textToHeaders(text);
  }
  return { id, data: { ...data, config } };
}

/** One line for a /test sample: the stats, or the item count, or a cut of the JSON. */
export function summarize(sample) {
  if (sample == null) return "";
  if (Array.isArray(sample?.stats) && sample.stats.length) {
    return sample.stats.map((s) => `${s.label}: ${s.value ?? "—"}${s.unit ? ` ${s.unit}` : ""}`).join(" · ");
  }
  if (Array.isArray(sample?.items) && sample.items.length) return sample.items.map((i) => i.title).join(" · ");
  const text = JSON.stringify(sample);
  return text.length > 240 ? `${text.slice(0, 240)}…` : text;
}

function TypePicker({ types, value, onChange }) {
  const sorted = [...types].sort((a, b) => a.label.localeCompare(b.label));
  return (
    <div className="type-picker" role="radiogroup" aria-label="Integration type">
      {sorted.map((t) => (
        <button key={t.type} type="button" role="radio" aria-checked={t.type === value} className={`type-option${t.type === value ? " on" : ""}`} onClick={() => onChange(t.type)}>
          <strong>{t.label}</strong>
          <span className="muted">{t.description}</span>
        </button>
      ))}
    </div>
  );
}

export default function IntegrationManager({ integrations, integrationData, onChanged, onClose }) {
  const { types, error: typesError } = useIntegrationTypes();
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState(null);

  const spec = (type) => types.find((t) => t.type === type);
  const typeLabel = (type) => spec(type)?.label || type;

  const save = async () => {
    setError("");
    const { id, data } = fromForm(editing, spec(editing.type));
    if (!data.name) {
      setError("Name is required.");
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

  const runTest = async () => {
    setError("");
    const { id, data } = fromForm(editing, spec(editing.type));
    setTest({ busy: true });
    try {
      setTest(await api.testIntegration({ type: data.type, config: data.config, ...(id ? { id } : {}) }));
    } catch (e) {
      setTest({ ok: false, error: e.message });
    }
  };

  const remove = async (i) => {
    if (!confirm(`Delete integration "${i.name}"? Widgets using it will show an error.`)) return;
    setError("");
    try {
      await api.deleteIntegration(i.id);
      await onChanged();
    } catch (e) {
      setError(`Could not delete "${i.name}": ${e.message}`);
    }
  };

  const edit = (next) => {
    setTest(null);
    setEditing(next);
  };

  const pickType = (type) =>
    edit({ ...editing, type, config: {}, saved: [], interval: spec(type)?.default_interval ?? 30 });

  if (editing) {
    const current = spec(editing.type);
    return (
      <Modal
        title={editing.id ? `Edit ${editing.name}` : "New integration"}
        onClose={() => edit(null)}
        footer={
          <>
            {error && <span className="error">{error}</span>}
            <span className="spacer" />
            <button onClick={() => edit(null)}>Back</button>
            <button disabled={!current || test?.busy} onClick={runTest}>{test?.busy ? "Testing…" : "Test"}</button>
            <button className="btn-primary" disabled={busy || !current} onClick={save}>{busy ? "Saving…" : "Save"}</button>
          </>
        }
      >
        {typesError && <p className="error">Could not load the integration types: {typesError}</p>}
        {!typesError && types.length === 0 && <p className="muted">Loading integration types…</p>}
        <TypePicker types={types} value={editing.type} onChange={pickType} />
        {current && (
          <>
            <div style={{ height: 12 }} />
            <ConfigForm fields={[BASE_FIELDS[0]]} value={editing} onChange={edit} />
            <ConfigForm fields={formFields(current, editing.saved)} value={editing.config} onChange={(config) => edit({ ...editing, config })} />
            <ConfigForm fields={BASE_FIELDS.slice(1)} value={editing} onChange={edit} />
            {test && !test.busy && (
              <div className="test-result" role="status">
                {test.ok ? (
                  <>
                    <strong>Works.</strong> <span className="muted">{summarize(test.sample)}</span>
                  </>
                ) : (
                  <span className="error">Failed: {test.error}</span>
                )}
              </div>
            )}
          </>
        )}
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
          {error && <span className="error">{error}</span>}
          <span className="muted">{integrations.length} integrations</span>
          <span className="spacer" />
          <button className="btn-primary" onClick={() => edit({ ...EMPTY })}>+ New integration</button>
        </>
      }
    >
      {integrations.length === 0 && (
        <p className="muted">
          Integrations are data sources the backend polls
          {types.length > 0 ? `: ${types.map((t) => t.label).join(", ")}` : ""}. Widgets read from them.
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
                <td className="muted">{typeLabel(i.type)}</td>
                <td className="muted">{i.config?.url}</td>
                <td className="muted">{d?.error ? <span className="error">{d.error}</span> : d?.fetched_at ? `updated ${Math.round((Date.now() / 1000 - d.fetched_at))}s ago` : ""}</td>
                <td className="right nowrap">
                  <button className="btn-sm" onClick={() => api.refreshIntegration(i.id).then(onChanged).catch((e) => setError(e.message))} title="Refresh now">↻</button>{" "}
                  <button className="btn-sm" disabled={types.length === 0} onClick={() => edit(toForm(i, spec(i.type)))}>Edit</button>{" "}
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
