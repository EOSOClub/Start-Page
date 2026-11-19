import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../api.js", () => ({ api: { integrationTypes: vi.fn().mockResolvedValue([]) } }));

import { carryConfig, defaultConfig, migrateConfig } from "./index.jsx";
import { migrateCalendar } from "./calendarWidget.jsx";
import WidgetEditor from "../components/WidgetEditor.jsx";

afterEach(cleanup);

describe("carryConfig (switching the type of an unsaved widget)", () => {
  it("drops another type's stale defaults instead of keeping them", () => {
    const next = carryConfig("integration_list", "home_assistant", { integration_id: "i1", layout: "tiles", limit: 10, title: "Hi" });
    expect(next.layout).toBe("grid"); // not the stale "tiles"
    expect(next).not.toHaveProperty("limit");
    expect(next.title).toBe("Hi"); // both declare a text title
  });
  it("keeps a valid shared value and rejects an invalid one", () => {
    expect(carryConfig("home_assistant", "home_assistant", { layout: "list" }).layout).toBe("list");
    const next = carryConfig("integration_stats", "home_assistant", { layout: "tiles", columns: 3 });
    expect(next.layout).toBe("grid"); // "tiles" is not an option of Home Assistant's select
  });
  it("does not carry an integration pick to a different kind of integration", () => {
    expect(carryConfig("sonarr", "radarr", { integration_id: "i-sonarr" })).not.toHaveProperty("integration_id");
    expect(carryConfig("sonarr", "sonarr", { integration_id: "i1" }).integration_id).toBe("i1");
  });
  it("starts from the new type's defaults", () => {
    expect(carryConfig("text", "gauge", {})).toEqual(defaultConfig("gauge"));
  });
});

describe("WidgetEditor type switch", () => {
  it("rebuilds the config of a new widget from the chosen type", () => {
    const onSave = vi.fn();
    render(<WidgetEditor widget={{ type: "integration_stats", config: { layout: "tiles", show_sparkline: true } }} services={[]} onSave={onSave} onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "home_assistant" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onSave.mock.calls[0][0].type).toBe("home_assistant");
    expect(onSave.mock.calls[0][0].config.layout).toBe("grid");
    expect(onSave.mock.calls[0][0].config).not.toHaveProperty("show_sparkline");
  });
});

describe("calendar config migration", () => {
  it("folds integration_id into integration_ids and drops it", () => {
    expect(migrateCalendar({ integration_id: "a", days: 3 })).toEqual({ days: 3, integration_ids: ["a"] });
    expect(migrateCalendar({ integration_id: "a", integration_ids: ["a", "b"] })).toEqual({ integration_ids: ["a", "b"] });
    expect(migrateCalendar({ integration_id: "a", integration_ids: ["b"] })).toEqual({ integration_ids: ["b", "a"] });
    const clean = { integration_ids: ["x"] };
    expect(migrateCalendar(clean)).toBe(clean);
    expect(migrateConfig("calendar", { integration_id: "z" })).toEqual({ integration_ids: ["z"] });
    expect(migrateConfig("text", { content: "a" })).toEqual({ content: "a" });
  });

  it("the editor saves a migrated config without the undeclared key", () => {
    const onSave = vi.fn();
    render(<WidgetEditor widget={{ id: "w1", type: "calendar", config: { integration_id: "a", days: 5 } }} services={[]} onSave={onSave} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][0].config).toEqual({ days: 5, integration_ids: ["a"] });
  });
});
