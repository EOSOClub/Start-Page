import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { PRESS_LABEL, isOn, isUnavailable, pickEntities, toggledState } from "./actionHelpers.js";
import { ErrorLine } from "./actionControls.jsx";
import { Header, Problem, useIntegration } from "./integrations.jsx";
import { formatValue } from "./integrationViz.js";

/* Home Assistant: the entities of a home_assistant integration as switches, press buttons and read-only values. */

// States come from remote data: coerce, never assume a string.
const asText = (state) => (state == null || typeof state === "object" ? "" : String(state));
const pretty = (state) => {
  const s = asText(state);
  return s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : "";
};

/** After a successful action the optimistic value stays this long unless a newer poll (or the matching state) arrives first. */
export const HOLD_MS = 5000;

function Entity({ e, state: rawState, canWrite, busy, onToggle, onPress }) {
  const state = asText(rawState);
  // a button reads "unknown" until it is first pressed, which is not a fault
  const unavailable = isUnavailable(state) && !(e.domain === "button" && state === "unknown");
  let control;
  if (unavailable) {
    control = <span className="ha-state">unavailable</span>;
  } else if (e.toggleable && canWrite) {
    const on = isOn(state);
    control = (
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={e.label}
        className={`ha-switch${on ? " on" : ""}`}
        disabled={busy}
        onClick={() => onToggle(e)}
      >
        <i />
      </button>
    );
  } else if (PRESS_LABEL[e.domain] && canWrite) {
    control = (
      <button type="button" className="btn-sm" disabled={busy} aria-label={`${PRESS_LABEL[e.domain]} ${e.label}`} onClick={() => onPress(e)}>
        {PRESS_LABEL[e.domain]}
      </button>
    );
  } else {
    const n = e.unit ? Number(state) : NaN;
    control = (
      <span className="ha-state">
        {Number.isFinite(n) ? formatValue(n) : e.toggleable || PRESS_LABEL[e.domain] ? pretty(state) : state}
        {e.unit ? <small className="muted"> {e.unit}</small> : null}
      </span>
    );
  }
  return (
    <div className={`ha-entity${unavailable ? " ha-unavailable" : ""}`} title={`${e.entity_id}: ${state}`}>
      <span className="ha-label">{e.label}</span>
      {control}
    </div>
  );
}

export const HomeAssistantWidget = {
  label: "Home Assistant",
  group: "Integrations",
  size: { w: 8, h: 5 },
  fields: [
    { key: "integration_id", label: "Home Assistant integration", type: "integration", integrationType: "home_assistant" },
    { key: "title", label: "Title", type: "text", placeholder: "(integration name)" },
    {
      key: "layout",
      label: "Layout",
      type: "select",
      default: "grid",
      options: [
        { value: "grid", label: "Grid of cards" },
        { value: "list", label: "List" },
      ],
    },
    { key: "columns", label: "Columns (grid)", type: "number", min: 1, max: 12, placeholder: "auto", showIf: (c) => (c.layout ?? "grid") === "grid" },
    { key: "compact", label: "Compact", type: "checkbox" },
    {
      key: "entity_ids",
      label: "Only these entities (optional, in this order)",
      type: "textarea",
      rows: 3,
      placeholder: "light.kitchen\nsensor.outside_temperature",
      help: "Entity ids, one per line or comma separated, from the integration's entity list. Empty = all of them.",
    },
  ],
  Render: ({ widget, integrations, integrationData, onRefresh }) => {
    const { integ, data } = useIntegration(widget, integrations, integrationData, "home_assistant");
    const { canWrite = true } = useApp();
    const [over, setOver] = useState({}); // optimistic states by entity id: {value, doneAt: ms | null (request still running)}
    const timers = useRef(new Set());
    useEffect(() => () => timers.current.forEach(clearTimeout), []);
    const [busy, setBusy] = useState({}); // entity ids with a request in flight
    const [error, setError] = useState("");
    const c = widget.config;
    if (!integ || !data || (!data.ok && !data.data)) return <Problem integ={integ} data={data} type="Home Assistant" />;
    if (!Array.isArray(data.data?.entities)) return <div className="muted pad">{integ.name} does not provide entities.</div>;
    const entities = pickEntities(data.data.entities, c.entity_ids);

    const drop = (id) => setOver(({ [id]: _drop, ...rest }) => rest);
    const run = async (e, name, optimistic) => {
      const id = e.entity_id;
      setBusy((b) => ({ ...b, [id]: true }));
      setError("");
      if (optimistic) setOver((o) => ({ ...o, [id]: { value: optimistic, doneAt: null } }));
      let ok = false;
      try {
        await api.integrationAction(integ.id, name, { entity_id: id });
        ok = true;
        await onRefresh?.();
      } catch (err) {
        setError(`${e.label}: ${err.message}`);
      } finally {
        setBusy(({ [id]: _drop, ...rest }) => rest);
        if (!ok) drop(id); // the action failed: roll back at once
        else if (optimistic) {
          // A poll that started before the action may land after the refresh and carry the old state: keep the
          // optimistic value until a poll that began after the action arrives (see shown()), or HOLD_MS passed.
          setOver((o) => (o[id] ? { ...o, [id]: { ...o[id], doneAt: Date.now() } } : o));
          const t = setTimeout(() => { timers.current.delete(t); drop(id); }, HOLD_MS);
          timers.current.add(t);
        }
      }
    };
    // fetched_at is the server's clock in seconds: the data was fetched after the action completed when it is newer.
    const fetchedMs = typeof data.fetched_at === "number" ? (data.fetched_at < 1e12 ? data.fetched_at * 1000 : data.fetched_at) : 0;
    const shown = (e) => {
      const o = over[e.entity_id];
      if (!o) return e.state;
      if (o.doneAt && (e.state === o.value || fetchedMs > o.doneAt)) return e.state; // the data caught up
      return o.value;
    };

    const layout = c.layout ?? "grid";
    const style = layout === "grid" && c.columns ? { gridTemplateColumns: `repeat(${c.columns}, minmax(0, 1fr))` } : undefined;
    return (
      <div className="group">
        <Header title={c.title || integ.name} right={!data.ok ? <span className="muted">stale</span> : null} />
        <div className="scroll">
          {entities.length === 0 && <div className="muted">No entities to show.</div>}
          <div className={`ha-grid ha-layout-${layout}${c.compact ? " ha-compact" : ""}`} style={style}>
            {entities.map((e) => (
              <Entity
                key={e.entity_id}
                e={e}
                state={shown(e)}
                canWrite={canWrite}
                busy={!!busy[e.entity_id]}
                onToggle={(x) => run(x, "toggle", toggledState({ ...x, state: shown(x) }))}
                onPress={(x) => run(x, "press")}
              />
            ))}
          </div>
        </div>
        <ErrorLine error={error} />
      </div>
    );
  },
};
