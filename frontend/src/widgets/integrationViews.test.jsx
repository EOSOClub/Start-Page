import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ integrationAction: vi.fn() }));
vi.mock("../api.js", () => ({ api }));

import { IntegrationListWidget, IntegrationStatsWidget, PiholeWidget, PrometheusWidget } from "./integrationViews.jsx";
import { WIDGET_TYPES, defaultConfig } from "./index.jsx";
import { AppContext } from "../context.js";
import { dotStatus, formatValue, parseKeys, pickStats, progressPercent, safeLink, seriesBuckets } from "./integrationViz.js";

afterEach(cleanup);
beforeEach(() => api.integrationAction.mockReset());

const INTEG = [{ id: "i1", name: "Hole", type: "pihole", config: {} }];
const ok = (data) => ({ i1: { integration_id: "i1", ok: true, fetched_at: 1, error: null, data } });
const STATS = [
  { key: "queries", label: "Queries today", value: 12345 },
  { key: "blocked", label: "Blocked", value: 2345, tone: "warn" },
  { key: "pct", label: "Percent blocked", value: 19.04, unit: "%" },
  { key: "load", label: "Load", value: 1.5, series: [1, 2, 3, 2] },
];

const show = (def, config, integrationData, integrations = INTEG, extra = {}) =>
  render(<def.Render widget={{ id: "w", type: "x", config }} integrations={integrations} integrationData={integrationData} {...extra} />);

describe("integration_stats", () => {
  it("asks for an integration, waits, and reports errors", () => {
    show(IntegrationStatsWidget, {}, {}, []);
    expect(screen.getByText(/Select a stats integration/)).toBeTruthy();
    cleanup();
    show(IntegrationStatsWidget, { integration_id: "i1" }, {});
    expect(screen.getByText("Waiting for first fetch…")).toBeTruthy();
    cleanup();
    show(IntegrationStatsWidget, { integration_id: "i1" }, { i1: { ok: false, error: "HTTP 401 Unauthorized (h:80)", data: null } });
    expect(screen.getByText("Error: HTTP 401 Unauthorized (h:80)")).toBeTruthy();
  });

  it("says so when the integration has no stats, and when no key matches", () => {
    show(IntegrationStatsWidget, { integration_id: "i1" }, ok({ title: "x" }));
    expect(screen.getByText("Hole does not provide stats.")).toBeTruthy();
    cleanup();
    show(IntegrationStatsWidget, { integration_id: "i1", stat_keys: "nope" }, ok({ stats: STATS }));
    expect(screen.getByText("No matching stats.")).toBeTruthy();
  });

  it("shows tiles in the chosen order with units, tones and a sparkline", () => {
    const { container } = show(IntegrationStatsWidget, { integration_id: "i1", stat_keys: "blocked, load, queries", title: "DNS" }, ok({ stats: STATS }));
    expect(screen.getByText("DNS")).toBeTruthy();
    expect([...container.querySelectorAll(".istat > span")].map((e) => e.textContent)).toEqual(["Blocked", "Load", "Queries today"]);
    expect(container.querySelector(".istat b.tone-warn").textContent).toBe("2,345");
    expect(container.querySelector(".istat svg.spark polyline")).toBeTruthy();
    expect(container.querySelectorAll("svg.spark").length).toBe(1); // only the stat with a series
  });

  it("hides sparklines when switched off and supports the rows layout", () => {
    const off = show(IntegrationStatsWidget, { integration_id: "i1", layout: "rows", show_sparkline: false }, ok({ stats: STATS }));
    expect(off.container.querySelector("svg.spark")).toBeNull();
    expect([...off.container.querySelectorAll(".istat-row .kuma-name")].map((e) => e.textContent)).toEqual(STATS.map((s) => s.label));
    expect(off.container.textContent).toContain("19");
    off.unmount();
    const on = show(IntegrationStatsWidget, { integration_id: "i1", layout: "rows" }, ok({ stats: STATS }));
    expect(on.container.querySelector(".istat-row svg.spark")).toBeTruthy();
  });

  it("keeps showing the last data, marked stale, when a refresh failed", () => {
    show(IntegrationStatsWidget, { integration_id: "i1" }, { i1: { ok: false, error: "timeout", data: { stats: STATS } } });
    expect(screen.getByText("stale")).toBeTruthy();
    expect(screen.getByText("Queries today")).toBeTruthy();
  });

  it("the Prometheus preset is the same widget limited to prometheus integrations", () => {
    expect(PrometheusWidget.Render).toBe(IntegrationStatsWidget.Render);
    expect(PrometheusWidget.fields[0]).toMatchObject({ type: "integration", integrationType: "prometheus" });
    expect(IntegrationStatsWidget.fields[0]).toMatchObject({ type: "integration", capability: "stats" });
    expect(WIDGET_TYPES.prometheus.label).toBe("Prometheus");
  });
});

describe("integration_list", () => {
  const ITEMS = [
    { title: "Router", subtitle: "Core", status: "online", meta: "3 ms" },
    { title: "NAS", status: "maintenance", progress: 41.6, url: "https://nas.lan" },
    { title: "Evil", url: "javascript:alert(1)" },
    { title: "Fourth" },
  ];

  it("has an empty state", () => {
    show(IntegrationListWidget, { integration_id: "i1" }, ok({ items: [] }));
    expect(screen.getByText("Nothing to show.")).toBeTruthy();
    cleanup();
    show(IntegrationListWidget, { integration_id: "i1" }, ok({ stats: [] }));
    expect(screen.getByText("Hole does not provide items.")).toBeTruthy();
  });

  it("shows an error when nothing was ever fetched", () => {
    show(IntegrationListWidget, { integration_id: "i1" }, { i1: { ok: false, error: "connection refused (h:1)" } });
    expect(screen.getByText("Error: connection refused (h:1)")).toBeTruthy();
  });

  it("renders dots, subtitle, meta, progress and only safe links, up to the limit", () => {
    const { container } = show(IntegrationListWidget, { integration_id: "i1", limit: 3 }, ok({ items: ITEMS }));
    expect(container.querySelectorAll(".irow").length).toBe(3);
    expect(screen.queryByText("Fourth")).toBeNull();
    expect(container.querySelector(".status-online")).toBeTruthy();
    expect(container.querySelector(".status-degraded")).toBeTruthy(); // maintenance
    expect(screen.getByText("Core")).toBeTruthy();
    expect(screen.getByText("3 ms")).toBeTruthy();
    expect(container.querySelector("[role=progressbar]").getAttribute("aria-valuenow")).toBe("42");
    expect(container.querySelector("[role=progressbar] i").style.width).toBe("41.6%");
    expect(screen.getByText("NAS").closest("a").getAttribute("href")).toBe("https://nas.lan");
    expect(screen.getByText("Evil").closest("a")).toBeNull();
  });
});

describe("pihole widget", () => {
  const data = (enabled, timer = null) => ({
    version: 6,
    blocking: { enabled, timer },
    stats: [
      { key: "queries", label: "Queries today", value: 100 },
      { key: "status", label: "Blocking", value: enabled ? "enabled" : "disabled" },
    ],
  });
  const view = (enabled, canWrite = true, onRefresh = vi.fn()) =>
    render(
      <AppContext.Provider value={{ canWrite }}>
        <PiholeWidget.Render widget={{ id: "w", config: { integration_id: "i1", ...defaultConfig("pihole") } }} integrations={INTEG} integrationData={ok(data(enabled))} onRefresh={onRefresh} />
      </AppContext.Provider>
    );

  it("shows the stats and the default stat selection", () => {
    view(true);
    expect(screen.getByText("Queries today")).toBeTruthy();
    expect(screen.queryByText("Blocking", { selector: ".istat > span" })).toBeNull(); // status is not in the default keys
    expect(screen.getByText("Blocking on")).toBeTruthy();
  });

  it("switches blocking off for 5 minutes or indefinitely, then refreshes", async () => {
    api.integrationAction.mockResolvedValue({});
    const onRefresh = vi.fn();
    view(true, true, onRefresh);
    fireEvent.click(screen.getByText("Disable for 5 min"));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(api.integrationAction).toHaveBeenLastCalledWith("i1", "set_blocking", { enabled: false, seconds: 300 });
    fireEvent.click(screen.getByText("Disable"));
    fireEvent.click(screen.getByRole("button", { name: "Yes" })); // the untimed disable asks first
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(2));
    expect(api.integrationAction).toHaveBeenLastCalledWith("i1", "set_blocking", { enabled: false });
    expect(screen.queryByText("Enable blocking")).toBeNull();
  });

  it("offers Enable while blocking is off", async () => {
    api.integrationAction.mockResolvedValue({});
    view(false);
    expect(screen.getByText("Blocking off")).toBeTruthy();
    expect(screen.queryByText("Disable")).toBeNull();
    fireEvent.click(screen.getByText("Enable blocking"));
    await waitFor(() => expect(api.integrationAction).toHaveBeenCalledWith("i1", "set_blocking", { enabled: true }));
  });

  it("is read-only for view-only guests", () => {
    view(true, false);
    expect(screen.getByText("Blocking on")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows the server's message when the action fails", async () => {
    api.integrationAction.mockImplementationOnce(async () => {
      throw new Error("login failed: check the Pi-hole password");
    });
    view(true);
    fireEvent.click(screen.getByText("Disable"));
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await waitFor(() => expect(api.integrationAction).toHaveBeenCalled());
    await screen.findByText("login failed: check the Pi-hole password");
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});

describe("registry", () => {
  it("registers the new widget types with defaults", () => {
    for (const t of ["integration_stats", "integration_list", "pihole", "prometheus"]) expect(WIDGET_TYPES[t]).toBeTruthy();
    expect(defaultConfig("integration_stats")).toEqual({ layout: "tiles", show_sparkline: true });
    expect(defaultConfig("integration_list")).toEqual({ limit: 10 });
  });
});

describe("helpers", () => {
  it("parseKeys / pickStats", () => {
    expect(parseKeys(" Queries,blocked\n  x ")).toEqual(["queries", "blocked", "x"]);
    expect(pickStats(STATS, "").length).toBe(4);
    expect(pickStats(STATS, "LOAD, nope, queries").map((s) => s.key)).toEqual(["load", "queries"]);
    expect(pickStats(undefined, "a")).toEqual([]);
  });

  it("formatValue / progress / links / series", () => {
    expect(formatValue(null)).toBe("—");
    expect(formatValue(true)).toBe("Yes");
    expect(formatValue(1234567)).toBe((1234567).toLocaleString());
    expect(formatValue(19.04)).toBe((19).toLocaleString(undefined, { minimumFractionDigits: 0 }));
    expect(formatValue("enabled")).toBe("enabled");
    expect(progressPercent(150)).toBe(100);
    expect(progressPercent(-3)).toBe(0);
    expect(progressPercent(undefined)).toBeNull();
    expect(safeLink("http://a")).toBe("http://a");
    expect(safeLink("javascript:x")).toBeNull();
    expect(dotStatus("maintenance")).toBe("degraded");
    expect(dotStatus(undefined)).toBe("unknown");
    expect(seriesBuckets([1, 2])).toEqual([{ up: 1, total: 1, latency_ms: 1 }, { up: 1, total: 1, latency_ms: 2 }]);
  });
});
