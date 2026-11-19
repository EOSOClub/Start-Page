import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// Phone layouts end to end in the App: the Desktop | Phone toggle, separate desktop/phone saves and
// undo that stays on its own layout. The api is a recording fake; each test imports a fresh App
// (it reads the localStorage cache at module scope).

const W1 = { id: "w1", type: "text", x: 0, y: 0, w: 6, h: 3, service_id: null, config: { content: "FIRST" } };
const W2 = { id: "w2", type: "link", x: 6, y: 0, w: 3, h: 1, service_id: null, config: { url: "https://example.com", name: "Example" } };
const PHONE_W1 = { x: 0, y: 0, w: 4, h: 3, hidden: false };
const PHONE_W2 = { x: 0, y: 3, w: 2, h: 1, hidden: false };

const calls = vi.hoisted(() => ({ saveLayout: [], clearPhoneLayout: [], updateDashboard: [], updateWidget: [] }));

vi.mock("./api.js", () => {
  const rec = (name, result) => (...args) => {
    calls[name]?.push(args);
    return Promise.resolve(typeof result === "function" ? result(...args) : result ?? null);
  };
  const known = {
    listDashboards: () => Promise.resolve([{ id: "p1", name: "Home" }]),
    getDashboard: () => Promise.resolve(globalThis.__phonePage),
    revision: () => Promise.resolve({ boot: "b", rev: 0 }),
    listServices: () => Promise.resolve([]),
    health: () => Promise.resolve({}),
    listIntegrations: () => Promise.resolve([]),
    integrationData: () => Promise.resolve({}),
    getSettings: () => Promise.resolve({}),
    saveLayout: rec("saveLayout", []),
    clearPhoneLayout: rec("clearPhoneLayout"),
    updateDashboard: rec("updateDashboard", {}),
    updateWidget: rec("updateWidget", {}),
  };
  return { api: new Proxy({}, { get: (_t, name) => known[name] || (() => Promise.resolve(null)) }) };
});

async function mountApp(page) {
  globalThis.__phonePage = page;
  localStorage.clear();
  localStorage.setItem(
    "sp.cache.v1",
    JSON.stringify({ dashboards: [{ id: "p1", name: "Home" }], pages: { p1: page }, services: [], integrations: [], settings: {} })
  );
  localStorage.setItem("sp.dashboard", "p1");
  vi.resetModules();
  const { default: App } = await import("./App.jsx");
  const view = render(<App />);
  await waitFor(() => screen.getByText("FIRST"));
  return view;
}

const customPage = () => ({
  id: "p1", name: "Home", columns: 24, row_height: 40,
  settings: { phone_layout: "custom", columns_sm: 4 },
  widgets: [{ ...W1, layout_sm: PHONE_W1 }, { ...W2, layout_sm: PHONE_W2 }],
});
const autoPage = () => ({ id: "p1", name: "Home", columns: 24, row_height: 40, settings: {}, widgets: [{ ...W1, layout_sm: null }, { ...W2, layout_sm: null }] });

const key = (k, extra = {}) => fireEvent.keyDown(document.body, { key: k, ...extra });
const enterEdit = async () => {
  key("e");
  await waitFor(() => screen.getByRole("button", { name: "Phone" }));
};
const pickPhone = () => fireEvent.click(screen.getByRole("button", { name: "Phone" }));
const pickDesktop = () => fireEvent.click(screen.getByRole("button", { name: "Desktop" }));
const select = (container, index = 0) => fireEvent.mouseDown(container.querySelectorAll(".widget-overlay")[index]);

beforeEach(() => {
  for (const k of Object.keys(calls)) calls[k].length = 0;
  window.innerWidth = 1280;
});
afterEach(cleanup);

describe("Desktop | Phone toggle", () => {
  it("shows the toggle only in edit mode and starts on Desktop", async () => {
    await mountApp(autoPage());
    expect(screen.queryByRole("button", { name: "Phone" })).toBeNull();
    await enterEdit();
    expect(screen.getByRole("button", { name: "Desktop" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Phone" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("an auto page opens a phone-width frame with the offer to start from the automatic layout", async () => {
    const { container } = await mountApp(autoPage());
    await enterEdit();
    pickPhone();
    expect(screen.getByTestId("phone-frame").style.width).toContain("390px");
    expect(screen.getByRole("button", { name: "Start from the automatic layout" })).toBeTruthy();
    expect(container.querySelector(".react-grid-layout")).toBeNull(); // a read-only stack preview, no drag grid
    pickDesktop();
    expect(screen.queryByTestId("phone-frame")).toBeNull();
    expect(container.querySelector(".react-grid-layout")).not.toBeNull();
  });

  it("a custom page edits on a real phone grid inside the frame", async () => {
    const { container } = await mountApp(customPage());
    await enterEdit();
    pickPhone();
    expect(screen.getByTestId("phone-frame")).toBeTruthy();
    expect(container.querySelector(".react-grid-layout")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Start from the automatic layout" })).toBeNull();
    expect(screen.getByRole("button", { name: "Reset to automatic" })).toBeTruthy();
    pickDesktop();
    expect(screen.queryByRole("button", { name: "Reset to automatic" })).toBeNull();
  });

  it("opens edit mode on Phone when the window is phone-sized", async () => {
    window.innerWidth = 400;
    await mountApp(customPage());
    key("e");
    await waitFor(() => screen.getByTestId("phone-frame"));
    expect(screen.getByRole("button", { name: "Phone" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("starting and resetting the phone layout", () => {
  it("seeds layout_sm for every widget, switches the page to custom, and undoes as one step", async () => {
    await mountApp(autoPage());
    await enterEdit();
    pickPhone();
    fireEvent.click(screen.getByRole("button", { name: "Start from the automatic layout" }));
    await waitFor(() => expect(calls.updateDashboard.length).toBe(1));

    const [dashId, items, bp] = calls.saveLayout[0];
    expect([dashId, bp]).toEqual(["p1", "sm"]);
    expect(items.map((i) => i.id).sort()).toEqual(["w1", "w2"]);
    for (const i of items) expect(i.x + i.w).toBeLessThanOrEqual(4);
    expect(calls.updateDashboard[0]).toEqual(["p1", { settings: { phone_layout: "custom" } }]);
    expect(calls.saveLayout.every((c) => c[2] === "sm")).toBe(true); // the desktop layout was never written

    // the server now holds the seeded positions: undo removes them one widget at a time (no all-or-nothing clear)
    globalThis.__phonePage = { ...autoPage(), settings: { phone_layout: "custom" }, widgets: items.map((i) => ({ ...(i.id === "w1" ? W1 : W2), layout_sm: i })) };
    key("z", { ctrlKey: true });
    await waitFor(() => expect(calls.updateDashboard.length).toBe(2));
    expect(calls.clearPhoneLayout).toEqual([]);
    expect(calls.updateWidget.map((c) => [c[0], c[1]]).sort()).toEqual([["w1", { layout_sm: null }], ["w2", { layout_sm: null }]]);
    expect(calls.updateDashboard[1]).toEqual(["p1", { settings: {} }]);
  });

  it("undo of Reset to automatic still works after columns_sm was reduced: positions are clamped and saved, nothing is cleared first", async () => {
    await mountApp({ ...customPage(), widgets: [{ ...W1, layout_sm: { x: 2, y: 0, w: 2, h: 3, hidden: false } }, { ...W2, layout_sm: PHONE_W2 }] });
    await enterEdit();
    pickPhone();
    fireEvent.click(screen.getByRole("button", { name: "Reset to automatic" }));
    await waitFor(() => expect(calls.updateDashboard.length).toBe(1));
    // meanwhile the page's phone grid was narrowed to 2 columns
    globalThis.__phonePage = { ...customPage(), settings: { phone_layout: "auto", columns_sm: 2 }, widgets: [{ ...W1, layout_sm: null }, { ...W2, layout_sm: null }] };
    key("z", { ctrlKey: true });
    await waitFor(() => expect(calls.updateDashboard.length).toBe(2));
    const [, items, bp] = calls.saveLayout.at(-1);
    expect(bp).toBe("sm");
    for (const i of items) expect(i.x + i.w).toBeLessThanOrEqual(2);
    expect(calls.clearPhoneLayout.length).toBe(1); // only the forward step cleared; the undo did not clear before saving
    expect(calls.updateDashboard[1][1].settings.phone_layout).toBe("custom");
  });

  it("switching a page to Custom in Page settings runs the same seed as the toolbar button", async () => {
    await mountApp(autoPage());
    await enterEdit();
    fireEvent.click(screen.getByRole("button", { name: "Page settings" }));
    const select = await screen.findByLabelText("Phone layout");
    fireEvent.change(select, { target: { value: "custom" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.updateDashboard.length).toBe(1));
    const [dashId, items, bp] = calls.saveLayout[0];
    expect([dashId, bp]).toEqual(["p1", "sm"]);
    expect(items.map((i) => i.id).sort()).toEqual(["w1", "w2"]);
    expect(calls.updateDashboard[0][1].settings.phone_layout).toBe("custom");
    expect(calls.updateDashboard[0][1].name).toBe("Home");
  });

  it("Reset to automatic clears the phone layout and undo restores it", async () => {
    await mountApp(customPage());
    await enterEdit();
    pickPhone();
    fireEvent.click(screen.getByRole("button", { name: "Reset to automatic" }));
    await waitFor(() => expect(calls.updateDashboard.length).toBe(1));
    expect(calls.clearPhoneLayout).toEqual([["p1"]]);
    expect(calls.updateDashboard[0][1].settings.phone_layout).toBe("auto");

    key("z", { ctrlKey: true });
    await waitFor(() => expect(calls.updateDashboard.length).toBe(2));
    const restored = calls.saveLayout.at(-1);
    expect(restored[2]).toBe("sm");
    expect(restored[1].map((i) => i.id).sort()).toEqual(["w1", "w2"]); // the stored phone positions come back
    expect(calls.updateDashboard[1][1].settings.phone_layout).toBe("custom");
  });
});

describe("the two layouts stay separate", () => {
  it("undo of a phone move of an unpinned widget restores 'no phone position', not the derived spot", async () => {
    const page = { ...customPage(), widgets: [{ ...W1, layout_sm: PHONE_W1 }, { ...W2, layout_sm: null }] };
    const { container } = await mountApp(page);
    await enterEdit();
    pickPhone();
    select(container, 1); // w2, shown at its derived spot
    key("ArrowDown");
    await waitFor(() => expect(calls.saveLayout.length).toBe(1));
    expect(calls.saveLayout[0][1][0].id).toBe("w2");
    key("z", { ctrlKey: true });
    await waitFor(() => expect(calls.updateWidget.length).toBe(1));
    expect(calls.updateWidget[0].slice(0, 2)).toEqual(["w2", { layout_sm: null }]);
    expect(calls.saveLayout.length).toBe(1); // no second save pinning it at the derived spot
  });

  it("a phone move saves the phone layout only, and its undo restores only the phone layout", async () => {
    const { container } = await mountApp(customPage());
    await enterEdit();
    pickPhone();
    select(container, 0); // w1
    key("ArrowDown");
    await waitFor(() => expect(calls.saveLayout.length).toBe(1));
    expect(calls.saveLayout[0]).toEqual(["p1", [{ id: "w1", x: 0, y: 1, w: 4, h: 3 }], "sm"]);

    key("z", { ctrlKey: true });
    await waitFor(() => expect(calls.saveLayout.length).toBe(2));
    const [dashId, before, bp] = calls.saveLayout[1];
    expect([dashId, bp]).toEqual(["p1", "sm"]);
    expect(before).toEqual([{ id: "w1", x: 0, y: 0, w: 4, h: 3, hidden: false }]); // the phone position from before the move
    expect(calls.saveLayout.some((c) => c[2] !== "sm")).toBe(false); // nothing ever touched the desktop layout
  });

  it("a desktop move saves the desktop layout only (the existing call shape), even on a page with a phone layout", async () => {
    const { container } = await mountApp(customPage());
    await enterEdit();
    select(container, 0);
    key("ArrowRight");
    await waitFor(() => expect(calls.saveLayout.length).toBe(1));
    expect(calls.saveLayout[0]).toEqual(["p1", [{ id: "w1", x: 1, y: 0, w: 6, h: 3 }], "lg"]); // "lg" sends no query (see api.layout.test.js)

    key("z", { ctrlKey: true });
    await waitFor(() => expect(calls.saveLayout.length).toBe(2));
    expect(calls.saveLayout[1]).toEqual(["p1", [{ id: "w1", x: 0, y: 0, w: 6, h: 3 }], "lg"]);
  });

  it("a desktop entry and a phone entry on the stack each undo on their own layout", async () => {
    const { container } = await mountApp(customPage());
    await enterEdit();
    select(container, 0);
    key("ArrowRight"); // desktop move
    await waitFor(() => expect(calls.saveLayout.length).toBe(1));
    pickPhone();
    select(container, 0);
    key("ArrowDown"); // phone move
    await waitFor(() => expect(calls.saveLayout.length).toBe(2));

    key("z", { ctrlKey: true }); // undoes the phone move
    await waitFor(() => expect(calls.saveLayout.length).toBe(3));
    expect(calls.saveLayout[2][2]).toBe("sm");
    key("z", { ctrlKey: true }); // then the desktop move
    await waitFor(() => expect(calls.saveLayout.length).toBe(4));
    expect(calls.saveLayout[3][2]).toBe("lg");
    expect(calls.saveLayout[3][1]).toEqual([{ id: "w1", x: 0, y: 0, w: 6, h: 3 }]);
  });

  it("Hide on phone is one undoable step that keeps the position", async () => {
    await mountApp(customPage());
    await enterEdit();
    pickPhone();
    fireEvent.click(screen.getAllByRole("button", { name: "Hide on phone" })[0]);
    await waitFor(() => expect(calls.saveLayout.length).toBe(1));
    expect(calls.saveLayout[0]).toEqual(["p1", [{ id: "w1", x: 0, y: 0, w: 4, h: 3, hidden: true }], "sm"]);
    await waitFor(() => screen.getByRole("button", { name: "Show on phone" })); // still listed, dimmed, so it can come back

    key("z", { ctrlKey: true });
    await waitFor(() => expect(calls.saveLayout.length).toBe(2));
    expect(calls.saveLayout[1][1]).toEqual([{ id: "w1", x: 0, y: 0, w: 4, h: 3, hidden: false }]);
  });
});

describe("view mode on a narrow window", () => {
  it("a custom page renders its phone grid, without hidden widgets", async () => {
    window.innerWidth = 400;
    const page = customPage();
    page.widgets[1].layout_sm = { ...PHONE_W2, hidden: true };
    const { container } = await mountApp(page);
    expect(container.querySelector(".react-grid-layout")).not.toBeNull();
    expect(container.querySelector(".canvas-stacked")).toBeNull();
    expect(screen.getByText("FIRST")).toBeTruthy();
    expect(container.querySelector(".widget-link")).toBeNull(); // w2 is hidden on phones
  });

  it("an auto page still stacks automatically", async () => {
    window.innerWidth = 400;
    const { container } = await mountApp(autoPage());
    expect(container.querySelector(".canvas-stacked")).not.toBeNull();
    expect(container.querySelector(".widget-link")).not.toBeNull();
  });
});
