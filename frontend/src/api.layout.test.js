import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api.js";

const fetchMock = () => {
  const f = vi.fn(async () => ({ ok: true, status: 200, json: async () => [], headers: { get: () => null } }));
  vi.stubGlobal("fetch", f);
  return f;
};
afterEach(() => vi.unstubAllGlobals());

describe("api.saveLayout", () => {
  it("keeps the desktop call exactly as before: same URL, no query", async () => {
    const f = fetchMock();
    await api.saveLayout("p1", [{ id: "a", x: 1, y: 2, w: 3, h: 4 }]);
    await api.saveLayout("p1", [], "lg");
    expect(f.mock.calls.map((c) => c[0])).toEqual(["/api/dashboards/p1/layout", "/api/dashboards/p1/layout"]);
    expect(f.mock.calls[0][1].method).toBe("PUT");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual([{ id: "a", x: 1, y: 2, w: 3, h: 4 }]);
  });

  it("sends the phone layout with breakpoint=sm", async () => {
    const f = fetchMock();
    await api.saveLayout("p1", [{ id: "a", x: 0, y: 0, w: 2, h: 1, hidden: true }], "sm");
    expect(f.mock.calls[0][0]).toBe("/api/dashboards/p1/layout?breakpoint=sm");
    expect(f.mock.calls[0][1].method).toBe("PUT");
  });

  it("clears the phone layout with a DELETE on the sm breakpoint", async () => {
    const f = fetchMock();
    f.mockResolvedValue({ ok: true, status: 204, headers: { get: () => null } });
    await api.clearPhoneLayout("p1");
    expect(f.mock.calls[0][0]).toBe("/api/dashboards/p1/layout?breakpoint=sm");
    expect(f.mock.calls[0][1].method).toBe("DELETE");
  });
});
