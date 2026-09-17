import React from "react";
import ReactDOM from "react-dom/client";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./styles.css";
import App from "./App.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";

/*
 * Last line of defence for a throw in App's own render. It renders outside AppContext,
 * so it must use no context, registry or Icon, and must not be able to throw itself.
 * The cache only stores committed state, so a crashing cache comes from version skew
 * (a cache written by an older build); a crash that survives the reset is in the saved data.
 */
const clearCacheAndReload = () => {
  for (const key of ["sp.cache.v1", "sp.dashboard"]) {
    try {
      localStorage.removeItem(key);
    } catch {
      // Storage can be blocked; reloading is still the best we can do.
    }
  }
  location.reload();
};

const messageOf = (error) => {
  try {
    return String(error?.message ?? error);
  } catch {
    return "Unknown error";
  }
};

const AppCrashed = (error) => (
  <div className="canvas-empty">
    <p>Start Page hit an error.</p>
    <p className="muted">
      Clearing the local cache fixes problems left over from an older version. If it keeps happening, the saved data itself is the problem.
    </p>
    <p className="muted">{messageOf(error)}</p>
    {/* .canvas-empty sets pointer-events: none, which the button would inherit. */}
    <button className="btn-primary" style={{ pointerEvents: "auto" }} onClick={clearCacheAndReload}>
      Clear local cache and reload
    </button>
  </div>
);

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ErrorBoundary fallback={AppCrashed}>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
