import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

vi.mock("react-grid-layout", () => ({
  default: ({ children }) => <div data-testid="rgl">{children}</div>,
  WidthProvider: (C) => C,
}));

import Canvas, { widgetClassName } from "./Canvas.jsx";
import { WIDGET_TYPES } from "../widgets/index.jsx";

afterEach(cleanup);

const DASH = { id: "p1", columns: 24, row_height: 40, settings: { phone_layout: "custom" }, widgets: [] };
const W = (o) => ({ x: 0, y: 0, w: 6, h: 3, service_id: null, config: {}, ...o });
const baseProps = { dashboard: DASH, services: [], health: {}, history: {}, integrations: [], integrationData: {}, onRefresh() {}, editMode: false };

describe("widget frame classes", () => {
  it("separates every class with a space (no 'widget-dividerwidget-bare')", () => {
    const divider = widgetClassName(W({ id: "a", type: "divider" }), WIDGET_TYPES.divider);
    expect(divider.split(" ")).toEqual(expect.arrayContaining(["widget", "widget-divider", "widget-bare"]));
    expect(divider).not.toContain("dividerwidget");
    const box = widgetClassName(W({ id: "b", type: "box" }), WIDGET_TYPES.box);
    expect(box.split(" ")).toContain("widget-bare");
  });

  it("marks a transparent widget bare and an ordinary one not", () => {
    const t = W({ id: "t", type: "text", config: { transparent: true } });
    expect(widgetClassName(t, WIDGET_TYPES.text).split(" ")).toContain("widget-bare");
    const n = W({ id: "n", type: "text" });
    expect(widgetClassName(n, WIDGET_TYPES.text).split(" ")).not.toContain("widget-bare");
    expect(widgetClassName(n, WIDGET_TYPES.text, { selected: true, hidden: true }).split(" ")).toEqual(
      expect.arrayContaining(["widget-selected", "widget-phone-hidden"])
    );
  });

  it("the rendered canvas carries the bare class on dividers, boxes and transparent widgets", () => {
    const widgets = [
      W({ id: "d", type: "divider" }),
      W({ id: "b", type: "box", y: 4 }),
      W({ id: "t", type: "text", y: 8, config: { transparent: true, content: "hi" } }),
      W({ id: "c", type: "text", y: 12, config: { content: "card" } }),
    ];
    const { container } = render(<Canvas {...baseProps} widgets={widgets} />);
    const cls = (id) => container.querySelector(`[data-widget-id="${id}"]`).className.split(" ");
    for (const id of ["d", "b", "t"]) expect(cls(id), id).toContain("widget-bare");
    expect(cls("c")).not.toContain("widget-bare");
    expect(cls("d")).toContain("widget-divider");
  });
});

describe("device toggle", () => {
  it("keeps widget instances mounted when switching between the desktop grid and the phone frame", () => {
    const widgets = [W({ id: "t", type: "text", config: { content: "hello" } })];
    const { container, rerender } = render(<Canvas {...baseProps} editMode widgets={widgets} device="desktop" />);
    const before = container.querySelector('[data-widget-id="t"]');
    rerender(<Canvas {...baseProps} editMode widgets={widgets} device="phone" />);
    expect(container.querySelector('[data-testid="phone-frame"]')).not.toBeNull();
    expect(container.querySelector('[data-widget-id="t"]')).toBe(before);
    rerender(<Canvas {...baseProps} editMode widgets={widgets} device="desktop" />);
    expect(container.querySelector('[data-testid="phone-frame"]')).toBeNull();
    expect(container.querySelector('[data-widget-id="t"]')).toBe(before);
  });
});
