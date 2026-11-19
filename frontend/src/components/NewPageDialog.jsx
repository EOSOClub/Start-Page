import { useEffect, useMemo, useRef, useState } from "react";
import Modal from "./Modal.jsx";
import { api } from "../api.js";
import { miniMap } from "./templateMap.js";

/** A template's layout as a small SVG: one rectangle per widget, no screenshots. */
export function TemplateMap({ template }) {
  const map = useMemo(() => miniMap(template), [template]);
  return (
    <svg className="tpl-map" viewBox={map.viewBox} preserveAspectRatio="xMidYMin meet" role="img" aria-label={`Layout of ${map.rects.length} widgets`}>
      {map.rects.map((r) => (
        <rect key={r.key} className={`tpl-rect tpl-rect-${r.kind}`} x={r.x} y={r.y} width={r.width} height={r.height} rx="0.25">
          <title>{r.type}</title>
        </rect>
      ))}
    </svg>
  );
}

const MODES = [
  { value: "match", label: "Use my existing services where they match, create the rest" },
  { value: "create", label: "Always create the page's services as new ones" },
  { value: "skip", label: "Don't create or link services" },
];

/** Step 2: what creating the page will do, with a service mode and an integration picker per integration. */
function Preview({ source, integrations, onBack, onCreated }) {
  const [name, setName] = useState(source.name);
  const [mode, setMode] = useState("match");
  const [imap, setImap] = useState({}); // ref -> integration id, or null for "set up later"; absent = automatic
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const options = () => ({ service_mode: mode, integration_map: Object.keys(imap).length ? imap : undefined });
  const mapKey = JSON.stringify(imap);
  useEffect(() => {
    let live = true;
    setError("");
    api
      .previewImport({ page: source.page, ...options() })
      .then((p) => live && setPreview(p))
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [mode, mapKey]); // eslint-disable-line

  const create = async () => {
    setBusy(true);
    setError("");
    try {
      const opts = { name: name.trim() || undefined, ...options() };
      const result = source.templateId ? await api.applyTemplate(source.templateId, opts) : await api.importPage({ page: source.page, ...opts });
      await onCreated(result);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  const needed = preview ? [...preview.matched_integrations, ...preview.unresolved_integrations].sort((a, b) => a.name.localeCompare(b.name)) : [];
  const chosen = (i) => (i.ref in imap ? imap[i.ref] : i.id) ?? "";
  const nServices = preview ? preview.created_services.length + preview.matched_services.length + preview.skipped_services.length : 0;

  return (
    <Modal
      title={`New page: ${source.title}`}
      wide
      onClose={source.onClose}
      footer={
        <>
          <button onClick={onBack}>Back</button>
          <span className="spacer" />
          <button className="btn-primary" onClick={create} disabled={!preview || busy}>{busy ? "Creating…" : "Create page"}</button>
        </>
      }
    >
      <div className="form">
        <div className="field">
          <label htmlFor="np-name">Page name</label>
          <input id="np-name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {!preview && !error && <div className="muted">Checking…</div>}
        {preview && (
          <>
            <p className="muted np-summary">
              {preview.widget_count} widget{preview.widget_count === 1 ? "" : "s"} on a new page. Nothing on your other pages changes.
            </p>
            {preview.warnings.length > 0 && (
              <ul className="np-warnings" aria-label="Warnings">
                {preview.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            )}
            {nServices > 0 && (
              <fieldset className="np-fieldset">
                <legend>Services</legend>
                <div className="help">
                  {preview.created_services.length} to create, {preview.matched_services.length} matched to existing
                  {preview.skipped_services.length ? `, ${preview.skipped_services.length} left unlinked` : ""}.
                </div>
                {MODES.map((m) => (
                  <label key={m.value} className="check">
                    <input type="radio" name="np-mode" checked={mode === m.value} onChange={() => setMode(m.value)} />
                    <span>{m.label}</span>
                  </label>
                ))}
              </fieldset>
            )}
            {needed.length > 0 && (
              <fieldset className="np-fieldset">
                <legend>Integrations this page needs</legend>
                {needed.map((i) => {
                  const same = integrations.filter((x) => x.type === i.type);
                  return (
                    <div className="field" key={i.ref}>
                      <label htmlFor={`np-int-${i.ref}`}>{i.name} <span className="muted">({i.type})</span></label>
                      <select id={`np-int-${i.ref}`} value={chosen(i)} onChange={(e) => setImap((m) => ({ ...m, [i.ref]: e.target.value || null }))}>
                        <option value="">Set up later</option>
                        {same.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                      </select>
                      {same.length === 0 && <div className="help">No {i.type} integration yet. The widget will ask you to pick one once you add it under Integrations.</div>}
                    </div>
                  );
                })}
              </fieldset>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

/**
 * The "+" of the tab bar: Blank page, From template, or Import from file; then a preview of what will happen.
 * onBlank(name) creates an empty page; onCreated(result) receives the import response.
 */
export default function NewPageDialog({ integrations = [], onBlank, onCreated, onClose }) {
  const [tab, setTab] = useState("blank");
  const [templates, setTemplates] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [blankName, setBlankName] = useState("New page");
  const [paste, setPaste] = useState("");
  const [fileError, setFileError] = useState("");
  const [source, setSource] = useState(null);
  const fileRef = useRef(null);

  useEffect(() => {
    if (tab !== "template" || templates) return;
    api.listTemplates().then(setTemplates).catch((e) => setLoadError(e.message));
  }, [tab, templates]);

  const tabFocus = useRef(false);
  useEffect(() => {
    if (!tabFocus.current) return;
    tabFocus.current = false;
    document.getElementById(`np-tab-${tab}`)?.focus();
  }, [tab]);

  const fromTemplate = async (t) => {
    try {
      const page = await api.getTemplate(t.id);
      setSource({ kind: "template", templateId: t.id, title: t.title, name: t.name || t.title, page });
    } catch (e) {
      setLoadError(e.message);
    }
  };
  const fromText = (text) => {
    setFileError("");
    let page;
    try {
      page = JSON.parse(text);
    } catch {
      return setFileError("That is not valid JSON.");
    }
    if (!page || page.format !== "startpage-page") return setFileError('That is not a Start Page page file (its "format" must be "startpage-page").');
    setSource({ kind: "file", title: page.name || "imported page", name: page.name || "Imported page", page });
  };

  if (source) {
    return <Preview source={{ ...source, onClose }} integrations={integrations} onBack={() => setSource(null)} onCreated={onCreated} />;
  }

  const TABS = [["blank", "Blank page"], ["template", "From template"], ["file", "Import from file"]];
  return (
    <Modal title="New page" wide onClose={onClose}>
      <div
        className="np-tabs"
        role="tablist"
        aria-label="How to start the page"
        onKeyDown={(e) => {
          // WAI-ARIA tabs: arrows move between tabs (wrapping), Home/End jump; the tab that gets focus is selected.
          const at = TABS.findIndex(([id]) => id === tab);
          const to = { ArrowRight: (at + 1) % TABS.length, ArrowLeft: (at + TABS.length - 1) % TABS.length, Home: 0, End: TABS.length - 1 }[e.key];
          if (to === undefined) return;
          e.preventDefault();
          tabFocus.current = true; // focus the new tab after the panel rendered (an autoFocus field would take it otherwise)
          setTab(TABS[to][0]);
        }}
      >
        {TABS.map(([id, label]) => (
          <button key={id} type="button" role="tab" id={`np-tab-${id}`} aria-selected={tab === id} aria-controls="np-panel" tabIndex={tab === id ? 0 : -1} className={`np-tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      <div id="np-panel" role="tabpanel" aria-labelledby={`np-tab-${tab}`} className="np-panel">
        {tab === "blank" && (
          <form className="form" onSubmit={(e) => { e.preventDefault(); if (blankName.trim()) onBlank(blankName.trim()); }}>
            <div className="field">
              <label htmlFor="np-blank-name">Page name</label>
              <input id="np-blank-name" type="text" autoFocus value={blankName} onChange={(e) => setBlankName(e.target.value)} />
            </div>
            <div><button className="btn-primary" type="submit" disabled={!blankName.trim()}>Create blank page</button></div>
          </form>
        )}
        {tab === "template" && (
          <>
            {loadError && <div className="error" role="alert">{loadError}</div>}
            {!templates && !loadError && <div className="muted">Loading templates…</div>}
            <div className="tpl-grid">
              {(templates || []).map((t) => (
                <button key={t.id} type="button" className="tpl-card" onClick={() => fromTemplate(t)} aria-label={`${t.title}: ${t.description}`}>
                  <TemplateMap template={t} />
                  <span className="tpl-title">{t.title}</span>
                  <span className="tpl-desc">{t.description}</span>
                  <span className="tpl-tags">
                    {t.tags.map((tag) => <span key={tag} className="chip">{tag}</span>)}
                    {t.requires.length > 0 && <span className="muted tpl-req">needs {t.requires.join(", ")}</span>}
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
        {tab === "file" && (
          <div className="form">
            <p className="muted">Pages shared as <code>.startpage-page.json</code>. The page is created as a new page; nothing existing is overwritten.</p>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              style={{ display: "none" }}
              onChange={async (e) => {
                const file = e.target.files[0];
                e.target.value = "";
                if (file) fromText(await file.text());
              }}
            />
            <div><button type="button" onClick={() => fileRef.current.click()}>Choose file…</button></div>
            <div className="field">
              <label htmlFor="np-paste">Or paste the JSON</label>
              <textarea id="np-paste" className="code-input" rows={6} spellCheck={false} value={paste} onChange={(e) => setPaste(e.target.value)} />
            </div>
            {fileError && <div className="error" role="alert">{fileError}</div>}
            <div><button className="btn-primary" type="button" disabled={!paste.trim()} onClick={() => fromText(paste)}>Continue</button></div>
          </div>
        )}
      </div>
    </Modal>
  );
}
