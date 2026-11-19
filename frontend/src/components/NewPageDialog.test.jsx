import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => ({
  listTemplates: vi.fn(),
  getTemplate: vi.fn(),
  applyTemplate: vi.fn(),
  importPage: vi.fn(),
  previewImport: vi.fn(),
  exportPage: vi.fn(),
}));
vi.mock("../api.js", () => ({ api }));

import NewPageDialog from "./NewPageDialog.jsx";
import ExportPageDialog from "./ExportPageDialog.jsx";

const TEMPLATES = [
  { id: "media", title: "Media", description: "Now playing and queues", tags: ["media"], requires: ["jellyfin", "sonarr"], name: "Media", columns: 24, rows: 10, widget_count: 2,
    layout: [{ type: "jellyfin", x: 0, y: 0, w: 12, h: 5 }, { type: "sonarr", x: 12, y: 0, w: 12, h: 5 }] },
  { id: "home", title: "Home", description: "Greeting and search", tags: ["general"], requires: [], name: "Home", columns: 24, rows: 4, widget_count: 1, layout: [{ type: "clock", x: 0, y: 0, w: 4, h: 4 }] },
];
const PREVIEW = {
  dashboard: { id: null, name: "Media", widgets: [] }, widget_count: 2, custom_widgets: 0, created_services: [], matched_services: [], skipped_services: [],
  matched_integrations: [{ ref: "jellyfin", id: "i1", type: "jellyfin", name: "Jellyfin", via: "auto" }],
  unresolved_integrations: [{ ref: "sonarr", type: "sonarr", name: "Sonarr", widget_ids: [], widget_count: 1 }],
  warnings: [],
};
const INTEGRATIONS = [{ id: "i1", name: "My Jelly", type: "jellyfin" }, { id: "i2", name: "TV", type: "sonarr" }, { id: "i3", name: "Sonarr 4K", type: "sonarr" }];

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.listTemplates.mockResolvedValue(TEMPLATES);
  api.getTemplate.mockResolvedValue({ format: "startpage-page", version: 1, id: "media", name: "Media", widgets: [] });
  api.previewImport.mockResolvedValue(PREVIEW);
  api.applyTemplate.mockResolvedValue({ dashboard: { id: "d9", name: "Media", widgets: [] }, unresolved_integrations: [] });
});
afterEach(cleanup);

const mount = (props = {}) => {
  const p = { integrations: INTEGRATIONS, onBlank: vi.fn(), onCreated: vi.fn(() => Promise.resolve()), onClose: vi.fn(), ...props };
  render(<NewPageDialog {...p} />);
  return p;
};

describe("New page dialog", () => {
  it("creates a blank page from the first tab", () => {
    const p = mount();
    fireEvent.change(screen.getByLabelText("Page name"), { target: { value: "Lab" } });
    fireEvent.click(screen.getByRole("button", { name: "Create blank page" }));
    expect(p.onBlank).toHaveBeenCalledWith("Lab");
  });

  it("template -> preview -> integration picker -> create", async () => {
    const p = mount();
    fireEvent.click(screen.getByRole("tab", { name: "From template" }));
    const card = await screen.findByRole("button", { name: /Media: Now playing/ });
    expect(within(card).getByRole("img", { name: "Layout of 2 widgets" })).toBeTruthy(); // the mini-map
    expect(within(card).getByText("needs jellyfin, sonarr")).toBeTruthy();
    fireEvent.click(card);

    await waitFor(() => expect(api.getTemplate).toHaveBeenCalledWith("media"));
    const jelly = await screen.findByLabelText(/Jellyfin/);
    expect(jelly.value).toBe("i1"); // auto-matched by the server
    const sonarr = screen.getByLabelText(/Sonarr/);
    expect(sonarr.value).toBe(""); // unresolved: "Set up later"
    expect([...sonarr.options].map((o) => o.text)).toEqual(["Set up later", "TV", "Sonarr 4K"]); // only this type
    fireEvent.change(sonarr, { target: { value: "i3" } });
    await waitFor(() => expect(api.previewImport).toHaveBeenLastCalledWith(expect.objectContaining({ integration_map: { sonarr: "i3" }, service_mode: "match" })));

    fireEvent.change(screen.getByLabelText("Page name"), { target: { value: "Movie night" } });
    fireEvent.click(screen.getByRole("button", { name: "Create page" }));
    await waitFor(() => expect(p.onCreated).toHaveBeenCalled());
    expect(api.applyTemplate).toHaveBeenCalledWith("media", { name: "Movie night", service_mode: "match", integration_map: { sonarr: "i3" } });
    expect(p.onCreated.mock.calls[0][0].dashboard.id).toBe("d9");
  });

  it("'Set up later' forces an integration to stay unset", async () => {
    mount();
    fireEvent.click(screen.getByRole("tab", { name: "From template" }));
    fireEvent.click(await screen.findByRole("button", { name: /Media: Now playing/ }));
    fireEvent.change(await screen.findByLabelText(/Jellyfin/), { target: { value: "" } });
    await waitFor(() => expect(api.previewImport).toHaveBeenLastCalledWith(expect.objectContaining({ integration_map: { jellyfin: null } })));
  });

  it("shows service counts and lets the user change the service mode", async () => {
    api.previewImport.mockResolvedValue({ ...PREVIEW, created_services: [{ ref: "a", name: "A" }], matched_services: [{ ref: "b", name: "B", id: "s1" }], warnings: ["Contains custom code; it runs sandboxed."] });
    mount();
    fireEvent.click(screen.getByRole("tab", { name: "From template" }));
    fireEvent.click(await screen.findByRole("button", { name: /Home: Greeting/ }));
    expect(await screen.findByText(/1 to create, 1 matched to existing/)).toBeTruthy();
    expect(screen.getByLabelText("Warnings").textContent).toContain("custom code");
    fireEvent.click(screen.getByLabelText(/Always create/));
    await waitFor(() => expect(api.previewImport).toHaveBeenLastCalledWith(expect.objectContaining({ service_mode: "create" })));
  });

  it("imports a pasted file through the same preview and import path", async () => {
    api.importPage.mockResolvedValue({ dashboard: { id: "d2", name: "Shared", widgets: [] }, unresolved_integrations: [] });
    const p = mount();
    fireEvent.click(screen.getByRole("tab", { name: "Import from file" }));
    fireEvent.change(screen.getByLabelText("Or paste the JSON"), { target: { value: "{nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect((await screen.findByRole("alert")).textContent).toContain("not valid JSON");
    const page = { format: "startpage-page", version: 1, name: "Shared", widgets: [] };
    fireEvent.change(screen.getByLabelText("Or paste the JSON"), { target: { value: JSON.stringify(page) } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(api.previewImport).toHaveBeenCalledWith(expect.objectContaining({ page })));
    fireEvent.click(await screen.findByRole("button", { name: "Create page" }));
    await waitFor(() => expect(api.importPage).toHaveBeenCalledWith(expect.objectContaining({ page, service_mode: "match" })));
    await waitFor(() => expect(p.onCreated).toHaveBeenCalled());
  });

  it("refuses a file that is not a page file", async () => {
    mount();
    fireEvent.click(screen.getByRole("tab", { name: "Import from file" }));
    fireEvent.change(screen.getByLabelText("Or paste the JSON"), { target: { value: '{"services":[]}' } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect((await screen.findByRole("alert")).textContent).toContain("not a Start Page page file");
    expect(api.previewImport).not.toHaveBeenCalled();
  });

  it("shows a server error from the preview and keeps Create disabled", async () => {
    api.previewImport.mockRejectedValue(new Error("widgets.0: unknown widget type 'evil'"));
    mount();
    fireEvent.click(screen.getByRole("tab", { name: "From template" }));
    fireEvent.click(await screen.findByRole("button", { name: /Home: Greeting/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("unknown widget type");
    expect(screen.getByRole("button", { name: "Create page" }).disabled).toBe(true);
  });

  it("the tabs are keyboard reachable buttons", () => {
    mount();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.tagName)).toEqual(["BUTTON", "BUTTON", "BUTTON"]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
  });
});

describe("Export page dialog", () => {
  beforeEach(() => {
    globalThis.URL.createObjectURL = vi.fn(() => "blob:x");
    globalThis.URL.revokeObjectURL = vi.fn();
  });

  it("shows warnings first and downloads only on request, without the warnings in the file", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    api.exportPage.mockResolvedValue({ format: "startpage-page", version: 1, widgets: [], warnings: ["Widget 1 (iframe), config.url: has credentials in the URL"] });
    const onClose = vi.fn();
    render(<ExportPageDialog dashboard={{ id: "d1", name: "My page" }} onClose={onClose} />);
    expect((await screen.findByLabelText("Warnings")).textContent).toContain("credentials");
    expect(click).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Download anyway" }));
    expect(click).toHaveBeenCalledTimes(1);
    const blob = URL.createObjectURL.mock.calls[0][0];
    expect(JSON.parse(await blob.text())).not.toHaveProperty("warnings");
    expect(onClose).toHaveBeenCalled();
    click.mockRestore();
  });

  it("never downloads on its own: with nothing to warn about it still shows what is in the file first", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    api.exportPage.mockResolvedValue({
      format: "startpage-page", version: 1, widgets: [], warnings: [],
      contents: { widget_count: 12, widget_types: { link: 3 }, service_count: 4, integration_count: 1, includes: { links: true, custom_code: true, text_content: false } },
    });
    const onClose = vi.fn();
    render(<ExportPageDialog dashboard={{ id: "d1", name: "My page" }} onClose={onClose} />);
    const line = await screen.findByTestId("export-contents");
    expect(line.textContent).toContain("12 widgets, 4 services, 1 integration");
    expect(line.textContent).toContain("Includes: links, custom code.");
    expect(click).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Warnings")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(click).toHaveBeenCalledTimes(1);
    const file = JSON.parse(await URL.createObjectURL.mock.calls[0][0].text());
    expect(file).not.toHaveProperty("contents"); // the summary is for the exporter, not part of the file
    expect(onClose).toHaveBeenCalled();
    click.mockRestore();
  });

  it("counts from the page JSON itself when the server sends no contents summary", async () => {
    api.exportPage.mockResolvedValue({
      format: "startpage-page", version: 1, warnings: [],
      widgets: [{ type: "custom", config: { js: "x" } }, { type: "link", config: { url: "https://a.example" } }, { type: "text", config: { content: "hi" }, layout_sm: { x: 0, y: 0, w: 2, h: 1 } }],
      services: [{ ref: "s1", name: "A" }], integrations: [],
    });
    render(<ExportPageDialog dashboard={{ id: "d1", name: "My page" }} onClose={vi.fn()} />);
    const line = (await screen.findByTestId("export-contents")).textContent;
    expect(line).toContain("3 widgets, 1 service, 0 integrations");
    expect(line).toContain("custom code");
    expect(line).toContain("links");
    expect(line).toContain("a phone layout");
  });
});
