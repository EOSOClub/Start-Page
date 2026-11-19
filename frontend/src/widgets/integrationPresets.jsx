import { useApp } from "../context.js";
import { BlockingBar, ErrorLine, PauseBar, RowActions, useAction } from "./actionControls.jsx";
import { portainerActions, proxmoxActions, sectionsOf } from "./actionHelpers.js";
import { Header, Problem, useIntegration } from "./integrations.jsx";
import { ItemRows, STATS_FIELDS, StatStrip, StatTiles } from "./integrationViews.jsx";
import { itemIdentity, pickStats } from "./integrationViz.js";

/*
 * Thin presets of the generic stats / list widgets for one integration type each. `integrationPreset`
 * builds a registry entry: the picker limited to the type, a title, the stat keys and list size that
 * suit it, and optional controls (a bar under the content, or a menu on every row).
 */

const SHOW_OPTIONS = [
  { value: "both", label: "Stats and list" },
  { value: "stats", label: "Stats only" },
  { value: "items", label: "List only" },
];

/**
 * @param type      integration type key (also the widget type key)
 * @param label     menu / editor name
 * @param size      default {w, h}
 * @param show      sections shown by default: ["stats", "items"] | ["stats"] | ["items"]; a type that
 *                  provides both gets a "Show" select so the user can switch
 * @param statKeys  default `stat_keys` ("" = all)
 * @param limit     default list rows
 * @param empty     text for an empty list ("Nothing playing")
 * @param controls  ({integ, data, onRefresh}) => node, rendered under the content
 * @param rowActions {field: {key, label, help}, row({item, integ, act}) => node, hint}: a per-row menu, off unless the
 *                  checkbox `field` is ticked (and the viewer can write). Rows are addressed by the item's `ref`
 *                  (set by the backend); `hint` is the one line shown when the box is ticked but no listed row has one
 */
export function integrationPreset({ type, label, size, show = ["stats", "items"], statKeys = "", limit = 8, empty = "Nothing to show.", controls, rowActions, onlyStats = false, extraFields = [] }) {
  const sections = sectionsOf(show);
  const both = !onlyStats && sections.stats && sections.items;
  const statsOnly = onlyStats || (sections.stats && !sections.items);
  const defaultShow = both ? "both" : statsOnly ? "stats" : "items";
  const fields = [
    { key: "integration_id", label: `${label} integration`, type: "integration", integrationType: type },
    { key: "title", label: "Title", type: "text", placeholder: "(integration name)" },
    ...(onlyStats ? [] : [{ key: "show", label: "Show", type: "select", options: SHOW_OPTIONS, default: defaultShow }]),
    { ...STATS_FIELDS[1], ...(statKeys ? { default: statKeys } : {}) },
    { ...STATS_FIELDS[2], label: onlyStats ? "Layout" : "Stats layout (when shown without the list)" },
    STATS_FIELDS[3],
    ...(onlyStats ? [] : [{ key: "limit", label: "Max list rows", type: "number", default: limit, min: 1 }, { key: "newTab", label: "Open links in new tab", type: "checkbox" }]),
    ...(rowActions ? [rowActions.field] : []),
    ...extraFields,
  ];

  function Render({ widget, integrations, integrationData, onRefresh }) {
    const { integ, data } = useIntegration(widget, integrations, integrationData, type);
    const { canWrite = true } = useApp();
    const act = useAction(integ?.id, onRefresh);
    const c = widget.config;
    if (!integ || !data || (!data.ok && !data.data)) return <Problem integ={integ} data={data} type={label} />;
    const d = data.data;
    const mode = onlyStats ? "stats" : c.show ?? defaultShow;
    const wantStats = mode === "both" || mode === "stats";
    const wantItems = !onlyStats && (mode === "both" || mode === "items");
    const stats = wantStats && Array.isArray(d.stats) ? pickStats(d.stats, c.stat_keys ?? statKeys) : [];
    const items = wantItems && Array.isArray(d.items) ? d.items.slice(0, Math.max(1, Number(c.limit) || limit)) : [];
    const actionsOn = !!rowActions && !!c.show_actions && canWrite;
    // Ticked, rows listed, but none can be acted on (e.g. the integration's list shows nodes, environments or stacks).
    const allItems = Array.isArray(d.items) ? d.items : [];
    const notice = actionsOn && wantItems && allItems.length > 0 && !allItems.some((i) => i?.ref) ? rowActions.hint : null;
    return (
      <div className="group">
        <Header title={c.title || integ.name} right={!data.ok ? <span className="muted">stale</span> : null} />
        {wantStats && (stats.length === 0 ? <div className="muted">No matching stats.</div> : wantItems ? <StatStrip stats={stats} sparkline={c.show_sparkline !== false} /> : <StatTiles stats={stats} layout={c.layout} sparkline={c.show_sparkline !== false} />)}
        {wantItems && (
          <ItemRows
            items={items}
            newTab={c.newTab}
            empty={empty}
            rowAction={actionsOn && !notice ? (item) => (item.ref ? rowActions.row({ item, integ, act, c }) : null) : undefined}
          />
        )}
        {notice && <div className="muted">{notice}</div>}
        {rowActions && <ErrorLine error={act.error} />}
        {controls?.({ integ, data: d, onRefresh })}
      </div>
    );
  }
  return { label, group: "Integrations", size, fields, Render };
}

// ---------- the presets ----------
export const PRESETS = {
  sonarr: integrationPreset({ type: "sonarr", label: "Sonarr", size: { w: 8, h: 6 }, empty: "No episodes in this list." }),
  radarr: integrationPreset({ type: "radarr", label: "Radarr", size: { w: 8, h: 6 }, empty: "No movies in this list." }),
  qbittorrent: integrationPreset({
    type: "qbittorrent", label: "qBittorrent", size: { w: 8, h: 6 }, statKeys: "download, upload, downloading, seeding", empty: "No active torrents.",
    controls: ({ integ, onRefresh }) => <PauseBar integId={integ.id} onRefresh={onRefresh} pauseAction="pause_all" resumeAction="resume_all" pauseLabel="Pause all" resumeLabel="Resume all" />,
  }),
  sabnzbd: integrationPreset({
    type: "sabnzbd", label: "SABnzbd", size: { w: 8, h: 6 }, statKeys: "speed, left, timeleft, queue", empty: "The queue is empty.",
    controls: ({ integ, data, onRefresh }) => {
      const paused = data.stats?.find((s) => s.key === "paused")?.value;
      return <PauseBar integId={integ.id} onRefresh={onRefresh} pauseAction="pause" resumeAction="resume" pauseLabel="Pause" resumeLabel="Resume" paused={typeof paused === "boolean" ? paused : undefined} />;
    },
  }),
  jellyfin: integrationPreset({ type: "jellyfin", label: "Jellyfin", size: { w: 8, h: 5 }, statKeys: "streams, transcodes", empty: "Nothing playing." }),
  plex: integrationPreset({ type: "plex", label: "Plex", size: { w: 8, h: 5 }, statKeys: "streams, transcodes", empty: "Nothing playing." }),
  proxmox: integrationPreset({
    type: "proxmox", label: "Proxmox", size: { w: 8, h: 7 }, limit: 12, empty: "No guests.",
    rowActions: {
      field: { key: "show_actions", label: "Show start / shut down / restart menus on guests", type: "checkbox", help: "Only works while the integration's list shows VMs and containers. Every action asks for confirmation. Not shown to view-only guests." },
      hint: 'Row actions need the integration\'s list set to "VMs and containers".',
      row: ({ item, act }) => {
        const { node, vmid, kind } = item.ref;
        return <RowActions name={item.title} identity={itemIdentity(item)} options={proxmoxActions(item.status)} busy={act.busy} onRun={(action) => act.run("guest_power", { node, vmid, kind, action })} />;
      },
    },
  }),
  truenas: integrationPreset({ type: "truenas", label: "TrueNAS", size: { w: 8, h: 6 }, empty: "No pools or alerts." }),
  portainer: integrationPreset({
    type: "portainer", label: "Portainer", size: { w: 8, h: 6 }, statKeys: "endpoints, running, stopped, unhealthy", limit: 12, empty: "No containers.",
    rowActions: {
      field: { key: "show_actions", label: "Show start / stop / restart menus on containers", type: "checkbox", help: "Only works while the integration's list shows containers (it uses the integration's Environment ID, or the first online environment). Every action asks for confirmation." },
      hint: 'Row actions need the integration\'s list set to "Containers".',
      row: ({ item, act }) => {
        const { endpoint_id, container_id } = item.ref;
        return <RowActions name={item.title} identity={itemIdentity(item)} options={portainerActions(item.status)} busy={act.busy} onRun={(action) => act.run("container", { endpoint_id, container_id, action })} />;
      },
    },
  }),
  unraid: integrationPreset({ type: "unraid", label: "Unraid", size: { w: 8, h: 6 }, statKeys: "array, array_used, parity, disk_errors, cpu, memory", empty: "Nothing to list." }),
  adguard: integrationPreset({
    type: "adguard", label: "AdGuard Home", size: { w: 6, h: 4 }, show: ["stats"], statKeys: "queries, blocked, percent_blocked, avg_time", empty: "No blocked domains yet.",
    controls: ({ integ, data, onRefresh }) => <BlockingBar integId={integ.id} blocking={data.blocking} onRefresh={onRefresh} action="set_protection" noun="protection" />,
  }),
  unifi: integrationPreset({ type: "unifi", label: "UniFi", size: { w: 6, h: 4 }, show: ["stats"], statKeys: "wan_status, clients, devices_offline, wan_down, wan_up, latency", empty: "Nothing to list." }),
  speedtest_tracker: integrationPreset({ type: "speedtest_tracker", label: "Speedtest Tracker", size: { w: 8, h: 3 }, show: ["stats"], onlyStats: true, statKeys: "download, upload, ping" }),
};
