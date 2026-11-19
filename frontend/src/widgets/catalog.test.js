import { describe, it, expect } from "vitest";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { WIDGET_GROUPS, WIDGET_TYPES } from "./index.jsx";

/*
 * The widget registry is the only schema for widget config. The Claude MCP server
 * (mcp/startpage_mcp.py) reads this JSON copy of it. This test keeps the copy in sync:
 * `npm run catalog` rewrites it after a widget or field changes.
 */
// vitest runs from frontend/ (import.meta.url isn't a file: URL under jsdom).
const CATALOG = resolve(process.cwd(), "../mcp/widget_catalog.json");
// The backend reads the same catalog to find the config keys that carry service/integration ids
// (page export/import). The mcp/ folder is not in the Docker image, so it keeps its own copy.
const BACKEND_CATALOG = resolve(process.cwd(), "../backend/app/widget_catalog.json");

// What a config value looks like for field types that aren't plain scalars.
const VALUE_SHAPES = {
  checkbox: "boolean",
  number: "number (null = unset)",
  range: "number",
  color: "hex string such as \"#6c8cff\", or \"\"",
  select: "one of options[].value",
  icon: "emoji, image URL, or a dashboard-icons name (see search_icons)",
  category: "a service category name",
  service: "a service id",
  services: "array of service ids",
  integration: "an integration id whose type matches integrationType (a type or list of types), or provides the capability (stats | items); with multiple: true, an array of such ids",
  statkeys: "text: stat keys of the selected integration (see get_overview / its data), comma separated; one key when multiple is false",
  links: "array of {id, name, url, icon}; id is any unique string",
};

const plain = (field) => {
  const out = {};
  for (const [k, v] of Object.entries(field)) {
    if (typeof v === "function") out.conditional = true; // showIf: only shown for some configs
    else out[k] = v;
  }
  if (VALUE_SHAPES[field.type]) out.value = VALUE_SHAPES[field.type];
  return out;
};

const buildCatalog = () =>
  Object.fromEntries(
    Object.entries(WIDGET_TYPES).map(([type, def]) => [
      type,
      { label: def.label, group: def.group, size: def.size, usesService: !!def.usesService, fields: (def.fields || []).map(plain) },
    ])
  );

describe("widget groups", () => {
  it("every widget is in one of the menu groups, and no group is empty", () => {
    for (const [type, def] of Object.entries(WIDGET_TYPES)) expect(WIDGET_GROUPS, `${type} has group ${def.group}`).toContain(def.group);
    for (const g of WIDGET_GROUPS) expect(Object.values(WIDGET_TYPES).some((d) => d.group === g), g).toBe(true);
  });

  it("every integration type with a widget has one, and every field has a known type", () => {
    const known = new Set(["text", "url", "number", "range", "textarea", "checkbox", "color", "select", "icon", "category", "service", "services", "integration", "links", "secret", "statkeys"]);
    for (const t of ["sonarr", "radarr", "qbittorrent", "sabnzbd", "jellyfin", "plex", "proxmox", "truenas", "portainer", "unraid", "adguard", "unifi", "speedtest_tracker", "home_assistant", "calendar", "countdown", "gauge"]) {
      expect(WIDGET_TYPES[t], t).toBeTruthy();
      for (const f of WIDGET_TYPES[t].fields) expect(known.has(f.type), `${t}.${f.key}: ${f.type}`).toBe(true);
    }
  });
});

describe("widget catalog", () => {
  it("mcp/widget_catalog.json matches the registry", () => {
    const text = JSON.stringify(buildCatalog(), null, 2) + "\n";
    if (process.env.UPDATE_CATALOG) {
      writeFileSync(CATALOG, text);
      writeFileSync(BACKEND_CATALOG, text);
    }
    for (const file of [CATALOG, BACKEND_CATALOG]) {
      let saved = "";
      try {
        saved = readFileSync(file, "utf8").replace(/\r\n/g, "\n");
      } catch {}
      expect(saved, `widget catalog is out of date: run "npm run catalog" in frontend/ (${file})`).toBe(text);
    }
  });
});
