import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

const api = vi.hoisted(() => ({ listTemplates: vi.fn(), deleteIntegration: vi.fn(), integrationTypes: vi.fn(), listIntegrations: vi.fn() }));
vi.mock("../api.js", () => ({ api }));

import Modal from "./Modal.jsx";
import NewPageDialog from "./NewPageDialog.jsx";
import ConfigForm from "./ConfigForm.jsx";

beforeEach(() => api.listTemplates.mockResolvedValue([]));
afterEach(cleanup);

const tab = (e = {}) => fireEvent.keyDown(window, { key: "Tab", ...e });

function Harness({ extra }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open</button>
      {open && (
        <Modal title="Demo dialog" onClose={() => setOpen(false)} footer={<button>Save</button>}>
          {extra ?? (
            <>
              <input aria-label="First field" />
              <input aria-label="Second field" />
            </>
          )}
        </Modal>
      )}
    </>
  );
}

describe("Modal", () => {
  it("is a labelled modal dialog", () => {
    render(<Modal title="Settings" onClose={() => {}}><p>x</p></Modal>);
    const d = screen.getByRole("dialog", { name: "Settings" });
    expect(d.getAttribute("aria-modal")).toBe("true");
  });

  it("moves focus to the first field on open and returns it to the opener on Escape", () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    opener.focus();
    fireEvent.click(opener);
    expect(document.activeElement).toBe(screen.getByLabelText("First field"));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("returns focus to the opener even when an autoFocus field took focus while the dialog mounted", () => {
    render(<Harness extra={<input aria-label="Auto" autoFocus />} />);
    const opener = screen.getByRole("button", { name: "Open" });
    opener.focus();
    fireEvent.click(opener);
    expect(document.activeElement).toBe(screen.getByLabelText("Auto"));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.activeElement).toBe(opener);
  });

  it("respects a field that took focus itself (autoFocus)", () => {
    render(<Harness extra={<><input aria-label="A" /><input aria-label="B" autoFocus /></>} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(document.activeElement).toBe(screen.getByLabelText("B"));
  });

  it("Tab and Shift+Tab cycle inside the dialog", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const close = screen.getByRole("button", { name: "Close" });
    const save = screen.getByRole("button", { name: "Save" });
    save.focus();
    tab(); // last -> first (the Close button is first in the dialog)
    expect(document.activeElement).toBe(close);
    tab({ shiftKey: true }); // first -> last
    expect(document.activeElement).toBe(save);
  });

  it("pulls focus back in when it is outside the dialog", () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    fireEvent.click(opener);
    opener.focus();
    tab();
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
  });

  it("closes on a backdrop click but not on a click inside", () => {
    const onClose = vi.fn();
    render(<Modal title="T" onClose={onClose}><p>inside</p></Modal>);
    fireEvent.mouseDown(screen.getByText("inside"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByRole("dialog").parentElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("only the topmost of two stacked dialogs traps Tab", () => {
    render(
      <>
        <Modal title="Under" onClose={() => {}}><input aria-label="under" /></Modal>
        <Modal title="Over" onClose={() => {}}><input aria-label="over" /></Modal>
      </>
    );
    const over = screen.getByRole("dialog", { name: "Over" });
    const last = over.querySelector("input");
    last.focus();
    tab(); // wraps within "Over" (its input is the last focusable: no footer)
    expect(over.contains(document.activeElement)).toBe(true);
  });
});

describe("New page dialog tabs", () => {
  it("arrow keys, Home and End move between the tabs with roving tabindex", () => {
    render(<NewPageDialog integrations={[]} onBlank={() => {}} onCreated={() => {}} onClose={() => {}} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "From template" }).getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "From template" }));
    fireEvent.keyDown(document.activeElement, { key: "End" });
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Import from file" }));
    fireEvent.keyDown(document.activeElement, { key: "ArrowRight" }); // wraps
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Blank page" }));
    fireEvent.keyDown(document.activeElement, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Import from file" }));
    fireEvent.keyDown(document.activeElement, { key: "Home" });
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Blank page" }));
    expect(screen.getByRole("tablist")).toBeTruthy();
    expect(screen.getByRole("tabpanel")).toBeTruthy();
  });
});

describe("secret inputs", () => {
  const field = { key: "api_key", label: "API key", type: "secret", noun: "key" };
  const Form = ({ value = { api_key: "abc" } }) => {
    const [v, setV] = useState(value);
    return <ConfigForm fields={[field]} value={v} onChange={setV} />;
  };

  it("are password fields with autocomplete off, and can be shown or hidden", () => {
    const { container } = render(<Form />);
    const input = container.querySelector("input");
    expect(input.type).toBe("password");
    expect(input.getAttribute("autocomplete")).toBe("new-password");
    fireEvent.click(screen.getByRole("button", { name: "Show API key" }));
    expect(input.type).toBe("text");
    fireEvent.click(screen.getByRole("button", { name: "Hide API key" }));
    expect(input.type).toBe("password");
  });

  it("a multiline secret stays a textarea", () => {
    const { container } = render(<ConfigForm fields={[{ ...field, multiline: true }]} value={{ api_key: "x" }} onChange={() => {}} />);
    expect(container.querySelector("textarea")).not.toBeNull();
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });
});
