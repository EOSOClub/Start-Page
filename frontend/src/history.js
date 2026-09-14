import { useCallback, useRef, useState } from "react";

const LIMIT = 100;
const MERGE_MS = 1200;

/**
 * Undo/redo stack of server-side operations.
 * An entry is {label, undo: async () => void, redo: async () => void, mergeKey?}.
 * Entries pushed with the same mergeKey within MERGE_MS collapse into one
 * (e.g. a run of arrow-key nudges), keeping the oldest `undo` and newest `redo`.
 */
export function useHistory() {
  const past = useRef([]);
  const future = useRef([]);
  const busy = useRef(false);
  const [, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);

  const push = useCallback((entry) => {
    const last = past.current[past.current.length - 1];
    const now = Date.now();
    if (entry.mergeKey && last?.mergeKey === entry.mergeKey && now - last.at < MERGE_MS) {
      last.redo = entry.redo;
      last.at = now;
    } else {
      past.current = [...past.current.slice(-(LIMIT - 1)), { ...entry, at: now }];
    }
    future.current = [];
    bump();
  }, []);

  const step = useCallback(async (from, to, action) => {
    if (busy.current || !from.current.length) return null;
    busy.current = true;
    const entry = from.current[from.current.length - 1];
    try {
      await entry[action]();
      from.current = from.current.slice(0, -1);
      to.current = [...to.current, { ...entry, mergeKey: undefined }];
      return entry;
    } finally {
      busy.current = false;
      bump();
    }
  }, []);

  const undo = useCallback(() => step(past, future, "undo"), [step]);
  const redo = useCallback(() => step(future, past, "redo"), [step]);

  return {
    push,
    undo,
    redo,
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
    undoLabel: past.current[past.current.length - 1]?.label,
    redoLabel: future.current[future.current.length - 1]?.label,
  };
}
