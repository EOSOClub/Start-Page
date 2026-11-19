/** Pure helpers for ConfigForm-driven forms. */

/**
 * A cleared number field is stored as `null` while the user edits (so the box can be empty).
 * Before sending, a cleared field falls back to the field's `default`, or its key is left out
 * when there is none: sending `null` for an integer setting is rejected with a 422.
 */
export function withNumberDefaults(fields, value) {
  const out = { ...value };
  for (const f of fields) {
    if (f.type !== "number" || !(f.key in out) || out[f.key] != null) continue;
    if (f.default !== undefined) out[f.key] = f.default;
    else delete out[f.key];
  }
  return out;
}

/** Settings as sent to PATCH /api/settings: no read-only keys, cleared numbers resolved. */
export function settingsPayload(fields, value) {
  const { notify_url_set, ...rest } = value; // eslint-disable-line no-unused-vars
  return withNumberDefaults(fields, rest);
}
