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
});
