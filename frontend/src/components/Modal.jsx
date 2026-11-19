import { useEffect, useId, useRef, useState } from "react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** Modals currently open, oldest first: only the topmost one traps Tab. */
const stack = [];

/** The tabbable elements of `root` (not hidden, not inside a closed <details>). */
function focusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter((el) => {
    if (el.closest("[hidden], [inert]")) return false;
    const closed = el.closest("details:not([open])");
    return !closed || el.parentElement === closed; // only the <summary> of a closed <details> is reachable
  });
}

/**
 * The one dialog shell. role=dialog + aria-modal + a labelled title, Escape and a backdrop click close it,
 * focus moves in when it opens (unless something inside already took it, e.g. an autoFocus field), Tab
 * and Shift+Tab cycle inside the topmost dialog, and focus goes back to what opened it when it closes.
 */
export default function Modal({ title, onClose, children, footer, wide }) {
  const dialog = useRef(null);
  const titleId = useId();
  // What had focus when the dialog was rendered: read before the commit, because an autoFocus field takes focus during it.
  const [opener] = useState(() => (typeof document !== "undefined" ? document.activeElement : null));

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const root = dialog.current;
    const me = {};
    stack.push(me);
    if (root && !root.contains(document.activeElement)) {
      const body = root.querySelector(".modal-body");
      const first = (body && focusables(body)[0]) || focusables(root)[0];
      (first || root).focus();
    }
    const onTab = (e) => {
      if (e.key !== "Tab" || stack[stack.length - 1] !== me || !root) return;
      const list = focusables(root);
      if (list.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      const active = document.activeElement;
      if (!root.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && (active === first || active === root)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onTab);
    return () => {
      window.removeEventListener("keydown", onTab);
      stack.splice(stack.indexOf(me), 1);
      // back to what opened the dialog, unless the page already moved focus somewhere real
      if (opener && opener !== document.body && opener.isConnected && (document.activeElement === document.body || !document.activeElement || root?.contains(document.activeElement))) opener.focus?.();
    };
  }, []);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={dialog} className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button className="btn-icon" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
