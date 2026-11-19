import { useEffect, useState } from "react";
import Modal from "./Modal.jsx";
import { api } from "../api.js";
import { contentsLine, exportFileName, withoutWarnings } from "./templateMap.js";

export function downloadJson(doc, fileName) {
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fileName;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/**
 * "Export page": fetches the portable JSON, says what is in it (always) and lists the server's warnings (secret-looking
 * addresses, removed service details, custom code); the download waits for a click either way.
 */
export default function ExportPageDialog({ dashboard, onClose }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState("");
  const warnings = Array.isArray(doc?.warnings) ? doc.warnings.filter((w) => typeof w === "string") : [];

  const save = (d) => {
    downloadJson(withoutWarnings(d), exportFileName(dashboard.name));
    onClose();
  };
  useEffect(() => {
    let live = true;
    api
      .exportPage(dashboard.id)
      .then((d) => {
        if (live) setDoc(d);
      })
      .catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [dashboard.id]); // eslint-disable-line

  return (
    <Modal
      title="Export page"
      onClose={onClose}
      footer={
        <>
          <span className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!doc} onClick={() => save(doc)}>{warnings.length ? "Download anyway" : "Download"}</button>
        </>
      }
    >
      {error && <div className="error" role="alert">{error}</div>}
      {!doc && !error && <div className="muted">Preparing…</div>}
      {doc && (
        <>
          <p data-testid="export-contents">This file contains: {contentsLine(doc)}</p>
          {warnings.length > 0 && (
            <>
              <p>Check these before you share the file:</p>
              <ul className="np-warnings" aria-label="Warnings">
                {warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </>
          )}
          <p className="help">Integration settings and keys are never part of the file.</p>
        </>
      )}
    </Modal>
  );
}
