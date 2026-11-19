import { useEffect, useState } from "react";
import { api } from "../api.js";

/** Password form. `onLoggedIn` runs after the server accepted the password and set the cookie. */
export function LoginForm({ onLoggedIn }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [wait, setWait] = useState(0); // seconds left after a 429
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (wait <= 0) return;
    const t = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(t);
  }, [wait]);

  const submit = async (e) => {
    e.preventDefault();
    if (busy || wait > 0 || !password) return;
    setBusy(true);
    setError("");
    try {
      await api.login(password);
      setPassword("");
      await onLoggedIn();
    } catch (err) {
      if (err.status === 429) setWait(err.retryAfter || 5);
      else setError(err.status === 401 ? "Wrong password." : err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="sp-login-password">Password</label>
        <input
          id="sp-login-password"
          type="password"
          autoFocus
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      {wait > 0 && <div className="error" role="alert">Too many attempts. Try again in {wait} s.</div>}
      {error && wait <= 0 && <div className="error" role="alert">{error}</div>}
      <button className="btn-primary" type="submit" disabled={busy || wait > 0 || !password}>Sign in</button>
    </form>
  );
}

/** Full-page sign-in, shown instead of the dashboard when guests are not allowed. */
export default function LoginScreen({ onLoggedIn }) {
  return (
    <div className="login-screen">
      <div className="login-card">
        <h1>Sign in</h1>
        <LoginForm onLoggedIn={onLoggedIn} />
      </div>
    </div>
  );
}
