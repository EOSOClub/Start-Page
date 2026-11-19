import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ integrationAction: vi.fn() }));
vi.mock("../api.js", () => ({ api }));

import { HOLD_MS, HomeAssistantWidget } from "./homeAssistant.jsx";
import { WIDGET_TYPES, defaultConfig } from "./index.jsx";
import { AppContext } from "../context.js";

afterEach(cleanup);
beforeEach(() => {
  api.integrationAction.mockReset(); // not returned: a returned function would run as teardown
});

const E = (entity_id, label, state, extra = {}) => ({
  entity_id, label, domain: entity_id.split(".")[0], state, unit: null, toggleable: false, attributes: {}, ...extra,
});
const ENTITIES = [
  E("light.kitchen", "Kitchen light", "on", { toggleable: true }),
  E("switch.fan", "Fan", "off", { toggleable: true }),
  E("cover.garage", "Garage door", "closed", { toggleable: true }),
  E("scene.movie", "Movie night", "scening"),
  E("button.doorbell", "Ring doorbell", "unknown"),
  E("script.goodnight", "Goodnight", "off"),
  E("sensor.temp", "Outside", "12.5", { unit: "°C" }),
  E("sensor.gone", "Old sensor", "unavailable", { unit: "W" }),
  E("light.broken", "Broken light", "unavailable", { toggleable: true }),
];
const INTEG = [{ id: "i1", name: "Home", type: "home_assistant", config: {} }];
const ok = (entities = ENTITIES) => ({ i1: { integration_id: "i1", ok: true, fetched_at: 1, error: null, data: { stats: [], items: [], entities } } });

function show(config = {}, data = ok(), { canWrite = true, onRefresh = vi.fn() } = {}) {
  return render(
    <AppContext.Provider value={{ canWrite }}>
      <HomeAssistantWidget.Render widget={{ id: "w", config: { integration_id: "i1", ...defaultConfig("home_assistant"), ...config } }} integrations={INTEG} integrationData={data} onRefresh={onRefresh} />
    </AppContext.Provider>
  );
}
const sw = (name) => screen.getByRole("switch", { name });

describe("Home Assistant widget", () => {
  it("is registered in the Integrations group with layout, columns, compact and entity subset fields", () => {
    expect(WIDGET_TYPES.home_assistant.group).toBe("Integrations");
    expect(WIDGET_TYPES.home_assistant.fields.map((f) => f.key)).toEqual(["integration_id", "title", "layout", "columns", "compact", "entity_ids"]);
    expect(WIDGET_TYPES.home_assistant.fields[0]).toMatchObject({ integrationType: "home_assistant" });
  });

  it("shows switches, press buttons, read-only values and unavailable entities", () => {
    show();
    expect(sw("Kitchen light").getAttribute("aria-checked")).toBe("true");
    expect(sw("Fan").getAttribute("aria-checked")).toBe("false");
    expect(sw("Garage door").getAttribute("aria-checked")).toBe("false"); // closed = off
    expect(screen.getByRole("button", { name: "Activate Movie night" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Press Ring doorbell" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run Goodnight" })).toBeTruthy();
    expect(screen.getByText("12.5")).toBeTruthy();
    expect(screen.getByText("°C")).toBeTruthy();
    // unavailable: no control, text and dimmed styling
    expect(screen.queryByRole("switch", { name: "Broken light" })).toBeNull();
    const dim = screen.getByText("Broken light").closest(".ha-entity");
    expect(dim.classList.contains("ha-unavailable")).toBe(true);
    expect(dim.textContent).toContain("unavailable");
    expect(screen.getByText("Old sensor").closest(".ha-entity").classList.contains("ha-unavailable")).toBe(true);
  });

  it("is read-only for view-only guests: no switches, no buttons, values as text", () => {
    show({}, ok(), { canWrite: false });
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("12.5")).toBeTruthy();
    expect(screen.getByText("On")).toBeTruthy(); // the kitchen light
  });

  it("toggles optimistically, calls the action, then refreshes", async () => {
    let finish;
    api.integrationAction.mockImplementation(() => new Promise((r) => { finish = r; }));
    const onRefresh = vi.fn().mockResolvedValue();
    show({}, ok(), { onRefresh });
    fireEvent.click(sw("Fan"));
    expect(sw("Fan").getAttribute("aria-checked")).toBe("true"); // flipped before the server answered
    expect(sw("Fan").disabled).toBe(true); // busy
    expect(api.integrationAction).toHaveBeenCalledWith("i1", "toggle", { entity_id: "switch.fan" });
    expect(sw("Kitchen light").disabled).toBe(false); // other entities stay usable
    finish({});
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(sw("Fan").disabled).toBe(false));
  });

  it("rolls the switch back and shows the error when the action fails", async () => {
    api.integrationAction.mockRejectedValue(new Error("entity is not in this integration's entity list"));
    const onRefresh = vi.fn();
    show({}, ok(), { onRefresh });
    fireEvent.click(sw("Kitchen light"));
    expect(sw("Kitchen light").getAttribute("aria-checked")).toBe("false"); // optimistic
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Kitchen light: entity is not in this integration's entity list");
    await waitFor(() => expect(sw("Kitchen light").getAttribute("aria-checked")).toBe("true")); // rolled back
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("covers flip between open and closed", async () => {
    api.integrationAction.mockResolvedValue({});
    show();
    fireEvent.click(sw("Garage door"));
    expect(sw("Garage door").getAttribute("aria-checked")).toBe("true");
    await waitFor(() => expect(api.integrationAction).toHaveBeenCalledWith("i1", "toggle", { entity_id: "cover.garage" }));
  });

  it("press buttons call press, disable while busy and report errors", async () => {
    let fail;
    api.integrationAction.mockImplementation(() => new Promise((_, rej) => { fail = rej; }));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Press Ring doorbell" }));
    expect(screen.getByRole("button", { name: "Press Ring doorbell" }).disabled).toBe(true);
    expect(api.integrationAction).toHaveBeenCalledWith("i1", "press", { entity_id: "button.doorbell" });
    fail(new Error("HTTP 500 Internal Server Error (ha:8123)"));
    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 500");
    await waitFor(() => expect(screen.getByRole("button", { name: "Press Ring doorbell" }).disabled).toBe(false));
  });

  it("shows a chosen subset in the chosen order, list layout and compact mode", () => {
    const { container } = show({ entity_ids: "sensor.temp\nlight.kitchen, nope.x", layout: "list", compact: true });
    expect([...container.querySelectorAll(".ha-label")].map((e) => e.textContent)).toEqual(["Outside", "Kitchen light"]);
    expect(container.querySelector(".ha-grid").className).toContain("ha-layout-list");
    expect(container.querySelector(".ha-grid").className).toContain("ha-compact");
  });

  it("sets the grid columns", () => {
    const { container } = show({ columns: 3 });
    expect(container.querySelector(".ha-grid").style.gridTemplateColumns).toBe("repeat(3, minmax(0, 1fr))");
  });

  it("states: nothing selected, waiting, error, empty", () => {
    show({ integration_id: null }, {});
    expect(screen.getByText(/Select a Home Assistant integration/)).toBeTruthy();
    cleanup();
    show({}, {});
    expect(screen.getByText("Waiting for first fetch…")).toBeTruthy();
    cleanup();
    show({}, { i1: { ok: false, error: "login failed: check the Home Assistant access token", data: null } });
    expect(screen.getByText("Error: login failed: check the Home Assistant access token")).toBeTruthy();
    cleanup();
    show({ entity_ids: "light.nothing" });
    expect(screen.getByText("No entities to show.")).toBeTruthy();
  });
});

describe("optimistic state versus polls that were already in flight", () => {
  const tree = (data, onRefresh) => (
    <AppContext.Provider value={{ canWrite: true }}>
      <HomeAssistantWidget.Render widget={{ id: "w", config: { integration_id: "i1", ...defaultConfig("home_assistant") } }} integrations={INTEG} integrationData={data} onRefresh={onRefresh} />
    </AppContext.Provider>
  );
  const at = (fetched_at, entities = ENTITIES) => ({ i1: { ...ok(entities).i1, fetched_at } });

  it("keeps the toggled value when an older poll lands after the refresh, and accepts the first poll that started after the action", async () => {
    api.integrationAction.mockResolvedValue({});
    const onRefresh = vi.fn().mockResolvedValue();
    const { rerender } = render(tree(at(1), onRefresh));
    fireEvent.click(sw("Fan")); // off -> on
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(sw("Fan").disabled).toBe(false));
    // the stale poll (fetched before the action) arrives now with the old state
    rerender(tree(at(2), onRefresh));
    expect(sw("Fan").getAttribute("aria-checked")).toBe("true");
    // a poll that started after the action says the fan really is on
    const fresh = ENTITIES.map((e) => (e.entity_id === "switch.fan" ? { ...e, state: "on" } : e));
    rerender(tree(at(Date.now() / 1000 + 5, fresh), onRefresh));
    expect(sw("Fan").getAttribute("aria-checked")).toBe("true");
  });

  it("lets go after the grace period even if no newer poll arrives, and rolls back at once on an error", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      api.integrationAction.mockResolvedValue({});
      const { rerender } = render(tree(at(1), vi.fn().mockResolvedValue()));
      fireEvent.click(sw("Fan"));
      await vi.waitFor(() => expect(sw("Fan").disabled).toBe(false));
      expect(sw("Fan").getAttribute("aria-checked")).toBe("true");
      await vi.advanceTimersByTimeAsync(HOLD_MS + 50);
      rerender(tree(at(1), vi.fn()));
      expect(sw("Fan").getAttribute("aria-checked")).toBe("false");
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not throw on a non-string state", () => {
    const odd = [E("switch.x", "Odd", { weird: 1 }, { toggleable: true, domain: "scene" }), E("scene.y", "Odd scene", 42), E("sensor.z", "Nothing", null)];
    expect(() => show({}, ok(odd), { canWrite: false })).not.toThrow();
    expect(screen.getByText("Odd scene")).toBeTruthy();
  });
});
