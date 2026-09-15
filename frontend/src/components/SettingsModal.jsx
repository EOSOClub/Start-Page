import { useEffect, useState } from "react";
import Modal from "./Modal.jsx";
import ConfigForm from "./ConfigForm.jsx";
import { api } from "../api.js";

const APPEARANCE = [
  { key: "title", label: "Page title", type: "text" },
  {
    key: "theme",
    label: "Theme",
    type: "select",
    options: [
      { value: "system", label: "Follow system" },
      { value: "dark", label: "Dark" },
      { value: "light", label: "Light" },
    ],
  },
  { key: "accent", label: "Accent color", type: "color" },
  { key: "font", label: "Font family", type: "text", placeholder: "system-ui (default)" },
  { key: "background_color", label: "Background color", type: "color" },
  { key: "background_image", label: "Background image URL", type: "text", placeholder: "https://… or /path.jpg" },
  { key: "background_dim", label: "Background dim", type: "range", min: 0, max: 90, unit: "%", showIf: (v) => !!v.background_image },
  { key: "background_blur", label: "Background blur", type: "range", min: 0, max: 30, unit: "px", showIf: (v) => !!v.background_image },
  { key: "widget_opacity", label: "Widget opacity", type: "range", min: 20, max: 100, unit: "%" },
];

const BEHAVIOUR = [
  { key: "search_url", label: "Web search URL", type: "text", help: "Used by the command palette when nothing matches. {q} is replaced with the query." },
  { key: "open_new_tab", label: "Open links in a new tab by default", type: "checkbox" },
  { key: "auto_favicons", label: "Show site icons for links without an icon", type: "checkbox", help: "Fetched and cached by the server, so no third-party service sees your links." },
  { key: "backup_enabled", label: "Automatic daily JSON backup", type: "checkbox" },
  { key: "backup_keep", label: "Keep last N automatic backups", type: "number", min: 1, showIf: (v) => v.backup_enabled },
];

const fmtSize = (n) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`);

export default function SettingsModal({ settings, onSave, onRestored, onClose }) {
  const [tab, setTab] = useState("appearance");
  const [value, setValue] = useState(settings);
  const [backups, setBackups] = useState([]);
  const [msg, setMsg] = useState("");

  // Keep the form in sync when the settings change under it (e.g. after a backup restore reloads everything).
  useEffect(() => setValue(settings), [settings]);

  const loadBackups = () => api.listBackups().then(setBackups).catch(() => {});
  useEffect(() => {
    if (tab === "backups") loadBackups();
  }, [tab]);

  const restore = async (name) => {
    if (!confirm(`Restore "${name}"? Current configuration will be replaced (a pre-restore backup is written first).`)) return;
    try {
      await api.restoreBackup(name, true);
      setMsg("Restored.");
      await onRestored();
    } catch (e) {
      setMsg(e.message);
    }
  };

  return (
    <Modal
      title="Settings"
      onClose={onClose}
      footer={
        tab !== "backups" ? (
          <>
            <span className="spacer" />
            <button onClick={onClose}>Cancel</button>
            <button className="btn-primary" onClick={() => onSave(value)}>Save</button>
          </>
        ) : (
          <>
            {msg && <span className="muted">{msg}</span>}
            <span className="spacer" />
            <button className="btn-primary" onClick={() => api.createBackup("manual").then(loadBackups).catch((e) => setMsg(e.message))}>Back up now</button>
          </>
        )
      }
    >
      <div className="subtabs">
        {[["appearance", "Appearance"], ["behaviour", "Behaviour"], ["backups", "Backups"]].map(([k, l]) => (
          <button key={k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === "appearance" && <ConfigForm fields={APPEARANCE} value={value} onChange={setValue} />}
      {tab === "behaviour" && <ConfigForm fields={BEHAVIOUR} value={value} onChange={setValue} />}
      {tab === "backups" && (
        <>
          <p className="muted">Stored in <code>data/backups/</code> on the server. Automatic backups run daily when enabled.</p>
          {backups.length === 0 && <p className="muted">No backups yet.</p>}
          <table className="svc-table">
            <tbody>
              {backups.map((b) => (
                <tr key={b.name}>
                  <td><code>{b.name}</code></td>
                  <td className="muted nowrap">{fmtSize(b.size)}</td>
                  <td className="muted nowrap">{new Date(b.modified * 1000).toLocaleString()}</td>
                  <td className="right nowrap">
                    <button className="btn-sm" onClick={() => restore(b.name)}>Restore</button>{" "}
                    <button className="btn-sm btn-danger" onClick={() => confirm(`Delete ${b.name}?`) && api.deleteBackup(b.name).then(loadBackups).catch((e) => setMsg(e.message))}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Modal>
  );
}
