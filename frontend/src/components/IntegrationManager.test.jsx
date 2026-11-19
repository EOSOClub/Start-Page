import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  integrationTypes: vi.fn(),
  createIntegration: vi.fn(),
  updateIntegration: vi.fn(),
  testIntegration: vi.fn(),
  deleteIntegration: vi.fn(),
  refreshIntegration: vi.fn(),
}));
vi.mock("../api.js", () => ({ api }));

import IntegrationManager, { summarize } from "./IntegrationManager.jsx";
import ConfigForm from "./ConfigForm.jsx";
import { resetIntegrationTypes } from "../integrationTypes.js";

const MASK = "********";
const TYPES = [
  {
    type: "svc",
    label: "Test Service",
    description: "Reads numbers from a made-up service.",
    default_interval: 45,
    provides: ["stats"],
    fields: [
      { key: "url", label: "Service URL", type: "url", required: true, placeholder: "http://svc" },
      { key: "token", label: "API token", type: "secret", secret: true, mask: MASK },
      { key: "headers", label: "Headers", type: "headers", secret: true, mask: MASK },
      { key: "mode", label: "Mode", type: "select", default: "a", options: [{ value: "a", label: "Mode A" }, { value: "b", label: "Mode B" }] },
      { key: "verify_tls", label: "Verify TLS certificate", type: "checkbox", default: false },
    ],
    actions: [],
  },
  { type: "plain", label: "Plain", description: "Needs nothing.", default_interval: 30, provides: [], fields: [], actions: [] },
];
const SAVED = { id: "i1", name: "Mine", type: "svc", interval: 45, enabled: true, config: { url: "http://svc", token: MASK, headers: MASK, mode: "a" } };

const mount = (integrations = [SAVED]) => {
  const onChanged = vi.fn(() => Promise.resolve());
  render(<IntegrationManager integrations={integrations} integrationData={{}} onChanged={onChanged} onClose={() => {}} />);
  return onChanged;
};
const edit = async () => {
  await waitFor(() => expect(screen.getByText("Edit").disabled).toBe(false)); // enabled once the types are loaded
  fireEvent.click(screen.getByText("Edit"));
};

beforeEach(() => {
  resetIntegrationTypes();
  for (const fn of Object.values(api)) fn.mockReset();
  api.integrationTypes.mockResolvedValue(TYPES);
  api.createIntegration.mockResolvedValue({});
  api.updateIntegration.mockResolvedValue({});
});
afterEach(cleanup);

describe("IntegrationManager", () => {
  it("builds the type picker and the form from the backend specs", async () => {
    mount([]);
    fireEvent.click(screen.getByText("+ New integration"));
    const radios = await screen.findAllByRole("radio");
    expect(radios.map((r) => r.textContent)).toEqual(["PlainNeeds nothing.", "Test ServiceReads numbers from a made-up service."]);
    expect(screen.queryByLabelText("Service URL")).toBeNull(); // no type chosen yet
    fireEvent.click(screen.getByRole("radio", { name: /Test Service/ }));
    expect(screen.getByLabelText("Service URL")).toBeTruthy();
    expect(screen.getByLabelText("Mode").value).toBe("a");
    expect(screen.getByLabelText("Poll interval (s)").value).toBe("45"); // the type's default_interval
    fireEvent.click(screen.getByRole("radio", { name: /Plain/ }));
    expect(screen.queryByLabelText("Service URL")).toBeNull();
  });

  it("fetches the specs once and labels existing rows from them", async () => {
    const first = render(<IntegrationManager integrations={[SAVED]} integrationData={{}} onChanged={() => {}} onClose={() => {}} />);
    await screen.findByText("Test Service");
    first.unmount();
    mount();
    await screen.findByText("Test Service");
    expect(api.integrationTypes).toHaveBeenCalledTimes(1);
  });

  it("creates with the typed config", async () => {
    const onChanged = mount([]);
    fireEvent.click(screen.getByText("+ New integration"));
    fireEvent.click(await screen.findByRole("radio", { name: /Test Service/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Svc" } });
    fireEvent.change(screen.getByLabelText("Service URL"), { target: { value: "http://x:1" } });
    fireEvent.change(screen.getByLabelText("API token"), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(api.createIntegration).toHaveBeenCalled());
    expect(api.createIntegration.mock.calls[0][0]).toEqual({
      name: "Svc", type: "svc", interval: 45, enabled: true, config: { url: "http://x:1", token: "s3cret" },
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("keeps a saved secret unless it is replaced or removed", async () => {
    mount();
    await edit();
    const token = screen.getByLabelText("API token");
    expect(token.value).toBe(""); // the mask is never shown
    expect(token.placeholder).toBe("Saved. Type a new value to replace it.");
    fireEvent.click(screen.getByText("Save")); // untouched: the mask goes back, which means "keep"
    await waitFor(() => expect(api.updateIntegration).toHaveBeenCalledTimes(1));
    expect(api.updateIntegration.mock.calls[0][1].config).toMatchObject({ token: MASK, headers: MASK });

    await edit(); // saving closes the form: open it again
    fireEvent.change(screen.getByLabelText("API token"), { target: { value: "new-token" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(api.updateIntegration).toHaveBeenCalledTimes(2));
    expect(api.updateIntegration.mock.calls[1][1].config.token).toBe("new-token");
  });

  it("removes a saved secret with the Remove button", async () => {
    mount();
    await edit();
    fireEvent.click(screen.getAllByText("Remove")[0]);
    expect(screen.getByLabelText("API token").placeholder).toBe("Will be removed when you save");
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(api.updateIntegration).toHaveBeenCalled());
    expect(api.updateIntegration.mock.calls[0][1].config.token).toBe("");
  });

  it("edits headers as Name: value lines and sends a dict", async () => {
    mount();
    await edit();
    const box = screen.getByLabelText("Headers");
    expect(box.tagName).toBe("TEXTAREA");
    fireEvent.change(box, { target: { value: "Authorization: Bearer abc\nX-Other: 1: 2" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(api.updateIntegration).toHaveBeenCalled());
    expect(api.updateIntegration.mock.calls[0][1].config.headers).toEqual({ Authorization: "Bearer abc", "X-Other": "1: 2" });
    expect(api.updateIntegration.mock.calls[0][1].config).not.toHaveProperty("headers_text");
  });

  it("tests the unsaved config, resolving secrets by id, and shows the result", async () => {
    api.testIntegration.mockResolvedValueOnce({ ok: true, error: null, sample: { stats: [{ key: "q", label: "Queries", value: 12, unit: "k" }] } });
    mount();
    await edit();
    fireEvent.change(screen.getByLabelText("Service URL"), { target: { value: "http://changed" } });
    fireEvent.click(screen.getByText("Test"));
    await screen.findByText("Works.");
    expect(screen.getByRole("status").textContent).toContain("Queries: 12 k");
    expect(api.testIntegration).toHaveBeenCalledWith({ type: "svc", id: "i1", config: expect.objectContaining({ url: "http://changed", token: MASK }) });
    expect(api.updateIntegration).not.toHaveBeenCalled();

    api.testIntegration.mockResolvedValueOnce({ ok: false, error: "HTTP 401 Unauthorized (svc:80)", sample: null });
    fireEvent.click(screen.getByText("Test"));
    await screen.findByText(/Failed: HTTP 401 Unauthorized/);

    api.testIntegration.mockRejectedValueOnce(new Error("config.url: is required"));
    fireEvent.click(screen.getByText("Test"));
    await screen.findByText(/Failed: config.url: is required/);
  });

  it("shows the server's per-field message when saving fails", async () => {
    api.updateIntegration.mockRejectedValueOnce(new Error("config.url: must start with http:// or https://"));
    mount();
    await edit();
    fireEvent.click(screen.getByText("Save"));
    await screen.findByText("config.url: must start with http:// or https://");
  });

  it("needs a name", async () => {
    mount([]);
    fireEvent.click(screen.getByText("+ New integration"));
    fireEvent.click(await screen.findByRole("radio", { name: /Plain/ }));
    fireEvent.click(screen.getByText("Save"));
    expect(screen.getByText("Name is required.")).toBeTruthy();
    expect(api.createIntegration).not.toHaveBeenCalled();
  });
});

describe("summarize", () => {
  it("prefers stats, then items, then cut JSON", () => {
    expect(summarize({ stats: [{ label: "A", value: 1 }, { label: "B", value: null, unit: "%" }] })).toBe("A: 1 · B: — %");
    expect(summarize({ items: [{ title: "x" }, { title: "y" }] })).toBe("x · y");
    expect(summarize({ big: "z".repeat(400) }).length).toBe(241);
    expect(summarize(null)).toBe("");
  });
});

describe("ConfigForm integration field", () => {
  const integrations = [
    { id: "a", name: "Dock", type: "docker" },
    { id: "b", name: "Feed", type: "rss" },
    { id: "c", name: "Hole", type: "pihole" },
  ];
  const specs = [
    { type: "docker", provides: ["stats"] },
    { type: "rss", provides: ["items"] },
    { type: "pihole", provides: ["stats"] },
  ];
  const options = () => [...screen.getByRole("combobox").options].map((o) => o.textContent);

  it("filters by one type, a list of types, or a capability", async () => {
    api.integrationTypes.mockResolvedValue(specs);
    const field = (extra) => [{ key: "integration_id", label: "Pick", type: "integration", ...extra }];
    const view = (extra) => render(<ConfigForm fields={field(extra)} value={{}} onChange={() => {}} integrations={integrations} />);

    let r = view({ integrationType: "rss" });
    expect(options()).toEqual(["— select —", "Feed (rss)"]);
    r.unmount();
    r = view({ integrationType: ["docker", "rss"] });
    expect(options()).toEqual(["— select —", "Dock (docker)", "Feed (rss)"]);
    r.unmount();
    expect(api.integrationTypes).not.toHaveBeenCalled(); // plain type filters never need the specs

    r = view({ capability: "stats" });
    await waitFor(() => expect(options()).toEqual(["— select —", "Dock (docker)", "Hole (pihole)"]));
    r.unmount();
    view({ capability: "items" });
    expect(options()).toEqual(["— select —", "Feed (rss)"]);
  });

  it("explains an empty list", () => {
    render(<ConfigForm fields={[{ key: "i", label: "Pick", type: "integration", integrationType: "pihole" }]} value={{}} onChange={() => {}} integrations={[]} />);
    expect(screen.getByText(/No pihole integration yet/)).toBeTruthy();
  });
});

describe("deleting an integration", () => {
  it("shows the failure instead of an unhandled rejection", async () => {
    vi.stubGlobal("confirm", () => true);
    api.deleteIntegration.mockRejectedValue(new Error("integration is in use"));
    const onChanged = mount();
    fireEvent.click(screen.getAllByText("✕").find((b) => b.className.includes("btn-danger")));
    await screen.findByText(/Could not delete "Mine": integration is in use/);
    expect(onChanged).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
