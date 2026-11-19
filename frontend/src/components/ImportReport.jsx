/*
 * The outcome of an import or a backup restore. The server answers {counts: {...}, warnings: [...]}; an older
 * server echoed the whole bundle plus `warnings`. Only `counts` and `warnings` are ever read, so both shapes work
 * and nothing from an echoed bundle is shown. Everything is rendered as text.
 */

const COUNT_LABELS = { services: "service", dashboards: "page", widgets: "widget", integrations: "integration" };

/** The warnings of a response as a list of strings (anything else is ignored). */
export function warningsOf(res) {
  return Array.isArray(res?.warnings) ? res.warnings.filter((w) => typeof w === "string" && w) : [];
}

/** "2 services, 1 page, 5 widgets", or "" when the response carries no counts. */
export function countsText(res) {
  const c = res?.counts;
  if (!c || typeof c !== "object") return "";
  return Object.entries(COUNT_LABELS)
    .filter(([k]) => Number.isInteger(c[k]))
    .map(([k, label]) => `${c[k]} ${label}${c[k] === 1 ? "" : "s"}`)
    .join(", ");
}

/** "Restored." / "Restored with 3 changes." (the verb is past tense). */
export function outcomeText(verb, res) {
  const n = warningsOf(res).length;
  return n ? `${verb} with ${n} change${n === 1 ? "" : "s"}.` : `${verb}.`;
}

export default function ImportReport({ verb, result }) {
  const warnings = warningsOf(result);
  const counts = countsText(result);
  return (
    <div className="import-report" role="status">
      <p className={warnings.length ? "warn" : "ok"}>
        {outcomeText(verb, result)}
        {counts ? ` (${counts})` : ""}
      </p>
      {warnings.length > 0 && (
        <details>
          <summary>What changed ({warnings.length})</summary>
          <ul className="np-warnings" aria-label="Changes made while importing">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
