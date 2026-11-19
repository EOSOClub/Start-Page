import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";

const api = vi.hoisted(() => ({ integrationTypes: vi.fn().mockResolvedValue([]) }));
vi.mock("../api.js", () => ({ api }));

import WidgetMenu from "./WidgetMenu.jsx";
import ConfigForm from "./ConfigForm.jsx";
import { groupWidgetTypes } from "./widgetMenu.js";
import { WIDGET_GROUPS, WIDGET_TYPES } from "../widgets/index.jsx";

afterEach(cleanup);

const TYPES = {
  link: { label: "Link", group: "Basics" },
  clock: { label: "Clock", group: "Basics" },
  service: { label: "Service", group: "Services" },
  home_assistant: { label: "Home Assistant", group: "Integrations" },
  sonarr: { label: "Sonarr", group: "Integrations" },
  divider: { label: "Divider", group: "Decoration" },
  odd: { label: "Odd one", group: "Somewhere" },
};
const GROUPS = ["Basics", "Services", "Integrations", "Decoration"];

describe("groupWidgetTypes", () => {
  it("sections in group order, items in registry order, unknown groups last", () => {
    const s = groupWidgetTypes(TYPES, GROUPS);
    expect(s.map((x) => x.group)).toEqual(["Basics", "Services", "Integrations", "Decoration", "Other"]);
    expect(s[0].items.map((i) => i.type)).toEqual(["link", "clock"]);
  });

  it("filters by label, type key or group name, every word must match, empty groups vanish", () => {
    expect(groupWidgetTypes(TYPES, GROUPS, "son").flatMap((s) => s.items.map((i) => i.type))).toEqual(["sonarr"]);
    expect(groupWidgetTypes(TYPES, GROUPS, "home assistant").flatMap((s) => s.items.map((i) => i.type))).toEqual(["home_assistant"]);
    expect(groupWidgetTypes(TYPES, GROUPS, "DECOR").map((s) => s.group)).toEqual(["Decoration"]);
    expect(groupWidgetTypes(TYPES, GROUPS, "integrations son").flatMap((s) => s.items.map((i) => i.type))).toEqual(["sonarr"]);
    expect(groupWidgetTypes(TYPES, GROUPS, "zzz")).toEqual([]);
  });

  it("puts every real widget in a real group", () => {
    const s = groupWidgetTypes(WIDGET_TYPES, WIDGET_GROUPS);
    expect(s.map((x) => x.group)).toEqual(WIDGET_GROUPS);
    expect(s.reduce((n, x) => n + x.items.length, 0)).toBe(Object.keys(WIDGET_TYPES).length);
  });
});

function Harness({ onPick = () => {}, startOpen = false }) {
  const [open, setOpen] = useState(startOpen);
  return <WidgetMenu types={TYPES} groups={GROUPS} open={open} onOpenChange={setOpen} onPick={onPick} />;
}

describe("WidgetMenu", () => {
  it("opens by button, lists grouped entries, adds the one clicked and closes", () => {
    const onPick = vi.fn();
    render(<Harness onPick={onPick} />);
    expect(screen.queryByRole("group")).toBeNull();
    const button = screen.getByRole("button", { name: /\+ Widget/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    const integrations = screen.getByRole("group", { name: "Integrations" });
    expect(within(integrations).getAllByRole("button").map((b) => b.textContent)).toEqual(["Home Assistant", "Sonarr"]);
    fireEvent.click(within(integrations).getByText("Sonarr"));
    expect(onPick).toHaveBeenCalledWith("sonarr");
    expect(screen.queryByRole("group", { name: "Integrations" })).toBeNull();
  });

  it("focuses the filter when opened from outside (Shift+W) and filters as you type", () => {
    render(<Harness startOpen />);
    const filter = screen.getByRole("searchbox", { name: "Filter widget types" });
    expect(document.activeElement).toBe(filter);
    fireEvent.change(filter, { target: { value: "clo" } });
    expect(screen.getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Basics"]);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toContain("Clock");
    expect(screen.queryByText("Sonarr")).toBeNull();
    fireEvent.change(filter, { target: { value: "nothing like this" } });
    expect(screen.getByText(/No widget matches/)).toBeTruthy();
  });

  it("Enter adds the first match; arrows move through the entries; Escape closes", () => {
    const onPick = vi.fn();
    render(<Harness startOpen onPick={onPick} />);
    const filter = screen.getByRole("searchbox");
    fireEvent.keyDown(filter, { key: "ArrowDown" });
    expect(document.activeElement.textContent).toBe("Link");
    fireEvent.keyDown(document.activeElement, { key: "ArrowDown" });
    expect(document.activeElement.textContent).toBe("Clock");
    fireEvent.keyDown(document.activeElement, { key: "ArrowUp" });
    fireEvent.keyDown(document.activeElement, { key: "ArrowUp" });
    expect(document.activeElement).toBe(filter);
    fireEvent.change(filter, { target: { value: "serv" } });
    fireEvent.keyDown(filter, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith("service");
    cleanup();
    render(<Harness startOpen />);
    fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("closes when clicking elsewhere", () => {
    render(<div><span>outside</span><Harness startOpen /></div>);
    fireEvent.mouseDown(screen.getByText("outside"));
    expect(screen.queryByRole("searchbox")).toBeNull();
  });
});

describe("ConfigForm: stat key chips and multi-select integrations", () => {
  const fields = [
    { key: "integration_id", label: "Integration", type: "integration", integrationType: "pihole" },
    { key: "stat_keys", label: "Stats", type: "statkeys", placeholder: "(all)" },
  ];
  const integrations = [{ id: "i1", name: "Hole", type: "pihole" }];
  const data = { i1: { ok: true, data: { stats: [{ key: "queries", label: "Queries", value: 5 }, { key: "blocked", label: "Blocked", value: 2, unit: "x" }] } } };
  function Form({ initial, extra = {}, f = fields }) {
    const [value, setValue] = useState(initial);
    return (
      <>
        <ConfigForm fields={f} value={value} onChange={setValue} integrations={integrations} integrationData={data} {...extra} />
        <output data-testid="value">{JSON.stringify(value)}</output>
      </>
    );
  }
  const val = () => JSON.parse(screen.getByTestId("value").textContent);

  it("offers the selected integration's stat keys as chips that toggle in the text", () => {
    render(<Form initial={{ integration_id: "i1" }} />);
    const group = screen.getByRole("group", { name: "Available stats" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["queries", "blocked"]);
    fireEvent.click(within(group).getByText("blocked"));
    fireEvent.click(within(group).getByText("queries"));
    expect(val().stat_keys).toBe("blocked, queries");
    expect(within(group).getByText("blocked").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(within(group).getByText("blocked"));
    expect(val().stat_keys).toBe("queries");
  });

  it("shows no chips without data, and a single-key field replaces its value", () => {
    render(<Form initial={{}} />);
    expect(screen.queryByRole("group", { name: "Available stats" })).toBeNull();
    cleanup();
    render(<Form initial={{ integration_id: "i1", stat_key: "queries" }} f={[fields[0], { key: "stat_key", label: "Stat", type: "statkeys", multiple: false }]} />);
    fireEvent.click(screen.getByText("blocked"));
    expect(val().stat_key).toBe("blocked");
    fireEvent.click(screen.getByText("blocked"));
    expect(val().stat_key).toBe("");
  });

  it("a multiple integration field is a checklist of ids", () => {
    const two = [{ id: "a", name: "Family", type: "ics" }, { id: "b", name: "Work", type: "ics" }, { id: "c", name: "Other", type: "pihole" }];
    function Multi() {
      const [value, setValue] = useState({});
      return (
        <>
          <ConfigForm fields={[{ key: "integration_ids", label: "Feeds", type: "integration", integrationType: "ics", multiple: true }]} value={value} onChange={setValue} integrations={two} />
          <output data-testid="value">{JSON.stringify(value)}</output>
        </>
      );
    }
    render(<Multi />);
    expect(screen.getAllByRole("checkbox").map((c) => c.closest("label").textContent)).toEqual(["Family (ics)", "Work (ics)"]);
    fireEvent.click(screen.getByLabelText(/Work/));
    fireEvent.click(screen.getByLabelText(/Family/));
    expect(val().integration_ids).toEqual(["b", "a"]);
    fireEvent.click(screen.getByLabelText(/Work/));
    expect(val().integration_ids).toEqual(["a"]);
  });
});
