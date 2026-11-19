import { describe, it, expect } from "vitest";
import { settingsPayload, withNumberDefaults } from "./formValues.js";

const FIELDS = [
  { key: "backup_keep", type: "number", default: 14 },
  { key: "limit", type: "number" },
  { key: "title", type: "text", default: "x" },
];

describe("withNumberDefaults", () => {
  it("replaces a cleared number with the field default", () => {
    expect(withNumberDefaults(FIELDS, { backup_keep: null })).toEqual({ backup_keep: 14 });
  });

  it("omits a cleared number that has no default", () => {
    expect(withNumberDefaults(FIELDS, { limit: null, title: "t" })).toEqual({ title: "t" });
  });

  it("leaves set numbers, zero, absent keys and non-number fields alone", () => {
    const v = { backup_keep: 0, title: null };
    expect(withNumberDefaults(FIELDS, v)).toEqual({ backup_keep: 0, title: null });
    expect(withNumberDefaults(FIELDS, {})).toEqual({});
  });

  it("does not mutate its input", () => {
    const v = { backup_keep: null };
    withNumberDefaults(FIELDS, v);
    expect(v).toEqual({ backup_keep: null });
  });
});

describe("settingsPayload", () => {
  it("drops notify_url_set and keeps the mask so the server keeps the stored URL", () => {
    expect(settingsPayload(FIELDS, { notify_url: "********", notify_url_set: true, backup_keep: null })).toEqual({
      notify_url: "********",
      backup_keep: 14,
    });
  });
});
