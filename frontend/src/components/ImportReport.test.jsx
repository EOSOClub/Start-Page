import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  exportConfig: vi.fn(), importConfig: vi.fn(), listBackups: vi.fn(), restoreBackup: vi.fn(),
}));
vi.mock("../api.js", () => ({ api }));

import ImportReport, { countsText, outcomeText, warningsOf } from "./ImportReport.jsx";
import ImportExport from "./ImportExport.jsx";
import SettingsModal from "./SettingsModal.jsx";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

const NEW_SHAPE = { counts: { services: 2, dashboards: 1, widgets: 5, integrations: 0 }, warnings: ["Service \"A\": protocol reset to http.", "Setting \"x\" was not imported (invalid value)."] };
// an older server echoed the whole bundle (with secrets) next to `warnings`
const OLD_SHAPE = { services: [{ name: "S" }], dashboards: [], integrations: [{ config: { api_key: "SECRET-KEY" } }], settings: {}, warnings: ["one"] };

describe("import/restore outcome helpers", () => {
  it("reads warnings and counts from either shape, never the echoed bundle", () => {
    expect(warningsOf(NEW_SHAPE)).toHaveLength(2);
    expect(warningsOf(OLD_SHAPE)).toEqual(["one"]);
    expect(warningsOf(null)).toEqual([]);
    expect(warningsOf({ warnings: "nope" })).toEqual([]);
    expect(countsText(NEW_SHAPE)).toBe("2 services, 1 page, 5 widgets, 0 integrations");
    expect(countsText(OLD_SHAPE)).toBe("");
    expect(outcomeText("Restored", NEW_SHAPE)).toBe("Restored with 2 changes.");
    expect(outcomeText("Restored", { warnings: ["a"] })).toBe("Restored with 1 change.");
    expect(outcomeText("Imported", { counts: {}, warnings: [] })).toBe("Imported.");
  });

  it("renders the count and an expandable list of warnings as text", () => {
    render(<ImportReport verb="Imported" result={{ ...NEW_SHAPE, warnings: ["<b>bold</b> text"] }} />);
    expect(screen.getByText(/Imported with 1 change\./).textContent).toContain("5 widgets");
    fireEvent.click(screen.getByText("What changed (1)"));
    const item = screen.getByLabelText("Changes made while importing").querySelector("li");
    expect(item.textContent).toBe("<b>bold</b> text");
    expect(item.querySelector("b")).toBeNull();
  });

  it("an old-shape answer shows no echoed secrets", () => {
    const { container } = render(<ImportReport verb="Restored" result={OLD_SHAPE} />);
    expect(container.textContent).not.toContain("SECRET-KEY");
    expect(container.textContent).toContain("Restored with 1 change.");
  });
});

describe("ImportExport", () => {
  it("says how many changes the import made and lists them", async () => {
    api.importConfig.mockResolvedValue(NEW_SHAPE);
    const onImported = vi.fn(() => Promise.resolve());
    const { container } = render(<ImportExport onImported={onImported} onClose={vi.fn()} />);
    const file = new File(['{"services":[]}'], "x.json", { type: "application/json" });
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [file] } });
    await screen.findByText(/Imported with 2 changes\./);
    expect(screen.getByText("What changed (2)")).toBeTruthy();
    expect(onImported).toHaveBeenCalled();
  });
});

describe("SettingsModal restore", () => {
  it("shows the restore count and warnings instead of a bare 'Restored.'", async () => {
    vi.stubGlobal("confirm", () => true);
    api.listBackups.mockResolvedValue([{ name: "b1.json", size: 2048, modified: 1 }]);
    api.restoreBackup.mockResolvedValue(NEW_SHAPE);
    const onRestored = vi.fn(() => Promise.resolve());
    render(<SettingsModal settings={{ title: "T" }} auth={{ has_password: false }} onAuthChanged={vi.fn()} onSave={vi.fn()} onRestored={onRestored} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Backups" }));
    fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    await screen.findByText(/Restored with 2 changes\./);
    expect(screen.getByText("What changed (2)")).toBeTruthy();
    await waitFor(() => expect(onRestored).toHaveBeenCalled());
  });
});
