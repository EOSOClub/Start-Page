import { useEffect, useMemo, useState } from "react";
import { useApp } from "../context.js";
import FitText from "./FitText.jsx";
import { countdownState, msUntilChange, parseTarget } from "./countdown.js";

/* Countdown to (or since) a date, optionally repeating every year. */

/**
 * The current time in ms, updated only when the displayed text can change (once a day for "days",
 * once a minute for "d h m") and not at all while the tab is hidden; on return it catches up at once.
 */
function useCountdownNow(spec) {
  const [now, setNow] = useState(() => Date.now());
  const ms = spec?.target.ms;
  useEffect(() => {
    if (!spec) return undefined;
    let t;
    const schedule = () => {
      clearTimeout(t);
      if (document.visibilityState === "hidden") return;
      t = setTimeout(() => {
        setNow(Date.now());
        schedule();
      }, msUntilChange(spec));
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") setNow(Date.now());
      schedule();
    };
    document.addEventListener("visibilitychange", onVisible);
    schedule();
    return () => {
      clearTimeout(t);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [ms, spec?.yearly, spec?.units, spec?.after, !!spec]); // eslint-disable-line
  return now;
}

export const CountdownWidget = {
  label: "Countdown",
  group: "Basics",
  size: { w: 4, h: 3 },
  fields: [
    { key: "label", label: "Label", type: "text", placeholder: "Summer holiday" },
    { key: "target", label: "Date and time", type: "text", placeholder: "2026-12-24 18:00", help: "YYYY-MM-DD or YYYY-MM-DD HH:mm in your local time, or ISO 8601 with an offset (2026-12-24T18:00:00+01:00). A date alone counts the whole day." },
    { key: "yearly", label: "Repeat every year (birthdays, anniversaries)", type: "checkbox" },
    {
      key: "units",
      label: "Count down to",
      type: "select",
      default: "days",
      options: [
        { value: "days", label: "Days" },
        { value: "hours", label: "Hours (12d 4h)" },
        { value: "minutes", label: "Minutes (12d 4h 5m)" },
        { value: "seconds", label: "Seconds (12d 4h 5m 3s)" },
      ],
    },
    {
      key: "after",
      label: "After it has passed",
      type: "select",
      default: "up",
      showIf: (c) => !c.yearly,
      options: [
        { value: "up", label: "Count up (“3 days ago”)" },
        { value: "done", label: "Show “Done”" },
        { value: "hide", label: "Hide the widget" },
      ],
    },
  ],
  Render: ({ widget }) => {
    const c = widget.config;
    const { editMode } = useApp();
    const parsed = useMemo(() => parseTarget(c.target), [c.target]);
    const spec = useMemo(
      () => (parsed.ok ? { target: parsed, yearly: !!c.yearly, units: c.units || "days", after: c.after || "up" } : null),
      [parsed, c.yearly, c.units, c.after]
    );
    const now = useCountdownNow(spec);
    if (!spec) {
      return <div className="muted pad">{c.target ? `Countdown: ${parsed.error}` : "Set a date in widget settings."}</div>;
    }
    const s = countdownState(spec, now);
    if (s.state === "passed" && spec.after === "hide") {
      return editMode ? <div className="muted pad">Countdown finished (hidden outside edit mode).</div> : null;
    }
    const target = new Date(s.target);
    const when = target.toLocaleString([], parsed.hasTime
      ? { dateStyle: "medium", timeStyle: "short" }
      : { dateStyle: "medium" });
    let big = s.text;
    let note = s.state === "upcoming" ? `until ${when}` : "";
    if (s.state === "passed") {
      big = spec.after === "done" ? "Done" : `${s.text} ago`;
      note = when;
    }
    if (s.state === "today") note = when;
    return (
      <FitText className="clock countdown" min={10}>
        {c.label && <div className="tile-sub">{c.label}</div>}
        <div className="clock-time" role="timer" aria-live="off">{big}</div>
        <div className="tile-sub">{note}</div>
      </FitText>
    );
  },
};
