import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Modal from "./Modal.jsx";
import Icon from "../widgets/Icon.jsx";
import { api } from "../api.js";

const CONF_LABEL = { label: "label", traefik: "traefik", port: "port", guess: "guess" };
const EDIT_KEYS = ["name", "category", "protocol", "host", "port", "path"];

const hasChanges = (c) => !!c.existing_service_id && !!c.changes;
const defaultChecked = (c) => !c.existing_service_id && c.confidence !== "guess";
const urlOf = (e) => `${e.protocol}://${e.host || "…"}${e.port ? `:${e.port}` : ""}${e.path || ""}`;

/** An inline message for a port that is not empty and not a whole number from 1 to 65535, else "". */
export function portProblem(port) {
  const s = String(port ?? "").trim();
  if (s === "") return "";
  return /^\d+$/.test(s) && +s >= 1 && +s <= 65535 ? "" : "Port must be a number from 1 to 65535";
}

/**
 * Fields sent back for one candidate: everything for a new service. For an update, the fields the discovery
 * reported as changed plus every field the user edited since (an edited name or category must not be dropped).
 */
export function buildFields(c, e, orig = e) {
  const all = {
    name: e.name.trim(),
    protocol: e.protocol,
    host: e.host.trim(),
    port: String(e.port ?? "").trim() === "" ? null : Number(e.port),
    path: e.path.trim(),
    category: e.category.trim(),
    icon: c.icon,
    description: c.description,
    health_enabled: c.health.enabled,
    health_type: c.health.type,
  };
  if (!c.existing_service_id) return all;
  const edited = EDIT_KEYS.filter((k) => String(e[k] ?? "").trim() !== String(orig[k] ?? "").trim());
  const keys = new Set([...Object.keys(c.changes || {}), ...edited]);
  return Object.fromEntries([...keys].filter((k) => k in all).map((k) => [k, all[k]]));
}

function Row({ c, edit, checked, onCheck, onEdit }) {
  const update = !!c.existing_service_id;
  const label = c.name;
  const disabled = update && !c.changes;
  return (
    <tr className={disabled ? "disc-row disc-done" : "disc-row"}>
      <td>
        <input type="checkbox" checked={checked && !disabled} disabled={disabled} onChange={(ev) => onCheck(ev.target.checked)} aria-label={`Import ${label}`} />
      </td>
      <td><Icon icon={c.icon} fallback={c.name} size={22} /></td>
      <td>
        <input className="disc-in" value={edit.name} onChange={(ev) => onEdit({ name: ev.target.value })} aria-label={`Name of ${label}`} />
        <input className="disc-in disc-sub" value={edit.category} placeholder="Category" onChange={(ev) => onEdit({ category: ev.target.value })} aria-label={`Category of ${label}`} />
      </td>
      <td>
        <div className="disc-url">
          <select value={edit.protocol} onChange={(ev) => onEdit({ protocol: ev.target.value })} aria-label={`Protocol of ${label}`}>
            <option value="http">http</option>
            <option value="https">https</option>
          </select>
          <input className="disc-in" value={edit.host} onChange={(ev) => onEdit({ host: ev.target.value })} aria-label={`Host of ${label}`} />
          <input className="disc-in disc-port" value={edit.port ?? ""} placeholder="port" inputMode="numeric" onChange={(ev) => onEdit({ port: ev.target.value })} aria-label={`Port of ${label}`} />
          <input className="disc-in disc-path" value={edit.path} placeholder="/path" onChange={(ev) => onEdit({ path: ev.target.value })} aria-label={`Path of ${label}`} />
        </div>
        {portProblem(edit.port) && <div className="error disc-sub" role="alert">{portProblem(edit.port)}</div>}
        <div className="muted disc-sub">{urlOf(edit)}</div>
        {update && (
          <div className="disc-changes">
            {c.changes
              ? Object.entries(c.changes).map(([k, [cur, next]]) => (
                  <div key={k}>
                    <strong>{k}</strong>: {String(cur ?? "") || "(empty)"} → {String(next ?? "") || "(empty)"}
                  </div>
                ))
              : "Up to date"}
          </div>
        )}
      </td>
      <td className="nowrap">
        <span className={`badge badge-${c.confidence}`} tabIndex={0} title={c.reasons.join("\n")} aria-label={`Confidence ${c.confidence}: ${c.reasons.join("; ")}`}>
          {CONF_LABEL[c.confidence] || c.confidence}
        </span>
        <div className="muted disc-sub">{c.source.container_name}</div>
      </td>
    </tr>
  );
}

function Group({ title, rows, state, setChecked, setEdit, setAll, empty }) {
  return (
    <section className="disc-group" aria-label={title}>
      <div className="disc-head">
        <span className="svc-cat">{title} ({rows.length})</span>
        {rows.length > 0 && (
          <span>
            <button className="btn-sm" onClick={() => setAll(rows, true)} aria-label={`Select all: ${title}`}>All</button>{" "}
            <button className="btn-sm" onClick={() => setAll(rows, false)} aria-label={`Select none: ${title}`}>None</button>
          </span>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="muted">{empty}</p>
      ) : (
        <table className="disc-table">
          <tbody>
            {rows.map((c) => (
              <Row key={c.key} c={c} edit={state[c.key].edit} checked={state[c.key].checked} onCheck={(v) => setChecked(c.key, v)} onEdit={(patch) => setEdit(c.key, patch)} />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function DiscoverPanel({ integrations, onBack, onImported }) {
  const [integrationId, setIntegrationId] = useState("");
  const [phase, setPhase] = useState("loading"); // loading | ready | error | importing | done
  const [error, setError] = useState("");
  const [data, setData] = useState({ candidates: [], skipped: [], errors: [] });
  const [state, setState] = useState({}); // key -> {checked, edit}
  const [result, setResult] = useState(null);
  const latest = useRef(0); // only the newest discovery request may fill the panel

  const load = useCallback(async () => {
    const mine = ++latest.current;
    setPhase("loading");
    setError("");
    try {
      const res = await api.discoverDocker(integrationId ? { integration_id: integrationId } : {});
      if (mine !== latest.current) return; // the integration select changed meanwhile
      setData(res);
      setState(
        Object.fromEntries(
          res.candidates.map((c) => [
            c.key,
            (() => {
              const edit = Object.fromEntries(EDIT_KEYS.map((k) => [k, k === "port" ? (c.port ?? "") : (c[k] ?? "")]));
              return { checked: defaultChecked(c), edit, orig: edit };
            })(),
          ])
        )
      );
      setPhase("ready");
    } catch (e) {
      if (mine !== latest.current) return;
      setError(e.message);
      setPhase("error");
    }
  }, [integrationId]);

  useEffect(() => {
    load();
  }, [load]);

  const fresh = useMemo(() => data.candidates.filter((c) => !c.existing_service_id), [data]);
  const existing = useMemo(() => data.candidates.filter((c) => c.existing_service_id), [data]);
  const selected = data.candidates.filter((c) => state[c.key]?.checked && !(c.existing_service_id && !c.changes));

  const badPorts = selected.filter((c) => portProblem(state[c.key].edit.port));

  const setChecked = (key, v) => setState((s) => ({ ...s, [key]: { ...s[key], checked: v } }));
  const setEdit = (key, patch) => setState((s) => ({ ...s, [key]: { ...s[key], edit: { ...s[key].edit, ...patch } } }));
  const setAll = (rows, v) => setState((s) => ({ ...s, ...Object.fromEntries(rows.filter((c) => !(c.existing_service_id && !c.changes)).map((c) => [c.key, { ...s[c.key], checked: v }])) }));

  const doImport = async () => {
    if (badPorts.length) return;
    setPhase("importing");
    setError("");
    const items = selected.map((c) => ({
      key: c.key,
      action: c.existing_service_id ? "update" : "create",
      ...(c.existing_service_id ? { service_id: c.existing_service_id } : {}),
      fields: buildFields(c, state[c.key].edit, state[c.key].orig),
    }));
    try {
      const res = await api.importDiscovered(items);
      setResult(res);
      setPhase("done");
      await onImported();
    } catch (e) {
      setError(e.message);
      setPhase("ready");
    }
  };

  const nameOf = (key) => data.candidates.find((c) => c.key === key)?.name || key;

  const footer = (
    <>
      {error && phase !== "error" && <span className="error">{error}</span>}
      <span className="spacer" />
      {phase === "done" ? (
        <>
          <button onClick={() => { setResult(null); load(); }}>Discover again</button>
          <button className="btn-primary" onClick={onBack}>Back to services</button>
        </>
      ) : (
        <>
          <button onClick={onBack}>Back</button>
          <button className="btn-primary" onClick={doImport} disabled={phase !== "ready" || selected.length === 0 || badPorts.length > 0} title={badPorts.length ? "Fix the port of the selected rows first" : undefined}>
            {phase === "importing" ? "Importing…" : `Import ${selected.length} selected`}
          </button>
        </>
      )}
    </>
  );

  return (
    <Modal title="Discover services" onClose={onBack} wide footer={footer}>
      <p className="help">
        Candidates come from the labels, Traefik rules and published ports of your Docker containers. Nothing is added until you import.
      </p>
      {integrations.length > 1 && (
        <div className="disc-pick">
          <label>
            Docker integration{" "}
            <select value={integrationId} onChange={(ev) => setIntegrationId(ev.target.value)}>
              <option value="">All ({integrations.length})</option>
              {integrations.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
            </select>
          </label>
        </div>
      )}
      {phase === "loading" && <p className="muted" role="status">Looking at your containers…</p>}
      {phase === "error" && (
        <div role="alert">
          <p className="error">Discovery failed: {error}</p>
          <button onClick={load}>Retry</button>
        </div>
      )}
      {phase === "done" && result && (
        <div role="status">
          <p>
            <strong>Created {result.created.length}, updated {result.updated.length}</strong>
            {result.errors.length > 0 && <span className="error">, {result.errors.length} failed</span>}.
          </p>
          {result.errors.length > 0 && (
            <ul className="disc-list">
              {result.errors.map((e) => <li key={e.key} className="error">{nameOf(e.key)}: {e.error}</li>)}
            </ul>
          )}
        </div>
      )}
      {(phase === "ready" || phase === "importing") && (
        <>
          {data.errors.map((e) => (
            <p key={e.integration_id} className="error" role="alert">
              {integrations.find((i) => i.id === e.integration_id)?.name || e.integration_id}: {e.error}
            </p>
          ))}
          <Group title="New" rows={fresh} state={state} setChecked={setChecked} setEdit={setEdit} setAll={setAll} empty="No new services found." />
          {existing.length > 0 && (
            <Group title="Already in your services (changes available)" rows={existing} state={state} setChecked={setChecked} setEdit={setEdit} setAll={setAll} empty="" />
          )}
          {data.skipped.length > 0 && (
            <details className="disc-skipped">
              <summary>Skipped containers ({data.skipped.length})</summary>
              <ul className="disc-list">
                {data.skipped.map((s, i) => <li key={`${s.container_name}-${i}`}><strong>{s.container_name}</strong>: <span className="muted">{s.reason}</span></li>)}
              </ul>
            </details>
          )}
        </>
      )}
    </Modal>
  );
}
