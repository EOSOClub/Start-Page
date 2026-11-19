import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => ({ integrationAction: vi.fn() }));
vi.mock("../api.js", () => ({ api }));

import { PRESETS } from "./integrationPresets.jsx";
import { WIDGET_TYPES, defaultConfig } from "./index.jsx";
import { AppContext } from "../context.js";
import { isOn, isUnavailable, pickEntities, portainerActions, proxmoxActions, sectionsOf, toggledState } from "./actionHelpers.js";

afterEach(cleanup);
beforeEach(() => {
  api.integrationAction.mockReset(); // not returned: a returned function would run as teardown
});

const st = (key, label, value, extra = {}) => ({ key, label, value, ...extra });
const item = (title, extra = {}) => ({ title, ...extra });

/** Fixtures shaped like what each backend type returns (stat keys and item fields from the type modules). */
const FIXTURES = {
  sonarr: {
    stats: [st("queue", "Queue", 2), st("missing", "Missing", 5, { tone: "warn" }), st("upcoming", "Next 7 days", 3), st("series", "Series", 40)],
    items: [item("Severance S02E03", { subtitle: "Who Is Alive?", status: "unknown", meta: "Tomorrow 21:00" })],
    empty: "No episodes in this list.",
  },
  radarr: {
    stats: [st("queue", "Queue", 1), st("missing", "Missing", 9), st("movies", "Movies", 300), st("upcoming", "Next 7 days", 2)],
    items: [item("Dune: Part Three", { subtitle: "2026", status: "unknown", meta: "Dec 18" })],
    empty: "No movies in this list.",
  },
  qbittorrent: {
    stats: [st("download", "Download", 4.2, { unit: "MB/s" }), st("upload", "Upload", 0.5, { unit: "MB/s" }), st("downloading", "Downloading", 2), st("seeding", "Seeding", 7)],
    items: [item("ubuntu-26.04.iso", { status: "online", progress: 42.5, meta: "4.2 MB/s" })],
    empty: "No active torrents.",
  },
  sabnzbd: {
    stats: [st("speed", "Speed", 12.5, { unit: "MB/s" }), st("left", "Left", 3.1, { unit: "GB" }), st("timeleft", "Time left", "0:04:10"), st("queue", "Queue", 3), st("paused", "Paused", false)],
    items: [item("Some.Release.1080p", { status: "online", progress: 10, meta: "0:04:10" })],
    empty: "The queue is empty.",
  },
  jellyfin: {
    stats: [st("streams", "Streams", 1), st("transcodes", "Transcodes", 0), st("movies", "Movies", 120)],
    items: [item("Alice: Arrival", { subtitle: "Alice on Living room TV", status: "online", progress: 63, meta: "Direct play" })],
    empty: "Nothing playing.",
  },
  plex: {
    stats: [st("streams", "Streams", 2), st("transcodes", "Transcodes", 1)],
    items: [item("The Bear S03E01", { subtitle: "Bob on Plex Web", status: "online", progress: 20, meta: "Transcode" })],
    empty: "Nothing playing.",
  },
  proxmox: {
    stats: [st("nodes", "Nodes online", "1/1", { tone: "ok" }), st("vms", "VMs running", "1/2"), st("lxcs", "LXCs running", "0/1"), st("cpu", "CPU", 12.5, { unit: "%" }), st("memory", "Memory", 40, { unit: "%" }), st("storage", "Storage", 61, { unit: "%" })],
    items: [item("web", { subtitle: "pve1 · VM 100", status: "online", progress: 12, meta: "1.0 GB / 2.0 GB", ref: { node: "pve1", vmid: "100", kind: "qemu" } }), item("db", { subtitle: "pve1 · LXC 101", status: "offline", ref: { node: "pve1", vmid: "101", kind: "lxc" } })],
    empty: "No guests.",
  },
  truenas: {
    stats: [st("pools", "Pools healthy", "2/2", { tone: "ok" }), st("used", "Used", 55, { unit: "%" }), st("alerts", "Alerts", 1, { tone: "warn" })],
    items: [item("tank", { subtitle: "RAIDZ1", status: "online", progress: 55, meta: "5.5 TB / 10 TB" })],
    empty: "No pools or alerts.",
  },
  portainer: {
    stats: [st("endpoints", "Environments up", "1/1"), st("running", "Running", 8), st("stopped", "Stopped", 2), st("unhealthy", "Unhealthy", 0, { tone: "ok" })],
    items: [item("nginx", { subtitle: "nginx:1.27", status: "online", meta: "Up 3 days", ref: { endpoint_id: 2, container_id: "c0ffee01" } }), item("old-job", { subtitle: "busybox", status: "offline", meta: "Exited (0)", ref: { endpoint_id: 2, container_id: "deadbeef" } })],
    empty: "No containers.",
  },
  unraid: {
    stats: [st("array", "Array", "Started"), st("array_used", "Array used", 61, { unit: "%" }), st("parity", "Parity", "Valid"), st("disk_errors", "Disk errors", 0), st("cpu", "CPU", 9, { unit: "%" }), st("memory", "Memory", 33, { unit: "%" })],
    items: [item("Disk 1", { subtitle: "sdb", status: "online", progress: 70, meta: "7 TB / 10 TB" })],
    empty: "Nothing to list.",
  },
  adguard: {
    blocking: { enabled: true, timer: null },
    stats: [st("queries", "Queries", 12000), st("blocked", "Blocked", 3000), st("percent_blocked", "Percent blocked", 25, { unit: "%" }), st("avg_time", "Avg time", 12.3, { unit: "ms" })],
    items: [item("ads.example.com", { meta: "120" })],
    empty: "No blocked domains yet.",
  },
  unifi: {
    stats: [st("wan_status", "WAN", "online", { tone: "ok" }), st("clients", "Clients", 31), st("devices_offline", "Devices offline", 0), st("wan_down", "WAN down", 120, { unit: "Mbit/s" }), st("wan_up", "WAN up", 22, { unit: "Mbit/s" }), st("latency", "Latency", 9, { unit: "ms" })],
    items: [item("USW-24", { subtitle: "switch", status: "online" })],
    empty: "Nothing to list.",
  },
  speedtest_tracker: {
    stats: [st("download", "Download", 480, { unit: "Mbit/s", series: [400, 450, 480] }), st("upload", "Upload", 40, { unit: "Mbit/s", series: [38, 41, 40] }), st("ping", "Ping", 9, { unit: "ms", series: [10, 9, 9] }), st("last_test", "Last test", "2h ago")],
  },
};

const TYPES = Object.keys(PRESETS);
const integ = (type, config = {}) => [{ id: "i1", name: `My ${type}`, type, config }];
const ok = (data) => ({ i1: { integration_id: "i1", ok: true, fetched_at: 1, error: null, data } });

function show(type, config = {}, integrationData, { canWrite = true, onRefresh = vi.fn(), integConfig = {} } = {}) {
  const def = WIDGET_TYPES[type];
  return render(
    <AppContext.Provider value={{ canWrite }}>
      <def.Render
        widget={{ id: "w", type, config: { integration_id: "i1", ...defaultConfig(type), ...config } }}
        integrations={integ(type, integConfig)}
        integrationData={integrationData}
        onRefresh={onRefresh}
      />
    </AppContext.Provider>
  );
}

describe("preset factory: all 13 types", () => {
  it("covers every integration type that has a preset, with the fixtures in this file", () => {
    expect(TYPES.sort()).toEqual(Object.keys(FIXTURES).sort());
  });

  it.each(TYPES)("%s: registered as a thin preset limited to its type, in the Integrations group", (type) => {
    const def = WIDGET_TYPES[type];
    expect(def.group).toBe("Integrations");
    expect(def.size.w).toBeGreaterThanOrEqual(4);
    expect(def.fields[0]).toMatchObject({ key: "integration_id", type: "integration", integrationType: type });
    expect(def.fields.map((f) => f.key)).toContain("stat_keys");
  });

  it.each(TYPES)("%s: renders a populated fixture (header, stats and the list)", (type) => {
    const fx = FIXTURES[type];
    const { container } = show(type, defaultConfig(type).show === "stats" ? { show: "both" } : {}, ok(fx));
    expect(screen.getByText(`My ${type}`)).toBeTruthy(); // header = the integration's name
    // at least the first default stat label is on screen
    const firstKey = (defaultConfig(type).stat_keys || fx.stats[0].key).split(/[,\s]+/)[0];
    expect(screen.getByText(fx.stats.find((s) => s.key === firstKey).label)).toBeTruthy();
    if (fx.items) {
      const rows = container.querySelectorAll(".irow");
      expect(rows.length).toBe(fx.items.length);
      expect(screen.getByText(fx.items[0].title)).toBeTruthy();
    }
  });

  it.each(TYPES.filter((t) => FIXTURES[t].items))("%s: shows its empty state for an empty list", (type) => {
    const fx = FIXTURES[type];
    show(type, {}, ok({ ...fx, items: [] }));
    if (type === "adguard" || type === "unifi") {
      cleanup();
      show(type, { show: "both" }, ok({ ...fx, items: [] }));
    }
    expect(screen.getByText(fx.empty)).toBeTruthy();
  });

  it.each(TYPES)("%s: shows the error when nothing was ever fetched, and asks for an integration", (type) => {
    show(type, {}, { i1: { ok: false, error: "HTTP 401 Unauthorized (h:80)", data: null } });
    expect(screen.getByText("Error: HTTP 401 Unauthorized (h:80)")).toBeTruthy();
    cleanup();
    show(type, { integration_id: null }, {});
    expect(screen.getByText(/Select a .* integration/)).toBeTruthy();
    cleanup();
    show(type, {}, {});
    expect(screen.getByText("Waiting for first fetch…")).toBeTruthy();
  });

  it.each(TYPES)("%s: keeps showing the last data, marked stale, after a failed refresh", (type) => {
    show(type, {}, { i1: { ok: false, error: "timeout", data: FIXTURES[type] } });
    expect(screen.getByText("stale")).toBeTruthy();
  });

  it("presentation defaults: Jellyfin and Plex are now-playing lists with progress and an empty state", () => {
    const { container } = show("jellyfin", {}, ok(FIXTURES.jellyfin));
    expect(container.querySelector("[role=progressbar]").getAttribute("aria-valuenow")).toBe("63");
    expect(container.querySelector(".istat-strip")).toBeTruthy(); // compact stats above the list
    cleanup();
    show("plex", {}, ok({ ...FIXTURES.plex, items: [] }));
    expect(screen.getByText("Nothing playing.")).toBeTruthy();
  });

  it("presentation defaults: Sonarr and Radarr put a stats row above the list", () => {
    for (const t of ["sonarr", "radarr"]) {
      const { container } = show(t, {}, ok(FIXTURES[t]));
      const strip = container.querySelector(".istat-strip");
      const list = container.querySelector(".irow");
      expect(strip && list && strip.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      cleanup();
    }
  });

  it("UniFi and AdGuard default to stats; the Show option switches to stats + list or the list alone", () => {
    const stats = show("unifi", {}, ok(FIXTURES.unifi));
    expect(stats.container.querySelector(".irow")).toBeNull();
    expect(stats.container.querySelector(".istats-body")).toBeTruthy();
    stats.unmount();
    const both = show("unifi", { show: "both" }, ok(FIXTURES.unifi));
    expect(both.container.querySelector(".irow")).toBeTruthy();
    expect(both.container.querySelector(".istat-strip")).toBeTruthy();
    both.unmount();
    const list = show("unifi", { show: "items" }, ok(FIXTURES.unifi));
    expect(list.container.querySelector(".istat-strip, .istats-body")).toBeNull();
    expect(screen.getByText("USW-24")).toBeTruthy();
  });

  it("Speedtest Tracker: three tiles with sparklines", () => {
    const { container } = show("speedtest_tracker", {}, ok(FIXTURES.speedtest_tracker));
    expect([...container.querySelectorAll(".istat > span")].map((e) => e.textContent)).toEqual(["Download", "Upload", "Ping"]);
    expect(container.querySelectorAll("svg.spark").length).toBe(3);
    expect(WIDGET_TYPES.speedtest_tracker.fields.some((f) => f.key === "show")).toBe(false); // it has no list
  });

  it("stat_keys picks and orders stats; the list limit applies", () => {
    const items = Array.from({ length: 5 }, (_, i) => item(`Row ${i}`));
    const { container } = show("sonarr", { stat_keys: "series, queue", limit: 2 }, ok({ stats: FIXTURES.sonarr.stats, items }));
    expect([...container.querySelectorAll(".istat-mini > span")].map((e) => e.textContent)).toEqual(["Series", "Queue"]);
    expect(container.querySelectorAll(".irow").length).toBe(2);
  });
});

const view = (type, config, data, opts) => show(type, config, ok(data), opts);

describe("qBittorrent pause / resume", () => {
  const data = FIXTURES.qbittorrent;

  it("is hidden for view-only guests", () => {
    view("qbittorrent", {}, data, { canWrite: false });
    expect(screen.queryByRole("button", { name: /pause|resume/i })).toBeNull();
  });

  it("pausing needs a second click", async () => {
    api.integrationAction.mockResolvedValue({});
    const onRefresh = vi.fn();
    view("qbittorrent", {}, data, { onRefresh });
    fireEvent.click(screen.getByRole("button", { name: "Pause all" }));
    expect(api.integrationAction).not.toHaveBeenCalled();
    expect(screen.getByText("Pause all?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(api.integrationAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Pause all" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(api.integrationAction).toHaveBeenCalledExactlyOnceWith("i1", "pause_all", {});
  });

  it("resuming is one click, disables the buttons while it runs, then re-enables them", async () => {
    let finish;
    api.integrationAction.mockImplementation(() => new Promise((r) => { finish = r; }));
    view("qbittorrent", {}, data);
    fireEvent.click(screen.getByRole("button", { name: "Resume all" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Resume all" }).disabled).toBe(true));
    expect(screen.getByRole("button", { name: "Pause all" }).disabled).toBe(true);
    expect(api.integrationAction).toHaveBeenCalledWith("i1", "resume_all", {});
    finish({});
    await waitFor(() => expect(screen.getByRole("button", { name: "Resume all" }).disabled).toBe(false));
  });

  it("shows the server's message when the action fails", async () => {
    api.integrationAction.mockRejectedValue(new Error("login failed: check the qBittorrent password"));
    view("qbittorrent", {}, data);
    fireEvent.click(screen.getByRole("button", { name: "Resume all" }));
    expect((await screen.findByRole("alert")).textContent).toBe("login failed: check the qBittorrent password");
    expect(screen.getByRole("button", { name: "Resume all" }).disabled).toBe(false);
  });
});

describe("SABnzbd pause / resume", () => {
  it("offers Pause (with confirmation) while running and Resume while paused, from the paused stat", async () => {
    api.integrationAction.mockResolvedValue({});
    const running = view("sabnzbd", {}, FIXTURES.sabnzbd);
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(api.integrationAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await waitFor(() => expect(api.integrationAction).toHaveBeenCalledWith("i1", "pause", {}));
    running.unmount();

    const stats = FIXTURES.sabnzbd.stats.map((s) => (s.key === "paused" ? { ...s, value: true } : s));
    view("sabnzbd", {}, { ...FIXTURES.sabnzbd, stats });
    expect(screen.getByText("Paused")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(api.integrationAction).toHaveBeenLastCalledWith("i1", "resume", {}));
  });

  it("is hidden for view-only guests", () => {
    view("sabnzbd", {}, FIXTURES.sabnzbd, { canWrite: false });
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("AdGuard protection control (shared with Pi-hole)", () => {
  it("shows the state and enables / disables protection", async () => {
    api.integrationAction.mockResolvedValue({});
    view("adguard", {}, FIXTURES.adguard);
    expect(screen.getByText("Protection on")).toBeTruthy();
    fireEvent.click(screen.getByText("Disable for 5 min"));
    await waitFor(() => expect(api.integrationAction).toHaveBeenLastCalledWith("i1", "set_protection", { enabled: false, seconds: 300 }));
    fireEvent.click(screen.getByText("Disable"));
    expect(api.integrationAction).toHaveBeenCalledTimes(1); // the untimed disable asks first
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await waitFor(() => expect(api.integrationAction).toHaveBeenLastCalledWith("i1", "set_protection", { enabled: false }));
    cleanup();
    view("adguard", {}, { ...FIXTURES.adguard, blocking: { enabled: false, timer: 120 } });
    expect(screen.getByText("Protection off (2 min left)")).toBeTruthy();
    fireEvent.click(screen.getByText("Enable protection"));
    await waitFor(() => expect(api.integrationAction).toHaveBeenLastCalledWith("i1", "set_protection", { enabled: true }));
  });

  it("is read-only for view-only guests and shows failures", async () => {
    view("adguard", {}, FIXTURES.adguard, { canWrite: false });
    expect(screen.getByText("Protection on")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    cleanup();
    api.integrationAction.mockRejectedValue(new Error("this AdGuard Home version cannot pause protection for a set time"));
    view("adguard", {}, FIXTURES.adguard);
    fireEvent.click(screen.getByText("Disable for 5 min"));
    expect((await screen.findByRole("alert")).textContent).toContain("cannot pause protection");
  });
});

describe("Proxmox guest menu", () => {
  const withActions = (opts) => view("proxmox", { show_actions: true }, FIXTURES.proxmox, opts);

  it("is off unless the checkbox is ticked, and hidden for view-only guests", () => {
    view("proxmox", {}, FIXTURES.proxmox);
    expect(screen.queryByRole("button", { name: /Actions for/ })).toBeNull();
    cleanup();
    withActions({ canWrite: false });
    expect(screen.queryByRole("button", { name: /Actions for/ })).toBeNull();
    expect(WIDGET_TYPES.proxmox.fields.find((f) => f.key === "show_actions").type).toBe("checkbox");
  });

  it("offers shut down / restart / force stop for a running guest and Start for a stopped one", () => {
    withActions();
    fireEvent.click(screen.getByRole("button", { name: "Actions for web" }));
    expect(["Shut down web", "Restart web", "Force stop web"].every((n) => screen.queryByRole("button", { name: n }))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Actions for db" }));
    expect(screen.getByRole("button", { name: "Start db" })).toBeTruthy();
  });

  it("asks for confirmation before anything runs; Cancel runs nothing", async () => {
    api.integrationAction.mockResolvedValue({});
    const onRefresh = vi.fn();
    withActions({ onRefresh });
    fireEvent.click(screen.getByRole("button", { name: "Actions for web" }));
    fireEvent.click(screen.getByRole("button", { name: "Shut down web" }));
    expect(api.integrationAction).not.toHaveBeenCalled();
    const group = screen.getByRole("group", { name: "Confirm Shut down web" });
    fireEvent.click(within(group).getByRole("button", { name: "Cancel" }));
    expect(api.integrationAction).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Actions for web" }));
    fireEvent.click(screen.getByRole("button", { name: "Force stop web" }));
    fireEvent.click(within(screen.getByRole("group", { name: "Confirm Force stop web" })).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(api.integrationAction).toHaveBeenCalledExactlyOnceWith("i1", "guest_power", { node: "pve1", kind: "qemu", vmid: "100", action: "stop" });
  });

  it("sends the right kind and vmid for a container, is busy while it runs, and shows errors", async () => {
    let fail;
    api.integrationAction.mockImplementation(() => new Promise((_, rej) => { fail = rej; }));
    withActions();
    fireEvent.click(screen.getByRole("button", { name: "Actions for db" }));
    fireEvent.click(screen.getByRole("button", { name: "Start db" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(api.integrationAction).toHaveBeenCalledWith("i1", "guest_power", { node: "pve1", kind: "lxc", vmid: "101", action: "start" }));
    expect(screen.getByRole("button", { name: "Confirm" }).disabled).toBe(true); // busy while the request runs
    fail(new Error("HTTP 500 Internal Server Error (pve:8006)"));
    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 500");
  });
});

describe("Portainer container menu", () => {
  const cfg = { show_actions: true };

  it("restarts / stops a running container and starts a stopped one, through a confirm step", async () => {
    api.integrationAction.mockResolvedValue({});
    view("portainer", cfg, FIXTURES.portainer, { integConfig: { endpoint_id: 2 } });
    fireEvent.click(screen.getByRole("button", { name: "Actions for nginx" }));
    expect(screen.getByRole("button", { name: "Stop nginx" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restart nginx" }));
    expect(api.integrationAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(api.integrationAction).toHaveBeenCalledWith("i1", "container", { endpoint_id: 2, container_id: "c0ffee01", action: "restart" }));
    fireEvent.click(screen.getByRole("button", { name: "Actions for old-job" }));
    expect(screen.getByRole("button", { name: "Start old-job" })).toBeTruthy();
  });

  it("works without an Environment ID on the integration: the item's ref names the environment", async () => {
    api.integrationAction.mockResolvedValue({});
    view("portainer", cfg, FIXTURES.portainer);
    expect(screen.queryByText(/Row actions need/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Actions for old-job" }));
    fireEvent.click(screen.getByRole("button", { name: "Start old-job" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(api.integrationAction).toHaveBeenCalledWith("i1", "container", { endpoint_id: 2, container_id: "deadbeef", action: "start" }));
  });

  it("shows a one-line hint, not nothing, when the box is ticked but the listed rows cannot be acted on", () => {
    const rows = [item("local", { subtitle: "unix:///var/run/docker.sock", status: "online" })]; // an environments list: no refs
    view("portainer", cfg, { ...FIXTURES.portainer, items: rows }, { integConfig: { list: "endpoints" } });
    expect(screen.getByText('Row actions need the integration\'s list set to "Containers".')).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Actions for/ })).toBeNull();
    cleanup();
    view("portainer", {}, { ...FIXTURES.portainer, items: rows }, { integConfig: { list: "endpoints" } }); // box not ticked: silent
    expect(screen.queryByText(/Row actions need/)).toBeNull();
    cleanup();
    view("proxmox", { show_actions: true }, { ...FIXTURES.proxmox, items: [item("pve1", { subtitle: "8 cores", status: "online" })] });
    expect(screen.getByText('Row actions need the integration\'s list set to "VMs and containers".')).toBeTruthy();
  });

  it("no menus without the checkbox, for guests, or on the environments / stacks lists", () => {
    view("portainer", {}, FIXTURES.portainer, { integConfig: { endpoint_id: 2 } });
    expect(screen.queryByRole("button", { name: /Actions for/ })).toBeNull();
    cleanup();
    view("portainer", cfg, FIXTURES.portainer, { integConfig: { endpoint_id: 2 }, canWrite: false });
    expect(screen.queryByRole("button", { name: /Actions for/ })).toBeNull();
    cleanup();
    view("portainer", cfg, { ...FIXTURES.portainer, items: [item("media", { status: "online" })] }, { integConfig: { endpoint_id: 2, list: "stacks" } }); // stacks carry no ref
    expect(screen.queryByRole("button", { name: /Actions for/ })).toBeNull();
  });
});

describe("action helpers", () => {
  it("offers the actions that fit a state", () => {
    expect(proxmoxActions("online").map((a) => a.action)).toEqual(["shutdown", "reboot", "stop"]);
    expect(proxmoxActions("offline").map((a) => a.action)).toEqual(["start"]);
    expect(proxmoxActions("maintenance")).toEqual([]);
    expect(portainerActions("online").map((a) => a.action)).toEqual(["restart", "stop"]);
    expect(portainerActions("degraded").map((a) => a.action)).toEqual(["restart", "stop"]);
    expect(portainerActions("offline").map((a) => a.action)).toEqual(["start"]);
    expect(portainerActions("unknown")).toEqual([]);
  });

  it("sectionsOf / toggledState / isOn / pickEntities", () => {
    expect(sectionsOf(["stats"])).toEqual({ stats: true, items: false });
    expect(sectionsOf("items")).toEqual({ stats: false, items: true });
    expect(sectionsOf(undefined)).toEqual({ stats: true, items: true });
    expect(toggledState({ domain: "light", state: "on" })).toBe("off");
    expect(toggledState({ domain: "switch", state: "off" })).toBe("on");
    expect(toggledState({ domain: "cover", state: "open" })).toBe("closed");
    expect(toggledState({ domain: "cover", state: "closed" })).toBe("open");
    expect(isOn("ON")).toBe(true);
    expect(isUnavailable("unavailable")).toBe(true);
    const es = [{ entity_id: "light.a" }, { entity_id: "sensor.b" }, { entity_id: "switch.c" }];
    expect(pickEntities(es, "").map((e) => e.entity_id)).toEqual(["light.a", "sensor.b", "switch.c"]);
    expect(pickEntities(es, "switch.c, nope\nlight.a").map((e) => e.entity_id)).toEqual(["switch.c", "light.a"]);
  });
});
