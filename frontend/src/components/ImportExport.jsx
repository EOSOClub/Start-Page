import { useRef, useState } from "react";
import Modal from "./Modal.jsx";
import { api } from "../api.js";
import ImportReport from "./ImportReport.jsx";

export default function ImportExport({ onImported, onClose }) {
  const fileRef = useRef();
  const [replace, setReplace] = useState(false);
  const [msg, setMsg] = useState("");
  const [report, setReport] = useState(null);

  const doExport = async () => {
    const bundle = await api.exportConfig();
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `startpage-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  const doImport = async (file) => {
    setMsg("");
    setReport(null);
    try {
      const bundle = JSON.parse(await file.text());
      if (replace && !confirm("This will delete ALL current dashboards, widgets and services first. Continue?")) return;
      const res = await api.importConfig(bundle, replace);
      setReport({ result: res || {} });
      await onImported();
    } catch (e) {
      setMsg(`Import failed: ${e.message}`);
    }
  };

  return (
    <Modal title="Import / export" onClose={onClose}>
      <div className="stack">
        <section>
          <h3>Export</h3>
          <p className="muted">Download the full configuration (services, dashboards, widgets) as JSON. Keep it as a backup.</p>
          <button className="btn-primary" onClick={doExport}>Download startpage.json</button>
        </section>
        <section>
          <h3>Import</h3>
          <p className="muted">Merge a previously exported file into the current configuration. Items with the same id are updated.</p>
          <label className="check">
            <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
            <span>Replace everything (wipe current config first)</span>
          </label>
          <input ref={fileRef} type="file" accept="application/json" style={{ display: "none" }} onChange={(e) => {
            const file = e.target.files[0];
            e.target.value = ""; // so choosing the same file again (after a failure or a cancelled confirm) fires change
            if (file) doImport(file);
          }} />
          <button onClick={() => fileRef.current.click()}>Choose file…</button>
          {msg && <p className="error">{msg}</p>}
          {report && <ImportReport verb="Imported" result={report.result} />}
        </section>
      </div>
    </Modal>
  );
}
