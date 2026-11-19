import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const api = vi.hoisted(() => ({ integrationAction: vi.fn() }));
vi.mock("../api.js", () => ({ api }));

import { ConfirmButton, RowActions } from "./actionControls.jsx";
import { PRESETS } from "./integrationPresets.jsx";
import { WIDGET_TYPES, defaultConfig } from "./index.jsx";
import { AppContext } from "../context.js";
import { CONFIRM_MS } from "./actionHelpers.js";
import { dotStatus } from "./integrationViz.js";

beforeEach(() => {
  api.integrationAction.mockReset();
  api.integrationAction.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const OPTIONS = [{ action: "stop", label: "Force stop", danger: true }];

describe("ConfirmButton focus", () => {
  it("moves focus into the confirm group when armed and back to the trigger on No", () => {
    render(<ConfirmButton label="Pause all" onConfirm={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Pause all" }));
    expect(document.activeElement).toBe(within(screen.getByRole("group")).getByRole("button", { name: "Yes" }));
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Pause all" }));
  });

  it("returns focus to the trigger when the timeout disarms it", () => {
    vi.useFakeTimers();
    render(<ConfirmButton label="Pause all" onConfirm={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Pause all" }));
    act(() => vi.advanceTimersByTime(CONFIRM_MS + 10));
    expect(screen.queryByRole("group")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Pause all" }));
  });
});

describe("RowActions", () => {
  it("names the target in the confirm prompt and moves focus along", () => {
    render(<RowActions name="web-proxy" options={OPTIONS} onRun={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for web-proxy" }));
    expect(document.activeElement.getAttribute("aria-label")).toBe("Force stop web-proxy");
    fireEvent.click(screen.getByRole("button", { name: "Force stop web-proxy" }));
    expect(screen.getByText("Force stop web-proxy?")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Confirm" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Actions for web-proxy" }));
  });

  it("disarms by itself after the same timeout as ConfirmButton", () => {
    vi.useFakeTimers();
    const onRun = vi.fn();
    render(<RowActions name="web" options={OPTIONS} onRun={onRun} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for web" }));
    fireEvent.click(screen.getByRole("button", { name: "Force stop web" }));
    act(() => vi.advanceTimersByTime(CONFIRM_MS + 10));
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(onRun).not.toHaveBeenCalled();
  });

  it("disarms when the row's identity changes", () => {
    const onRun = vi.fn();
    const { rerender } = render(<RowActions name="web" identity="a" options={OPTIONS} onRun={onRun} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for web" }));
    fireEvent.click(screen.getByRole("button", { name: "Force stop web" }));
    rerender(<RowActions name="db" identity="b" options={OPTIONS} onRun={onRun} />);
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(screen.getByRole("button", { name: "Actions for db" })).toBeTruthy();
  });
});

describe("an armed row action survives a reorder only on the same guest", () => {
  const guest = (name, vmid) => ({ title: name, subtitle: `pve1 · VM ${vmid}`, status: "online", ref: { node: "pve1", vmid, kind: "qemu" } });
  const mount = (items) => {
    const def = WIDGET_TYPES.proxmox;
    return (
      <AppContext.Provider value={{ canWrite: true }}>
        <def.Render
          widget={{ id: "w", type: "proxmox", config: { integration_id: "i1", ...defaultConfig("proxmox"), show_actions: true } }}
          integrations={[{ id: "i1", name: "pve", type: "proxmox", config: {} }]}
          integrationData={{ i1: { integration_id: "i1", ok: true, fetched_at: 1, error: null, data: { stats: [], items } } }}
          onRefresh={vi.fn()}
        />
      </AppContext.Provider>
    );
  };

  it("Confirm after a reorder runs against the guest that was armed, not the new row at that position", async () => {
    expect(PRESETS.proxmox).toBeTruthy();
    const a = guest("web-proxy", "100");
    const b = guest("db", "101");
    const { rerender } = render(mount([a, b]));
    fireEvent.click(screen.getByRole("button", { name: "Actions for web-proxy" }));
    fireEvent.click(screen.getByRole("button", { name: "Force stop web-proxy" }));
    rerender(mount([b, a])); // a poll reordered the rows
    const group = screen.getByRole("group", { name: "Confirm Force stop web-proxy" });
    expect(screen.getByText("Force stop web-proxy?")).toBeTruthy();
    await act(async () => fireEvent.click(within(group).getByRole("button", { name: "Confirm" })));
    expect(api.integrationAction).toHaveBeenCalledExactlyOnceWith("i1", "guest_power", { node: "pve1", kind: "qemu", vmid: "100", action: "stop" });
  });

  it("disarms when the armed guest disappears from the list", () => {
    const a = guest("web-proxy", "100");
    const b = guest("db", "101");
    const { rerender } = render(mount([a, b]));
    fireEvent.click(screen.getByRole("button", { name: "Actions for web-proxy" }));
    fireEvent.click(screen.getByRole("button", { name: "Force stop web-proxy" }));
    rerender(mount([b]));
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(screen.getByRole("button", { name: "Actions for db" })).toBeTruthy();
  });
});

describe("status class allowlist", () => {
  it("passes the five known statuses and maps anything else to unknown", () => {
    for (const s of ["online", "offline", "degraded", "unknown", "disabled"]) expect(dotStatus(s)).toBe(s);
    expect(dotStatus("maintenance")).toBe("degraded");
    expect(dotStatus("modal-backdrop")).toBe("unknown");
    expect(dotStatus("online widget-overlay")).toBe("unknown");
    expect(dotStatus({})).toBe("unknown");
    expect(dotStatus(undefined)).toBe("unknown");
  });
});
