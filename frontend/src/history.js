import { useCallback, useRef, useState } from "react";

const LIMIT = 100;
const MERGE_MS = 1200;

/**
 * Undo/redo stack of server-side operations.
 * An entry is {label, undo: async () => void, redo: async () => void, mergeKey?}.
 * Entries pushed with the same mergeKey within MERGE_MS collapse into one
 * (e.g. a run of arrow-key nudges), keeping the oldest `undo` and newest `redo`.
 * ADR-015: a failing step keeps its entry (keep-and-retry); only an explicit skip() drops it.
 */
export function useHistory() {
  const past = useRef([]);
  const future = useRef([]);
  const busy = useRef(false);
  const failed = useRef(null); // {action, entry} of the last failing step; cleared on push/success
  const [, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);

  const push = useCallback((entry) => {
    const last = past.current[past.current.length - 1];
    const now = Date.now();
    failed.current = null;
    let stored = last;
    if (entry.mergeKey && last?.mergeKey === entry.mergeKey && now - last.at < MERGE_MS) {
      last.redo = entry.redo;
      last.at = now;
    } else {
      stored = { ...entry, at: now };
      past.current = [...past.current.slice(-(LIMIT - 1)), stored];
    }
    future.current = [];
    bump();
    return stored; // the object on the stack, for isTop()
  }, []);

  /** Whether `entry` (as returned by push/undo/redo) is still the next step to undo or redo. */
  const isTop = useCallback(
    (entry) => !!entry && (past.current[past.current.length - 1] === entry || future.current[future.current.length - 1] === entry),
    []
  );

  const step = useCallback(async (from, to, action) => {
    if (busy.current || !from.current.length) return null;
    busy.current = true;
    const entry = from.current[from.current.length - 1];
    try {
      await entry[action]();
      // By identity, not position: a push that landed while the step was in flight is now on top.
      from.current = from.current.filter((e) => e !== entry);
      const moved = { ...entry, mergeKey: undefined };
      to.current = [...to.current, moved];
      failed.current = null;
      return moved;
    } catch (e) {
      failed.current = { action, entry };
      throw e;
    } finally {
      busy.current = false;
      bump();
    }
  }, []);

  const undo = useCallback(() => step(past, future, "undo"), [step]);
  const redo = useCallback(() => step(future, past, "redo"), [step]);

  // Drop the stuck entry from the stack it failed on. Never moved to the other stack.
  const skip = useCallback(() => {
    if (busy.current || !failed.current) return null;
    const from = failed.current.action === "undo" ? past : future;
    failed.current = null;
    bump();
    if (!from.current.length) return null;
    const entry = from.current[from.current.length - 1];
    from.current = from.current.slice(0, -1);
    return entry;
  }, []);

  // Forget everything: steps capture old server state, which an outside edit makes stale.
  const clear = useCallback(() => {
    if (busy.current) return false;
    past.current = [];
    future.current = [];
    failed.current = null;
    bump();
    return true;
  }, []);

  return {
    push,
    isTop,
    clear,
    undo,
    redo,
    skip,
    failedAction: failed.current?.action,
    failedLabel: failed.current?.entry.label,
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
    undoLabel: past.current[past.current.length - 1]?.label,
    redoLabel: future.current[future.current.length - 1]?.label,
  };
}
