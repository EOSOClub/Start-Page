import { useState } from "react";
import Icon from "../widgets/Icon.jsx";
import IconPicker from "./IconPicker.jsx";
import LinksEditor from "./LinksEditor.jsx";
import { suitableIntegrations, useIntegrationTypes } from "../integrationTypes.js";
import { formatValue, parseKeys } from "../widgets/integrationViz.js";

/**
 * An `integration` field: pick one integration. It lists those whose type is `integrationType`
 * (one type or a list) and/or whose type provides `capability` ("stats" | "items").
 */
function IntegrationField({ f, id, v, set, integrations }) {
  const { types, error } = useIntegrationTypes(!!f.capability); // specs are only needed to match a capability
  const list = suitableIntegrations(integrations, types, f);
  const what = f.capability ? `${f.capability}-providing` : [].concat(f.integrationType || []).join(" / ");
  const loading = f.capability && types.length === 0 && !error;
  const help = loading ? "Loading integration types…" : list.length === 0 ? `No ${what} integration yet — add one under Integrations.` : f.help;
  if (f.multiple) {
    // `multiple`: the value is an array of integration ids, shown as a checklist
    const selected = Array.isArray(v) ? v : [];
    return (
      <div className="field">
        <label id={`${id}-label`}>{f.label}</label>
        <div className="checklist" role="group" aria-labelledby={`${id}-label`}>
          {list.map((i) => (
            <label key={i.id} className="check">
              <input
                type="checkbox"
                checked={selected.includes(i.id)}
                onChange={(e) => set(f.key, e.target.checked ? [...selected, i.id] : selected.filter((x) => x !== i.id))}
              />
              <span>{i.name} <span className="muted">({i.type})</span></span>
            </label>
          ))}
        </div>
        {help && <div className="help">{help}</div>}
      </div>
    );
  }
  return (
    <div className="field">
      <label htmlFor={id}>{f.label}</label>
      <select id={id} value={v ?? ""} onChange={(e) => set(f.key, e.target.value || null)}>
        <option value="">— select —</option>
        {list.map((i) => (
          <option key={i.id} value={i.id}>{i.name} ({i.type})</option>
        ))}
      </select>
      {help && <div className="help">{help}</div>}
    </div>
  );
}

/**
 * A `statkeys` field: stat keys as text (comma separated; one key when `multiple` is false) with the keys
 * the selected integration (`value.integration_id`) currently reports offered as clickable chips.
 */
function StatKeysField({ f, id, v, set, stats }) {
  const single = f.multiple === false;
  const keys = parseKeys(v);
  const toggle = (key) => {
    if (single) return set(f.key, keys[0] === key ? "" : key);
    set(f.key, keys.includes(key) ? keys.filter((k) => k !== key).join(", ") : [...keys, key].join(", "));
  };
  return (
    <div className="field">
      <label htmlFor={id}>{f.label}</label>
      <input id={id} type="text" value={v ?? ""} placeholder={f.placeholder} onChange={(e) => set(f.key, e.target.value)} />
      {stats.length > 0 && (
        <div className="chips" role="group" aria-label="Available stats">
          {stats.map((s) => (
            <button
              key={s.key}
              type="button"
              className={`chip${keys.includes(String(s.key).toLowerCase()) ? " on" : ""}`}
              aria-pressed={keys.includes(String(s.key).toLowerCase())}
              title={`${s.label}: ${formatValue(s.value)}${s.unit ? ` ${s.unit}` : ""}`}
              onClick={() => toggle(String(s.key).toLowerCase())}
            >
              {s.key}
            </button>
          ))}
        </div>
      )}
      {f.help && <div className="help">{f.help}</div>}
    </div>
  );
}

/**
 * Schema-driven form. `fields` come from the widget registry (or a static schema).
 * Supported types: text, url, number, range, textarea, checkbox, color, select, icon,
 *                  category, service, services, integration, links, secret, statkeys
 * `integration` with `multiple: true` holds an array of ids. `statkeys` is text plus chips of the stat keys
 * of the integration chosen in the `integration_id` field (needs `integrationData`).
 * A field's `default` is shown when the value is unset.
 * `secret` is write-only (the server sends `mask`); `multiline` makes it a textarea.
 */
/** A single-line secret: masked by default (type=password, never offered to the browser's autofill), with a show/hide toggle. */
function SecretInput({ label, ...props }) {
  const [show, setShow] = useState(false);
  return (
    <>
      <input type={show ? "text" : "password"} {...props} />
      <button type="button" className="btn-sm" aria-pressed={show} aria-label={`${show ? "Hide" : "Show"} ${label || "value"}`} onClick={() => setShow((s) => !s)}>
        {show ? "Hide" : "Show"}
      </button>
    </>
  );
}

export default function ConfigForm({ fields, value, onChange, services = [], integrations = [], integrationData = {} }) {
  const categories = [...new Set(services.map((s) => s.category).filter(Boolean))].sort();
  const set = (key, v) => onChange({ ...value, [key]: v });
  const [pickerFor, setPickerFor] = useState(null);

  return (
    <div className="form">
      {fields.map((f) => {
        if (f.showIf && !f.showIf(value)) return null;
        const v = value[f.key] ?? f.default;
        const id = `f-${f.key}`;
        let control;
        switch (f.type) {
          case "checkbox":
            return (
              <div className="field" key={f.key}>
                <label className="check">
                  <input id={id} type="checkbox" checked={!!v} onChange={(e) => set(f.key, e.target.checked)} />
                  <span>{f.label}</span>
                </label>
                {f.help && <div className="help">{f.help}</div>}
              </div>
            );
          case "textarea":
            control = (
              <>
                <textarea
                  id={id}
                  rows={f.rows || 6}
                  className={f.mono ? "code-input" : undefined}
                  spellCheck={f.mono ? false : undefined}
                  value={v ?? ""}
                  placeholder={f.placeholder}
                  onChange={(e) => set(f.key, e.target.value)}
                />
                {f.examples && (
                  // Replaces the html, css and js of the widget with a short working example.
                  <select
                    className="btn-sm example-select"
                    aria-label="Insert example"
                    value=""
                    onChange={(e) => {
                      const ex = f.examples[Number(e.target.value)];
                      if (!ex) return;
                      const hasCode = ["html", "css", "js"].some((k) => value[k]);
                      if (hasCode && !confirm("Replace the current HTML, CSS and JavaScript with this example?")) return;
                      onChange({ ...value, ...ex.config });
                    }}
                  >
                    <option value="">Insert example…</option>
                    {f.examples.map((ex, i) => <option key={ex.label} value={i}>{ex.label}</option>)}
                  </select>
                )}
              </>
            );
            break;
          case "number":
            control = (
              <input
                id={id}
                type="number"
                value={v ?? ""}
                placeholder={f.placeholder}
                min={f.min}
                max={f.max}
                onChange={(e) => set(f.key, e.target.value === "" ? null : Number(e.target.value))}
              />
            );
            break;
          case "secret": {
            // Write-only value: the server only ever sends `f.mask`. Typing replaces it, an
            // emptied box keeps the saved one (`f.saved`), and "Remove" clears it on Save.
            const isMask = f.mask !== undefined && v === f.mask;
            const removing = f.saved && v === "";
            const props = {
              id,
              autoComplete: "off",
              spellCheck: false,
              value: isMask ? "" : v ?? "",
              placeholder: isMask ? `Saved. Type a new ${f.noun || "value"} to replace it.` : removing ? "Will be removed when you save" : f.placeholder,
              onChange: (e) => set(f.key, e.target.value === "" && f.saved ? f.mask : e.target.value),
            };
            control = (
              <div className="row">
                {f.multiline ? <textarea rows={f.rows || 3} {...props} /> : <SecretInput {...props} autoComplete="new-password" label={f.label} />}
                {isMask && <button type="button" className="btn-sm" onClick={() => set(f.key, "")}>Remove</button>}
              </div>
            );
            break;
          }
          case "range":
            control = (
              <div className="row">
                <input id={id} type="range" min={f.min ?? 0} max={f.max ?? 100} step={f.step ?? 1} value={v ?? f.min ?? 0} onChange={(e) => set(f.key, Number(e.target.value))} />
                <span className="muted nowrap" style={{ width: 48, textAlign: "right" }}>{v ?? f.min ?? 0}{f.unit || ""}</span>
              </div>
            );
            break;
          case "color":
            control = (
              <div className="row">
                <input type="color" value={v || "#6c8cff"} onChange={(e) => set(f.key, e.target.value)} />
                <input id={id} type="text" value={v ?? ""} placeholder="#hex or empty" onChange={(e) => set(f.key, e.target.value)} />
                {v && <button type="button" className="btn-sm" onClick={() => set(f.key, "")}>Clear</button>}
              </div>
            );
            break;
          case "select":
            control = (
              // Map back to the option's own value so numeric options stay numbers.
              <select id={id} value={v ?? f.options[0]?.value} onChange={(e) => set(f.key, f.options.find((o) => String(o.value) === e.target.value)?.value ?? e.target.value)}>
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            );
            break;
          case "icon":
            control = (
              <>
                <div className="row">
                  <Icon icon={v} url={value.url} fallback={value.name || value.label || value.title || "?"} size={32} />
                  <input
                    id={id}
                    type="text"
                    value={v ?? ""}
                    placeholder="emoji, image URL, or dashboard-icons name"
                    onChange={(e) => set(f.key, e.target.value)}
                  />
                  <button type="button" className="btn-sm" onClick={() => setPickerFor(pickerFor === f.key ? null : f.key)}>
                    {pickerFor === f.key ? "Close" : "Browse…"}
                  </button>
                </div>
                {pickerFor === f.key && (
                  <IconPicker initial={v && !/[^a-z0-9-]/.test(v) ? v : ""} onPick={(n) => { set(f.key, n); setPickerFor(null); }} onClose={() => setPickerFor(null)} />
                )}
              </>
            );
            break;
          case "category":
            control = (
              <>
                <input id={id} list={`${id}-list`} value={v ?? ""} placeholder={f.placeholder} onChange={(e) => set(f.key, e.target.value)} />
                <datalist id={`${id}-list`}>
                  {categories.map((c) => <option key={c} value={c} />)}
                </datalist>
              </>
            );
            break;
          case "service":
            control = (
              <select id={id} value={v ?? ""} onChange={(e) => set(f.key, e.target.value || null)}>
                <option value="">{f.placeholder || "— none —"}</option>
                {services.filter((s) => !f.exclude || s.id !== f.exclude).map((s) => (
                  <option key={s.id} value={s.id}>{s.category ? `${s.category} / ` : ""}{s.name}</option>
                ))}
              </select>
            );
            break;
          case "integration":
            return <IntegrationField key={f.key} f={f} id={id} v={v} set={set} integrations={integrations} />;
          case "statkeys": {
            const stats = integrationData[value.integration_id]?.data?.stats;
            return <StatKeysField key={f.key} f={f} id={id} v={v} set={set} stats={Array.isArray(stats) ? stats : []} />;
          }
          case "links":
            control = <LinksEditor value={v ?? []} onChange={(links) => set(f.key, links)} />;
            break;
          case "services": {
            const selected = v ?? [];
            const pool = services.filter((s) => !f.exclude || s.id !== f.exclude);
            control = (
              <div className="checklist">
                {pool.length === 0 && <div className="muted">No services defined yet.</div>}
                {pool.map((s) => (
                  <label key={s.id} className="check">
                    <input
                      type="checkbox"
                      checked={selected.includes(s.id)}
                      onChange={(e) =>
                        set(f.key, e.target.checked ? [...selected, s.id] : selected.filter((x) => x !== s.id))
                      }
                    />
                    <span>{s.name} <span className="muted">{s.category}</span></span>
                  </label>
                ))}
              </div>
            );
            break;
          }
          default:
            control = <input id={id} type="text" value={v ?? ""} placeholder={f.placeholder} onChange={(e) => set(f.key, e.target.value)} />;
        }
        return (
          <div className="field" key={f.key}>
            <label htmlFor={id}>{f.label}</label>
            {control}
            {f.help && <div className="help">{f.help}</div>}
          </div>
        );
      })}
    </div>
  );
}
