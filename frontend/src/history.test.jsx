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
});
