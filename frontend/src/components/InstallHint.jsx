import { useState } from "react";
import { promptInstall, useInstallState } from "../pwa.js";

/** The "Install as an app" block in Settings: a button when the browser offers one, else what to do by hand. */
export default function InstallHint() {
  const state = useInstallState();
  const [msg, setMsg] = useState("");
  const install = async () => {
    const outcome = await promptInstall();
    setMsg(outcome === "accepted" ? "Installing…" : outcome === "dismissed" ? "Install cancelled." : "");
  };
  return (
    <div className="install-hint" data-state={state}>
      <h4 style={{ margin: "16px 0 6px" }}>Install as an app</h4>
      {state === "installed" && <p className="muted">Start Page is running as an installed app.</p>}
      {state === "prompt" && (
        <p>
          <button className="btn-primary" onClick={install}>Install Start Page</button> {msg && <span className="muted">{msg}</span>}
        </p>
      )}
      {state === "ios" && <p className="muted">On iPhone or iPad: tap Share, then “Add to Home Screen”.</p>}
      {state === "manual" && <p className="muted">Use your browser’s menu: “Install app”, or “Add to Home screen” on a phone.</p>}
      {state === "insecure" && <p className="muted">This page isn’t on a secure connection, so it can’t be installed and has no offline support.</p>}
      <p className="muted">Installing and offline support need HTTPS (or http://localhost).</p>
    </div>
  );
}
