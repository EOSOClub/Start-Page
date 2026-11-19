import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useHistory } from "./history.js";

const entry = (label, extra = {}) => ({ label, undo: vi.fn(async () => {}), redo: vi.fn(async () => {}), ...extra });

describe("useHistory", () => {
  it("undo then redo moves the entry across stacks", async () => {
    const { result } = renderHook(() => useHistory());
    const e = entry("A");
    act(() => result.current.push(e));
    expect(result.current.canUndo).toBe(true);
    await act(() => result.current.undo());
    expect(e.undo).toHaveBeenCalledOnce();
    expect(result.current.canUndo).toBe(false);
    expect(result.current.redoLabel).toBe("A");
    await act(() => result.current.redo());
    expect(e.redo).toHaveBeenCalledOnce();
    expect(result.current.undoLabel).toBe("A");
    expect(result.current.canRedo).toBe(false);
  });

  it("a push clears the redo stack", async () => {
    const { result } = renderHook(() => useHistory());
    act(() => result.current.push(entry("A")));
    await act(() => result.current.undo());
    expect(result.current.canRedo).toBe(true);
    act(() => result.current.push(entry("B")));
    expect(result.current.canRedo).toBe(false);
  });

  it("same mergeKey within the window collapses: oldest undo, newest redo", async () => {
    const { result } = renderHook(() => useHistory());
    const a = entry("nudge", { mergeKey: "k" });
    const b = entry("nudge", { mergeKey: "k" });
    act(() => result.current.push(a));
    act(() => result.current.push(b));
    await act(() => result.current.undo());
    expect(a.undo).toHaveBeenCalledOnce();
    expect(b.undo).not.toHaveBeenCalled();
    expect(result.current.canUndo).toBe(false);
    await act(() => result.current.redo());
    expect(b.redo).toHaveBeenCalledOnce();
    expect(a.redo).not.toHaveBeenCalled();
  });

  it("ADR-015: a failing undo keeps the entry (keep-and-retry, never pop)", async () => {
    const { result } = renderHook(() => useHistory());
    const a = entry("A");
    const b = entry("B", { undo: vi.fn().mockRejectedValueOnce(new Error("503")).mockResolvedValue() });
    act(() => result.current.push(a));
    act(() => result.current.push(b));
    await act(async () => {
      await expect(result.current.undo()).rejects.toThrow("503");
    });
    expect(result.current.undoLabel).toBe("B");
    expect(result.current.canRedo).toBe(false);
    // Retry succeeds on the same entry; the older entry is untouched.
    await act(() => result.current.undo());
    expect(b.undo).toHaveBeenCalledTimes(2);
    expect(a.undo).not.toHaveBeenCalled();
    expect(result.current.undoLabel).toBe("A");
    expect(result.current.redoLabel).toBe("B");
  });

  const failUndo = async (result) => {
    await act(async () => {
      await expect(result.current.undo()).rejects.toThrow("gone");
    });
  };

  it("(a) skip after a failing undo drops only that entry and never moves it to redo", async () => {
    const { result } = renderHook(() => useHistory());
    const a = entry("A");
    const b = entry("B", { undo: vi.fn().mockRejectedValue(new Error("gone")) });
    act(() => result.current.push(a));
    act(() => result.current.push(b));
    await failUndo(result);
    expect(result.current.failedLabel).toBe("B");
    expect(result.current.failedAction).toBe("undo");
    let skipped;
    act(() => { skipped = result.current.skip(); });
    expect(skipped.label).toBe("B");
    expect(result.current.undoLabel).toBe("A");
    expect(result.current.canRedo).toBe(false);
    expect(result.current.failedLabel).toBeUndefined();
    expect(b.undo).toHaveBeenCalledOnce();
    await act(() => result.current.undo());
    expect(a.undo).toHaveBeenCalledOnce();
    expect(result.current.redoLabel).toBe("A");
    expect(result.current.canUndo).toBe(false);
  });

  it("(b) skip is a no-op without a live failure (none, after push, after a successful retry)", async () => {
    const { result } = renderHook(() => useHistory());
    let skipped;
    // No failure at all.
    act(() => result.current.push(entry("A")));
    act(() => { skipped = result.current.skip(); });
    expect(skipped).toBeNull();
    expect(result.current.undoLabel).toBe("A");
    // Failure, then a push: the record is stale and must not drop C.
    act(() => result.current.push(entry("B", { undo: vi.fn().mockRejectedValue(new Error("gone")) })));
    await failUndo(result);
    expect(result.current.failedLabel).toBe("B");
    act(() => result.current.push(entry("C")));
    expect(result.current.failedLabel).toBeUndefined();
    act(() => { skipped = result.current.skip(); });
    expect(skipped).toBeNull();
    expect(result.current.undoLabel).toBe("C");
    // Failure, then a successful retry: the record is cleared.
    act(() => result.current.push(entry("D", { undo: vi.fn().mockRejectedValueOnce(new Error("gone")).mockResolvedValue() })));
    await failUndo(result);
    expect(result.current.failedLabel).toBe("D");
    await act(() => result.current.undo());
    expect(result.current.failedLabel).toBeUndefined();
    act(() => { skipped = result.current.skip(); });
    expect(skipped).toBeNull();
    expect(result.current.undoLabel).toBe("C");
    expect(result.current.redoLabel).toBe("D");
  });

  it("(c) skip after a failing redo drops only the redo top and leaves undo alone", async () => {
    const { result } = renderHook(() => useHistory());
    const a = entry("A");
    const b = entry("B", { redo: vi.fn().mockRejectedValue(new Error("gone")) });
    act(() => result.current.push(a));
    act(() => result.current.push(b));
    await act(() => result.current.undo());
    expect(result.current.undoLabel).toBe("A");
    expect(result.current.redoLabel).toBe("B");
    await act(async () => {
      await expect(result.current.redo()).rejects.toThrow("gone");
    });
    expect(result.current.failedAction).toBe("redo");
    expect(result.current.failedLabel).toBe("B");
    let skipped;
    act(() => { skipped = result.current.skip(); });
    expect(skipped.label).toBe("B");
    expect(result.current.canRedo).toBe(false);
    expect(result.current.canUndo).toBe(true);
    expect(result.current.undoLabel).toBe("A");
    expect(result.current.failedLabel).toBeUndefined();
  });

  it("(d) skip is refused while a retry is in flight (busy guard)", async () => {
    const { result } = renderHook(() => useHistory());
    let finishRetry;
    const b = entry("B", {
      undo: vi.fn()
        .mockRejectedValueOnce(new Error("gone"))
        .mockImplementationOnce(() => new Promise((resolve) => { finishRetry = resolve; })),
    });
    act(() => result.current.push(entry("A")));
    act(() => result.current.push(b));
    await failUndo(result);
    expect(result.current.failedLabel).toBe("B");
    let retry;
    act(() => { retry = result.current.undo(); });
    let skipped;
    act(() => { skipped = result.current.skip(); });
    expect(skipped).toBeNull();
    expect(result.current.undoLabel).toBe("B");
    await act(async () => { finishRetry(); await retry; });
    // The retry won: B moved to redo as a normal undo, nothing was dropped.
    expect(result.current.undoLabel).toBe("A");
    expect(result.current.redoLabel).toBe("B");
    expect(result.current.failedLabel).toBeUndefined();
  });

  it("a push that lands during an in-flight undo is kept; only the undone entry moves", async () => {
    const { result } = renderHook(() => useHistory());
    let finishUndo;
    act(() => result.current.push(entry("A")));
    act(() => result.current.push(entry("B", { undo: vi.fn(() => new Promise((resolve) => { finishUndo = resolve; })) })));
    let undoing;
    act(() => { undoing = result.current.undo(); });
    act(() => result.current.push(entry("C"))); // e.g. a drag whose save resolved first
    await act(async () => { finishUndo(); await undoing; });
    // Used to slice off the top by position: C vanished and B sat on both stacks.
    expect(result.current.undoLabel).toBe("C");
    expect(result.current.redoLabel).toBe("B");
    await act(() => result.current.undo());
    expect(result.current.undoLabel).toBe("A");
  });

  it("isTop() is true only while that step is next to undo or redo", async () => {
    const { result } = renderHook(() => useHistory());
    let a;
    act(() => { a = result.current.push(entry("A")); });
    expect(result.current.isTop(a)).toBe(true);
    let b;
    act(() => { b = result.current.push(entry("B")); });
    expect(result.current.isTop(a)).toBe(false); // a toast's "Undo" for A must not undo B
    expect(result.current.isTop(b)).toBe(true);
    let undone;
    await act(async () => { undone = await result.current.undo(); });
    expect(result.current.isTop(undone)).toBe(true); // B is next to redo
    act(() => result.current.push(entry("C")));
    expect(result.current.isTop(undone)).toBe(false); // the redo stack was cleared
    expect(result.current.isTop(null)).toBe(false);
  });

  it("clear() empties both stacks and the failed step", async () => {
    const { result } = renderHook(() => useHistory());
    act(() => result.current.push(entry("A")));
    act(() => result.current.push(entry("B", { undo: vi.fn(async () => { throw new Error("gone"); }) })));
    await act(() => result.current.undo().catch(() => {}));
    await act(() => result.current.undo().catch(() => {}));
    expect(result.current.failedAction).toBe("undo");
    act(() => result.current.clear());
    expect(result.current.canUndo).toBe(false);
    expect(result.current.canRedo).toBe(false);
    expect(result.current.failedAction).toBeUndefined();
  });
});
