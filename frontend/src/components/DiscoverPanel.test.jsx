import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => ({
  discoverDocker: vi.fn(),
  importDiscovered: vi.fn(),
  listIntegrations: vi.fn(),
  listServices: vi.fn(),
  checkHealth: vi.fn(),
}));
vi.mock("../api.js", () => ({ api }));
vi.mock("../widgets/Icon.jsx", () => ({ default: ({ icon }) => <span data-testid="icon">{icon}</span> }));

import DiscoverPanel from "./DiscoverPanel.jsx";
import ServiceManager from "./ServiceManager.jsx";

const cand = (over = {}) => ({
  key: "i1:aaa", source: { integration_id: "i1", integration_name: "Docker", container_id: "aaa", container_name: "jf", image: "jellyfin/jellyfin", state: "running" },
  name: "Jellyfin", protocol: "http", host: "nas.lan", port: 8096, path: "", url: "http://nas.lan:8096", icon: "jellyfin", category: "Media",
  description: "", health: { enabled: true, type: "http" }, confidence: "label", reasons: ["name from the startpage.name label"],
  existing_service_id: null, changes: null, ...over,
});
const DATA = {
  candidates: [
    cand(),
    cand({ key: "i1:bbb", name: "Guessy", confidence: "guess", reasons: ["first published port (a guess)"], port: 6666, url: "http://nas.lan:6666", source: { ...cand().source, container_name: "odd" } }),
    cand({ key: "i1:ccc", name: "Sonarr", port: 8989, confidence: "port", existing_service_id: "s1", changes: { name: ["sonarr", "Sonarr"], port: [1, 8989] }, source: { ...cand().source, container_name: "sonarr" } }),
    cand({ key: "i1:ddd", name: "Uptodate", existing_service_id: "s2", changes: null, source: { ...cand().source, container_name: "utd" } }),
  ],
  skipped: [{ container_name: "db", reason: "not a web UI (postgres)" }],
  errors: [],
};
const INTEGRATIONS = [{ id: "i1", name: "Docker", type: "docker" }];
const mount = (props = {}) => {
  const onImported = vi.fn(() => Promise.resolve());
  const onBack = vi.fn();
  render(<DiscoverPanel integrations={INTEGRATIONS} onBack={onBack} onImported={onImported} {...props} />);
  return { onImported, onBack };
};

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.discoverDocker.mockResolvedValue(DATA);
});
afterEach(cleanup);

describe("DiscoverPanel", () => {
  it("groups candidates and sets the default checks", async () => {
    mount();
    expect(screen.getByRole("status").textContent).toMatch(/Looking at your containers/);
    await screen.findByText("New (2)");
    expect(screen.getByText("Already in your services (changes available) (2)")).toBeTruthy();
    expect(screen.getByLabelText("Import Jellyfin").checked).toBe(true); // label, new
    expect(screen.getByLabelText("Import Guessy").checked).toBe(false); // guess
    expect(screen.getByLabelText("Import Sonarr").checked).toBe(false); // update
    expect(screen.getByLabelText("Import Uptodate").disabled).toBe(true); // nothing to change
    expect(screen.getByRole("button", { name: "Import 1 selected" })).toBeTruthy();
    expect(screen.getByText("Up to date")).toBeTruthy();
    expect(screen.getByText(/sonarr → Sonarr/)).toBeTruthy();
    expect(screen.getByLabelText(/Confidence guess/).getAttribute("title")).toMatch(/a guess/);
    fireEvent.click(screen.getByText("Skipped containers (1)"));
    expect(screen.getByText("not a web UI (postgres)")).toBeTruthy();
  });

  it("selects all and none per group", async () => {
    mount();
    await screen.findByText("New (2)");
    fireEvent.click(screen.getByLabelText("Select all: New"));
    expect(screen.getByLabelText("Import Guessy").checked).toBe(true);
    expect(screen.getByRole("button", { name: "Import 2 selected" })).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Select none: New"));
    expect(screen.getByRole("button", { name: /^Import 0 selected/ }).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Select all: Already in your services (changes available)"));
    expect(screen.getByLabelText("Import Sonarr").checked).toBe(true);
    expect(screen.getByLabelText("Import Uptodate").checked).toBe(false);
  });

  it("imports the edited fields; an update sends only the changed fields", async () => {
    api.importDiscovered.mockResolvedValue({ created: [{ key: "i1:aaa", service: { id: "n1" } }], updated: [{ key: "i1:ccc", service: { id: "s1" } }], errors: [] });
    const { onImported } = mount();
    await screen.findByText("New (2)");
    fireEvent.change(screen.getByLabelText("Name of Jellyfin"), { target: { value: "Movies" } });
    fireEvent.change(screen.getByLabelText("Category of Jellyfin"), { target: { value: "Cinema" } });
    fireEvent.change(screen.getByLabelText("Port of Jellyfin"), { target: { value: "9000" } });
    fireEvent.change(screen.getByLabelText("Protocol of Jellyfin"), { target: { value: "https" } });
    fireEvent.change(screen.getByLabelText("Host of Jellyfin"), { target: { value: "media.lan" } });
    fireEvent.change(screen.getByLabelText("Path of Jellyfin"), { target: { value: "/web" } });
    expect(screen.getAllByText("https://media.lan:9000/web").length).toBe(1);
    fireEvent.click(screen.getByLabelText("Import Sonarr"));
    fireEvent.click(screen.getByRole("button", { name: "Import 2 selected" }));
    await screen.findByText(/Created 1, updated 1/);
    expect(api.importDiscovered).toHaveBeenCalledWith([
      { key: "i1:aaa", action: "create", fields: { name: "Movies", protocol: "https", host: "media.lan", port: 9000, path: "/web", category: "Cinema", icon: "jellyfin", description: "", health_enabled: true, health_type: "http" } },
      { key: "i1:ccc", action: "update", service_id: "s1", fields: { name: "Sonarr", port: 8989 } },
    ]);
    expect(onImported).toHaveBeenCalled();
  });

  it("shows per-item errors from the import", async () => {
    api.importDiscovered.mockResolvedValue({ created: [], updated: [], errors: [{ key: "i1:aaa", error: "host: host must be a plain hostname or IPv4 address" }] });
    mount();
    await screen.findByText("New (2)");
    fireEvent.click(screen.getByRole("button", { name: "Import 1 selected" }));
    await screen.findByText(/1 failed/);
    expect(screen.getByText(/Jellyfin: host: host must be/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Back to services" })).toBeTruthy();
  });

  it("an empty port is sent as null", async () => {
    api.importDiscovered.mockResolvedValue({ created: [], updated: [], errors: [] });
    mount();
    await screen.findByText("New (2)");
    fireEvent.change(screen.getByLabelText("Port of Jellyfin"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Import 1 selected" }));
    await waitFor(() => expect(api.importDiscovered).toHaveBeenCalled());
    expect(api.importDiscovered.mock.calls[0][0][0].fields.port).toBeNull();
  });

  it("an update row sends the fields the user edited, not only the ones discovery reported", async () => {
    api.importDiscovered.mockResolvedValue({ created: [], updated: [{ key: "i1:ccc", service: { id: "s1" } }], errors: [] });
    mount();
    await screen.findByText("New (2)");
    fireEvent.click(screen.getByLabelText("Import Sonarr"));
    fireEvent.change(screen.getByLabelText("Category of Sonarr"), { target: { value: "TV" } });
    fireEvent.change(screen.getByLabelText("Name of Sonarr"), { target: { value: "Sonarr 4K" } });
    fireEvent.click(screen.getByLabelText("Import Jellyfin")); // only the update
    fireEvent.click(screen.getByRole("button", { name: "Import 1 selected" }));
    await waitFor(() => expect(api.importDiscovered).toHaveBeenCalled());
    expect(api.importDiscovered.mock.calls[0][0]).toEqual([
      { key: "i1:ccc", action: "update", service_id: "s1", fields: { name: "Sonarr 4K", port: 8989, category: "TV" } },
    ]);
  });

  it("a non-numeric port shows an inline error and blocks the import instead of sending null", async () => {
    mount();
    await screen.findByText("New (2)");
    fireEvent.change(screen.getByLabelText("Port of Jellyfin"), { target: { value: "80a" } });
    expect(screen.getByText("Port must be a number from 1 to 65535")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import 1 selected" }).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Import 1 selected" }));
    expect(api.importDiscovered).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Port of Jellyfin"), { target: { value: "70000" } });
    expect(screen.getByText("Port must be a number from 1 to 65535")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Port of Jellyfin"), { target: { value: "8096" } });
    expect(screen.queryByText("Port must be a number from 1 to 65535")).toBeNull();
    expect(screen.getByRole("button", { name: "Import 1 selected" }).disabled).toBe(false);
  });

  it("an earlier, slower discovery request cannot overwrite the result of the integration just picked", async () => {
    let releaseFirst;
    api.discoverDocker.mockReset();
    api.discoverDocker
      .mockImplementationOnce(() => new Promise((res) => { releaseFirst = () => res(DATA); }))
      .mockResolvedValueOnce({ candidates: [cand({ key: "i2:zzz", name: "OnlyOnNas" })], skipped: [], errors: [] });
    mount({ integrations: [...INTEGRATIONS, { id: "i2", name: "NAS", type: "docker" }] });
    fireEvent.change(screen.getByLabelText(/Docker integration/), { target: { value: "i2" } });
    await screen.findByLabelText("Import OnlyOnNas");
    releaseFirst(); // the "All" request finally answers
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByLabelText("Import OnlyOnNas")).toBeTruthy();
    expect(screen.queryByLabelText("Import Sonarr")).toBeNull();
  });

  it("shows the empty state and integration errors", async () => {
    api.discoverDocker.mockResolvedValue({ candidates: [], skipped: [], errors: [{ integration_id: "i1", error: "connection refused (dock:2375)" }] });
    mount();
    await screen.findByText("No new services found.");
    expect(screen.getByRole("alert").textContent).toMatch(/Docker: connection refused/);
    expect(screen.getByRole("button", { name: /^Import 0 selected/ }).disabled).toBe(true);
  });

  it("shows a failure and retries", async () => {
    api.discoverDocker.mockRejectedValueOnce(new Error("Authentication required"));
    mount();
    await screen.findByText(/Discovery failed: Authentication required/);
    fireEvent.click(screen.getByText("Retry"));
    await screen.findByText("New (2)");
    expect(api.discoverDocker).toHaveBeenCalledTimes(2);
  });

  it("lets you pick one of several docker integrations", async () => {
    mount({ integrations: [...INTEGRATIONS, { id: "i2", name: "NAS", type: "docker" }] });
    await screen.findByText("New (2)");
    fireEvent.change(screen.getByLabelText(/Docker integration/), { target: { value: "i2" } });
    await waitFor(() => expect(api.discoverDocker).toHaveBeenLastCalledWith({ integration_id: "i2" }));
  });

  it("goes back with the Back button", async () => {
    const { onBack } = mount();
    await screen.findByText("New (2)");
    fireEvent.click(screen.getByText("Back"));
    expect(onBack).toHaveBeenCalled();
  });
});

describe("ServiceManager discovery entry", () => {
  const props = { services: [], health: {}, onChanged: vi.fn(() => Promise.resolve()), onClose: () => {} };

  it("offers Discover only when a docker integration exists", async () => {
    api.listIntegrations.mockResolvedValue([{ id: "i1", name: "Docker", type: "docker" }, { id: "h", name: "H", type: "host" }]);
    render(<ServiceManager {...props} />);
    const button = await screen.findByRole("button", { name: "Discover" });
    expect(screen.queryByText(/Discovery needs a Docker integration/)).toBeNull();
    fireEvent.click(button);
    await screen.findByText("New (2)");
    expect(within(screen.getByRole("heading", { level: 2 })).getByText("Discover services")).toBeTruthy();
  });

  it("shows a one-line hint without one", async () => {
    api.listIntegrations.mockResolvedValue([{ id: "h", name: "H", type: "host" }]);
    render(<ServiceManager {...props} />);
    await screen.findByText(/Discovery needs a Docker integration/);
    expect(screen.queryByRole("button", { name: "Discover" })).toBeNull();
  });
});
