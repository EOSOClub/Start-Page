import ImportReport from "./ImportReport.jsx";
import { useEffect, useRef, useState } from "react";
import Modal from "./Modal.jsx";
import ConfigForm from "./ConfigForm.jsx";
import SecurityTab from "./SecurityTab.jsx";
import InstallHint from "./InstallHint.jsx";
import { settingsPayload } from "./formValues.js";
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
  { key: "backup_keep", label: "Keep last N automatic backups", type: "number", min: 1, default: 14, showIf: (v) => v.backup_enabled },
];

/** What the server shows instead of the stored notification URL (it is write-only). */
const URL_MASK = "********";

const NOTIFICATIONS = [
  {
    key: "notify_type",
    label: "Notify me when a service goes down",
    type: "select",
    options: [
      { value: "none", label: "Off" },
      { value: "ntfy", label: "ntfy" },
      { value: "discord", label: "Discord webhook" },
      { value: "webhook", label: "Generic webhook (JSON)" },
    ],
  },
  { key: "notify_url", label: "URL", type: "secret", mask: URL_MASK, noun: "URL", placeholder: "https://ntfy.sh/my-topic", showIf: (v) => (v.notify_type ?? "none") !== "none" },
  { key: "notify_after_failures", label: "Failed checks before alerting", type: "number", min: 1, max: 10, default: 2, help: "Consecutive failures, so a single blip does not alert.", showIf: (v) => (v.notify_type ?? "none") !== "none" },
  { key: "notify_on_recover", label: "Also notify when it recovers", type: "checkbox", showIf: (v) => (v.notify_type ?? "none") !== "none" },
  { key: "health_history_days", label: "Keep check history for (days)", type: "number", min: 1, max: 365, default: 7, placeholder: "7" },
  { key: "health_events_days", label: "Keep incident log for (days)", type: "number", min: 1, max: 3650, default: 90, placeholder: "90" },
];

const ALL_FIELDS = [...APPEARANCE, ...BEHAVIOUR, ...NOTIFICATIONS];
const NOTIFY_KEYS = ["notify_type", "notify_url", "notify_after_failures", "notify_on_recover"];

const fmtSize = (n) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`);

export default function SettingsModal({ settings, auth, onAuthChanged, onSave, onSettingsSaved, onRestored, onClose }) {
  const [tab, setTab] = useState("appearance");
  const [value, setValue] = useState(settings);
  const [backups, setBackups] = useState([]);
  const [msg, setMsg] = useState(""); // Backups tab
  const [restored, setRestored] = useState(null); // the server's answer to the last restore {counts, warnings}
  const [testMsg, setTestMsg] = useState(""); // Notifications tab ("Send test")
  const [urlSaved, setUrlSaved] = useState(!!settings.notify_url_set);

  // Re-seed the form only after a backup restore. Any other refetch (tab refocus, an outside edit)
  // also hands us a new `settings` object and must not wipe what the user is typing.
  const resync = useRef(false);
  useEffect(() => {
    if (!resync.current) return;
    resync.current = false;
    setValue(settings);
    setUrlSaved(!!settings.notify_url_set);
  }, [settings]);

  const loadBackups = () => api.listBackups().then(setBackups).catch(() => {});
  useEffect(() => {
    if (tab === "backups") loadBackups();
  }, [tab]);

  const restore = async (name) => {
    if (!confirm(`Restore "${name}"? Current configuration will be replaced (a pre-restore backup is written first).`)) return;
    try {
      const res = await api.restoreBackup(name, true);
      setMsg("");
      setRestored({ result: res || {} });
      resync.current = true;
      await onRestored();
    } catch (e) {
      resync.current = false;
      setRestored(null);
      setMsg(e.message);
    }
  };

  // The server sends the test with its saved settings, so save the notification fields first.
  const sendTest = async () => {
    if (!urlSaved && !value.notify_url) {
      setTestMsg("Enter a URL first.");
      return;
    }
    setTestMsg("Saving, then sending…");
    try {
      const payload = settingsPayload(ALL_FIELDS, Object.fromEntries(NOTIFY_KEYS.filter((k) => value[k] !== undefined).map((k) => [k, value[k]])));
      const saved = await api.updateSettings(payload); // a still-masked URL keeps the stored one
      setUrlSaved(!!saved.notify_url_set);
      setValue((v) => ({ ...v, notify_url: saved.notify_url, notify_url_set: saved.notify_url_set }));
      onSettingsSaved?.(saved); // the app's own copy of the settings stays in step with the server
      const r = await api.notifyTest();
      setTestMsg(r.ok ? "Test sent." : `Test failed: ${r.error}`);
    } catch (e) {
      setTestMsg(e.message);
    }
  };

  return (
    <Modal
      title="Settings"
      onClose={onClose}
      footer={
        tab === "security" ? (
          <>
            <span className="spacer" />
            <button onClick={onClose}>Close</button>
          </>
        ) : tab !== "backups" ? (
          <>
            <span className="spacer" />
            <button onClick={onClose}>Cancel</button>
            <button className="btn-primary" onClick={() => onSave(settingsPayload(ALL_FIELDS, value))}>Save</button>
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
        {[["appearance", "Appearance"], ["behaviour", "Behaviour"], ["notifications", "Notifications"], ["backups", "Backups"], ["security", "Security"]].map(([k, l]) => (
          <button key={k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === "appearance" && <ConfigForm fields={APPEARANCE} value={value} onChange={setValue} />}
      {tab === "behaviour" && (
        <>
          <ConfigForm fields={BEHAVIOUR} value={value} onChange={setValue} />
          <InstallHint />
        </>
      )}
      {tab === "notifications" && (
        <>
          <ConfigForm fields={NOTIFICATIONS.map((f) => (f.key === "notify_url" ? { ...f, saved: urlSaved } : f))} value={value} onChange={setValue} />
          {(value.notify_type ?? "none") !== "none" && (
            <>
              <p>
                <button onClick={sendTest}>Send test</button> {testMsg && <span className="muted">{testMsg}</span>}
              </p>
              <p className="muted">Send test saves these notification settings first. The URL is never shown again once saved.</p>
            </>
          )}
        </>
      )}
      {tab === "security" && <SecurityTab auth={auth} onChanged={onAuthChanged} />}
      {tab === "backups" && (
        <>
          <p className="muted">Stored in <code>data/backups/</code> on the server. Automatic backups run daily when enabled.</p>
          {restored && <ImportReport verb="Restored" result={restored.result} />}
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
