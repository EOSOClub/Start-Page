import { useEffect, useRef, useState } from "react";
import { CONFIRM_MS } from "./actionHelpers.js";
import { api } from "../api.js";
import { useApp } from "../context.js";

/*
 * Controls for integration actions (POST /api/integrations/{id}/action/{name}). Every control hides
 * itself for view-only guests (canWrite false), disables while a request runs, and reports a failed
 * action in an inline alert line. Disruptive actions need a second click.
 */

/** {busy, error, run(name, payload) -> true | false}; refreshes the integration data after a success. */
export function useAction(integrationId, onRefresh) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const run = async (name, payload = {}) => {
    setBusy(true);
    setError("");
    try {
      await api.integrationAction(integrationId, name, payload);
      await onRefresh?.();
      return true;
    } catch (e) {
      if (alive.current) setError(e.message);
      return false;
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  return { busy, error, run, setError };
}

export const ErrorLine = ({ error }) => (error ? <div className="error" role="alert">{error}</div> : null);

/** A button that needs a second click: "Pause all" -> "Pause all?  [Yes] [No]" (disarms after a few seconds). */
export function ConfirmButton({ label, onConfirm, disabled, danger = false, title }) {
  const [armed, setArmed] = useState(false);
  const trigger = useRef(null);
  const group = useRef(null);
  const wasArmed = useRef(false);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), CONFIRM_MS);
    return () => clearTimeout(t);
  }, [armed]);
  // The armed button unmounts the trigger that had focus: move focus into the confirm group, and back to the trigger afterwards.
  useEffect(() => {
    if (armed) group.current?.querySelector("button")?.focus();
    else if (wasArmed.current && document.activeElement === document.body) trigger.current?.focus();
    wasArmed.current = armed;
  }, [armed]);
  if (!armed) {
    return (
      <button ref={trigger} type="button" className={`btn-sm${danger ? " btn-danger" : ""}`} disabled={disabled} title={title} onClick={() => setArmed(true)}>
        {label}
      </button>
    );
  }
  return (
    <span ref={group} className="confirm-inline" role="group" aria-label={`Confirm: ${label}`}>
      <span className="muted">{label}?</span>
      <button type="button" className="btn-sm btn-danger" disabled={disabled} onClick={() => { setArmed(false); onConfirm(); }}>Yes</button>
      <button type="button" className="btn-sm" onClick={() => setArmed(false)}>No</button>
    </span>
  );
}

/**
 * The Pi-hole / AdGuard blocking switch: status text plus enable / disable for 5 minutes / disable.
 * `action` is the integration action ("set_blocking" or "set_protection"), both take {enabled, seconds?}.
 */
export function BlockingBar({ integId, blocking, onRefresh, action = "set_blocking", noun = "blocking" }) {
  const { canWrite = true } = useApp(); // view-only guests get no switch
  const { busy, error, run } = useAction(integId, onRefresh);
  const enabled = blocking?.enabled;
  const timer = blocking?.timer;
  const set = (on, seconds) => run(action, { enabled: on, ...(seconds ? { seconds } : {}) });
  const Noun = noun[0].toUpperCase() + noun.slice(1);
  return (
    <>
      <div className="row pihole-bar">
        <span className={`status-dot status-${enabled === true ? "online" : enabled === false ? "degraded" : "unknown"}`} />
        <span className="muted">
          {enabled === true ? `${Noun} on` : enabled === false ? `${Noun} off${timer ? ` (${Math.ceil(timer / 60)} min left)` : ""}` : `${Noun} state unknown`}
        </span>
        <span className="spacer" />
        {canWrite && enabled !== true && <button className="btn-sm" disabled={busy} onClick={() => set(true)}>Enable {noun}</button>}
        {canWrite && enabled !== false && (
          <>
            <button className="btn-sm" disabled={busy} onClick={() => set(false, 300)}>Disable for 5 min</button>
            <ConfirmButton label="Disable" disabled={busy} onConfirm={() => set(false)} />
          </>
        )}
      </div>
      <ErrorLine error={error} />
    </>
  );
}

/**
 * Pause / resume for a download client. Pausing asks for a second click; resuming does not.
 * `paused` is true / false when the integration reports it (SABnzbd) and undefined when it does not
 * (qBittorrent: both buttons are shown).
 */
export function PauseBar({ integId, onRefresh, pauseAction, resumeAction, pauseLabel, resumeLabel, paused }) {
  const { canWrite = true } = useApp();
  const { busy, error, run } = useAction(integId, onRefresh);
  if (!canWrite) return null;
  return (
    <>
      <div className="row pihole-bar">
        {paused !== undefined && <span className={`status-dot status-${paused ? "degraded" : "online"}`} />}
        {paused !== undefined && <span className="muted">{paused ? "Paused" : "Running"}</span>}
        <span className="spacer" />
        {paused !== true && <ConfirmButton label={pauseLabel} disabled={busy} onConfirm={() => run(pauseAction)} />}
        {paused !== false && <button type="button" className="btn-sm" disabled={busy} onClick={() => run(resumeAction)}>{resumeLabel}</button>}
      </div>
      <ErrorLine error={error} />
    </>
  );
}

/**
 * The per-row control of a guest / container list: a "..." button that opens the options inline, and a
 * confirm step before anything runs. `options` = [{action, label, danger?}]; `onRun(action)` returns a promise.
 * `identity` names the row's target (see itemIdentity): when it changes (a poll reordered or replaced the
 * rows) the menu closes instead of acting on whatever now sits there. The prompt names the target, an
 * open menu closes after CONFIRM_MS, and focus moves with the controls.
 */
export function RowActions({ name, options, busy, onRun, identity = name }) {
  const [state, setState] = useState({ open: false, pending: null, forId: identity });
  const live = state.forId === identity ? state : { open: false, pending: null, forId: identity };
  const { open, pending } = live;
  const ref = useRef(null);
  const wasOpen = useRef(false);
  const close = () => setState({ open: false, pending: null, forId: identity });
  useEffect(() => {
    if (!open) return undefined;
    const t = setTimeout(() => setState({ open: false, pending: null, forId: identity }), CONFIRM_MS);
    return () => clearTimeout(t);
  }, [open, pending, identity]);
  useEffect(() => {
    if (open || pending) ref.current?.querySelector("button")?.focus();
    else if (wasOpen.current && document.activeElement === document.body) ref.current?.focus();
    wasOpen.current = open;
  }, [open, pending]);
  if (!options.length) return null;
  if (!open) {
    return <button ref={ref} type="button" className="btn-sm btn-quiet" aria-label={`Actions for ${name}`} onClick={() => setState({ open: true, pending: null, forId: identity })}>⋯</button>;
  }
  if (pending) {
    return (
      <span ref={ref} className="confirm-inline" role="group" aria-label={`Confirm ${pending.label} ${name}`}>
        <span className="muted">{pending.label} {name}?</span>
        <button type="button" className="btn-sm btn-danger" disabled={busy} onClick={async () => { await onRun(pending.action); close(); }}>Confirm</button>
        <button type="button" className="btn-sm" onClick={close}>Cancel</button>
      </span>
    );
  }
  return (
    <span ref={ref} className="confirm-inline" role="group" aria-label={`Actions for ${name}`}>
      {options.map((o) => (
        <button key={o.action} type="button" className={`btn-sm${o.danger ? " btn-danger" : ""}`} disabled={busy} aria-label={`${o.label} ${name}`} onClick={() => setState({ open: true, pending: o, forId: identity })}>{o.label}</button>
      ))}
      <button type="button" className="btn-sm btn-quiet" aria-label={`Close actions for ${name}`} onClick={close}>×</button>
    </span>
  );
}
