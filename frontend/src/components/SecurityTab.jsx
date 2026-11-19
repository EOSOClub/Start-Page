import { useEffect, useState } from "react";
import { api } from "../api.js";

const when = (iso) => (iso ? new Date(iso + "Z").toLocaleString() : "never");

/** Settings -> Security: password, guest access, API tokens. `auth` is the last /auth/status. */
export default function SecurityTab({ auth, onChanged }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [msg, setMsg] = useState("");
  const [tokens, setTokens] = useState([]);
  const [tokenName, setTokenName] = useState("");
  const [fresh, setFresh] = useState(null); // {name, token}: the plaintext, shown once
  const [copied, setCopied] = useState(false);

  // Tokens and guest access only exist while authentication is on (the server answers 409 otherwise).
  const canManage = !!auth.enabled;
  const whyDisabled = auth.has_password
    ? "Authentication is switched off by STARTPAGE_AUTH_DISABLE on the server, so this is unavailable."
    : "Set a password first. Tokens and guest access only exist while authentication is on, and anything created before the first password is discarded.";

  const loadTokens = () => (canManage ? api.listTokens().then((t) => setTokens(t || [])).catch(() => {}) : setTokens([]));
  useEffect(() => {
    loadTokens();
  }, [canManage]); // eslint-disable-line

  const guard = (fn) => async (...args) => {
    setMsg("");
    try {
      await fn(...args);
    } catch (e) {
      setMsg(e.status === 429 ? `Too many attempts. Try again in ${e.retryAfter || 5} s.` : e.message);
    }
  };

  const savePassword = guard(async () => {
    if (next !== again) throw new Error("The new passwords do not match.");
    await api.setPassword(current, next);
    setCurrent("");
    setNext("");
    setAgain("");
    setMsg(auth.has_password ? "Password changed. Other browsers were signed out." : "Password set. You are signed in.");
    await onChanged();
  });

  const removePassword = guard(async () => {
    if (!confirm("Remove the password? Anyone who can reach this page will have full access. All API tokens are deleted and guest access is reset.")) return;
    await api.removePassword(current);
    setCurrent("");
    setMsg("Password removed. Authentication is off.");
    await onChanged();
  });

  const setGuest = guard(async (mode) => {
    await api.setGuestAccess(mode);
    await onChanged();
  });

  const createToken = guard(async () => {
    const t = await api.createToken(tokenName.trim() || "token");
    setFresh({ name: t.name, token: t.token });
    setCopied(false);
    setTokenName("");
    await loadTokens();
  });

  const revoke = (t) =>
    guard(async () => {
      if (!confirm(`Revoke token "${t.name}"? Anything using it stops working.`)) return;
      await api.deleteToken(t.id);
      if (fresh?.name === t.name) setFresh(null);
      await loadTokens();
    })();

  const revokeAll = guard(async () => {
    if (!confirm("Revoke every API token? Anything using one stops working.")) return;
    await api.revokeAllTokens();
    setFresh(null);
    await loadTokens();
  });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fresh.token);
      setCopied(true);
    } catch {
      setMsg("Copy failed; select the token and copy it by hand.");
    }
  };

  return (
    <>
      <div className="sec-section">
        <h3>Password {auth.enabled ? "(on)" : "(off)"}</h3>
        <p className="help">
          {auth.has_password
            ? "Everyone must sign in to use this page. Changing the password signs out every other browser but keeps API tokens: they are separate credentials, so use Revoke all tokens below if you want them gone. Removing the password deletes all tokens."
            : "No password is set, so anyone who can reach this page has full access. Setting one turns authentication on."}
        </p>
        <div className="form">
          {auth.has_password && (
            <div className="field">
              <label htmlFor="sec-current">Current password</label>
              <input id="sec-current" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
            </div>
          )}
          <div className="field">
            <label htmlFor="sec-new">New password (at least 8 characters)</label>
            <input id="sec-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="sec-again">Repeat new password</label>
            <input id="sec-again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
          </div>
          <div className="row">
            <button className="btn-primary" onClick={savePassword} disabled={next.length < 8 || !next.trim()}>{auth.has_password ? "Change password" : "Set password"}</button>
            {auth.has_password && <button className="btn-danger" onClick={removePassword} disabled={!current}>Remove password</button>}
            {msg && <span className="muted">{msg}</span>}
          </div>
        </div>
      </div>

      <div className="sec-section">
        <h3>Guest access</h3>
        <div className="field">
          <select aria-label="Guest access" value={auth.guest_access} disabled={!canManage} onChange={(e) => setGuest(e.target.value)}>
            <option value="none">Nobody: sign in required</option>
            <option value="read">View only: anyone can look, only you can change</option>
          </select>
        </div>
        <p className="help">
          View-only guests never see integration keys, the notification URL, backups or exports, and cannot trigger Docker actions or checks. Applies once a password is set.
        </p>
        {!canManage && <p className="help" role="note">{whyDisabled}</p>}
      </div>

      <div className="sec-section">
        <h3>API tokens</h3>
        <p className="help">For scripts and the Claude MCP server (set it as <code>STARTPAGE_TOKEN</code> in <code>.mcp.json</code>). Sent as <code>Authorization: Bearer …</code>. Shown once.</p>
        {!canManage && <p className="help" role="note">{whyDisabled}</p>}
        {fresh && (
          <div className="token-once">
            <code>{fresh.token}</code>
            <button className="btn-sm" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
          </div>
        )}
        <table className="svc-table">
          <tbody>
            {tokens.length === 0 && <tr><td className="muted">No tokens yet.</td></tr>}
            {tokens.map((t) => (
              <tr key={t.id}>
                <td><strong>{t.name}</strong></td>
                <td className="muted nowrap">created {when(t.created_at)}</td>
                <td className="muted nowrap">used {when(t.last_used)}</td>
                <td className="right"><button className="btn-sm btn-danger" onClick={() => revoke(t)} disabled={!canManage} aria-label={`Revoke ${t.name}`}>Revoke</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 8 }}>
          <input type="text" aria-label="Token name" placeholder="Name, e.g. claude-mcp" value={tokenName} disabled={!canManage} onChange={(e) => setTokenName(e.target.value)} />
          <button onClick={createToken} disabled={!canManage}>Create token</button>
          <button className="btn-danger" onClick={revokeAll} disabled={!canManage || tokens.length === 0}>Revoke all tokens</button>
        </div>
      </div>

      <p className="help">
        Behind an auth proxy (Authelia, Authentik…): set <code>STARTPAGE_TRUSTED_PROXIES</code> and <code>STARTPAGE_AUTH_HEADER</code> (e.g. <code>Remote-User</code>) on the server.
        Locked out? Set <code>STARTPAGE_AUTH_DISABLE=1</code> and restart, or run <code>python -m app.auth reset</code> in <code>backend/</code>.
        This page does not provide HTTPS; terminate TLS in a reverse proxy.
      </p>
    </>
  );
}
