import { useState } from "react";
import Icon from "../widgets/Icon.jsx";
import IconPicker from "./IconPicker.jsx";
import LinksEditor from "./LinksEditor.jsx";

/**
 * Schema-driven form. `fields` come from the widget registry (or a static schema).
 * Supported types: text, number, range, textarea, checkbox, color, select, icon,
 *                  category, service, services, integration, links
 * A field's `default` is shown when the value is unset.
 */
export default function ConfigForm({ fields, value, onChange, services = [], integrations = [] }) {
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
            control = <textarea id={id} rows={f.rows || 6} value={v ?? ""} placeholder={f.placeholder} onChange={(e) => set(f.key, e.target.value)} />;
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
          case "integration": {
            const list = integrations.filter((i) => !f.integrationType || i.type === f.integrationType);
            control = (
              <select id={id} value={v ?? ""} onChange={(e) => set(f.key, e.target.value || null)}>
                <option value="">— select —</option>
                {list.map((i) => (
                  <option key={i.id} value={i.id}>{i.name} ({i.type})</option>
                ))}
              </select>
            );
            if (list.length === 0) f = { ...f, help: `No ${f.integrationType || ""} integration yet — add one under Integrations.` };
            break;
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
