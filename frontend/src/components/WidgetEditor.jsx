import { useState } from "react";
import Modal from "./Modal.jsx";
import ConfigForm from "./ConfigForm.jsx";
import ErrorBoundary from "./ErrorBoundary.jsx";
import { WIDGET_GROUPS, WIDGET_TYPES, carryConfig, migrateConfig } from "../widgets/index.jsx";
import { groupWidgetTypes } from "./widgetMenu.js";

/**
 * Edit an existing widget (type, service, config) or create a new one.
 * `widget` is {type, service_id, config, ...}; onSave receives the updated fields.
 */
export default function WidgetEditor({ widget, services, integrations = [], integrationData = {}, dashboards = [], onSave, onDelete, onDuplicate, onMoveTo, onClose, onManageServices, onManageIntegrations }) {
  const [type, setType] = useState(widget.type);
  const [serviceId, setServiceId] = useState(widget.service_id ?? "");
  const [config, setConfig] = useState(() => migrateConfig(widget.type, widget.config));
  const def = WIDGET_TYPES[type] || { label: type, fields: [] };
  const isNew = !widget.id;

  const save = () => onSave({ type, service_id: def.usesService ? serviceId || null : null, config });
  const otherPages = dashboards.filter((d) => d.id !== widget.dashboard_id);

  return (
    <Modal
      title={isNew ? "Add widget" : "Widget settings"}
      onClose={onClose}
      footer={
        <>
          {!isNew && (
            <>
              <button className="btn-danger" onClick={onDelete}>Delete</button>
              <button onClick={onDuplicate} title="Duplicate (Ctrl+D)">Duplicate</button>
              {otherPages.length > 0 && onMoveTo && (
                <select
                  className="page-move-select"
                  value=""
                  title="Move or copy this widget to another page"
                  onChange={(e) => {
                    const [mode, id] = e.target.value.split(":");
                    if (id) onMoveTo(id, mode === "copy");
                  }}
                >
                  <option value="">To page…</option>
                  <optgroup label="Move to">
                    {otherPages.map((d) => <option key={d.id} value={`move:${d.id}`}>{d.name}</option>)}
                  </optgroup>
                  <optgroup label="Copy to">
                    {otherPages.map((d) => <option key={d.id} value={`copy:${d.id}`}>{d.name}</option>)}
                  </optgroup>
                </select>
              )}
            </>
          )}
          <span className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={save}>{isNew ? "Add" : "Save"}</button>
        </>
      }
    >
      <div className="form">
        <div className="field">
          <label htmlFor="widget-type">Type</label>
          <select
            id="widget-type"
            value={type}
            onChange={(e) => {
              const to = e.target.value;
              setType(to);
              // An unsaved widget starts over on the new type's defaults, keeping only what both types declare and accept.
              if (isNew) setConfig((cfg) => carryConfig(type, to, cfg));
            }}
          >
            {groupWidgetTypes(WIDGET_TYPES, WIDGET_GROUPS).map((s) => (
              <optgroup key={s.group} label={s.group}>
                {s.items.map((it) => <option key={it.type} value={it.type}>{it.label}</option>)}
              </optgroup>
            ))}
          </select>
        </div>
        {def.usesService && (
          <div className="field">
            <label>Service</label>
            <div className="row">
              <select value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
                <option value="">— select a service —</option>
                {services.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.category ? `${s.category} / ` : ""}{s.name}
                  </option>
                ))}
              </select>
              <button type="button" className="btn-sm" onClick={onManageServices}>Manage…</button>
            </div>
          </div>
        )}
      </div>
      <ErrorBoundary
        resetKey={config}
        fallback={() => (
          <div className="help">These settings can't be edited (invalid config). You can still delete this widget.</div>
        )}
      >
        <ConfigForm fields={def.fields} value={config} onChange={setConfig} services={services} integrations={integrations} integrationData={integrationData} />
      </ErrorBoundary>
      {def.fields.some((f) => f.type === "integration") && (
        <div className="help" style={{ marginTop: 10 }}>
          <button type="button" className="btn-sm" onClick={onManageIntegrations}>Manage integrations…</button>
        </div>
      )}
    </Modal>
  );
}
