/* Pure helpers for the per-row actions of the Proxmox and Portainer widgets and the Home Assistant switches. */

/** [{action, label, danger?}] a Proxmox guest offers, from its item status (online = running, offline = stopped). */
/** How long an armed confirm control waits before it disarms itself. */
export const CONFIRM_MS = 6000;

export function proxmoxActions(status) {
  if (status === "online") {
    return [
      { action: "shutdown", label: "Shut down" },
      { action: "reboot", label: "Restart" },
      { action: "stop", label: "Force stop", danger: true },
    ];
  }
  if (status === "offline") return [{ action: "start", label: "Start" }];
  return []; // paused guests cannot be started or stopped the same way
}

/** [{action, label, danger?}] a Portainer container offers (online/degraded = running, offline = stopped). */
export function portainerActions(status) {
  if (status === "online" || status === "degraded") {
    return [
      { action: "restart", label: "Restart" },
      { action: "stop", label: "Stop", danger: true },
    ];
  }
  if (status === "offline") return [{ action: "start", label: "Start" }];
  return [];
}

/** Which sections a stats+list preset shows: "both" | "stats" | "items" (arrays like ["stats"] are accepted). */
export function sectionsOf(show) {
  const list = Array.isArray(show) ? show : show === "stats" || show === "items" ? [show] : ["stats", "items"];
  return { stats: list.includes("stats"), items: list.includes("items") };
}

const ON_STATES = new Set(["on", "open", "opening", "playing", "home"]);
const OFF_STATES = new Set(["off", "closed", "closing", "away", "not_home", "paused", "idle", "standby"]);
const UNAVAILABLE = new Set(["unavailable", "unknown"]);

export const isUnavailable = (state) => UNAVAILABLE.has(String(state ?? "").toLowerCase());
export const isOn = (state) => ON_STATES.has(String(state ?? "").toLowerCase());

/** The state a toggle is expected to produce, for the optimistic update (covers: open <-> closed). */
export function toggledState(entity) {
  const s = String(entity.state ?? "").toLowerCase();
  if (entity.domain === "cover") return isOn(s) ? "closed" : "open";
  if (entity.domain === "media_player") return isOn(s) ? "paused" : "playing";
  return isOn(s) ? "off" : "on";
}

/** The entities to show: `idsText` (comma / space / line separated) picks a subset in that order; empty = all. */
export function pickEntities(entities, idsText) {
  const list = Array.isArray(entities) ? entities : [];
  const ids = String(idsText ?? "").split(/[\s,]+/).map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!ids.length) return list;
  return ids.map((id) => list.find((e) => e.entity_id === id)).filter(Boolean);
}

/** Press-button wording by domain. */
export const PRESS_LABEL = { button: "Press", scene: "Activate", script: "Run" };
