import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { AppContext } from "./context.js";
import { useHistory } from "./history.js";
import { collectLinks, hostOf, looksLikeUrl, normalizeUrl, tidyTitle } from "./search.js";
import Canvas, { SNAP_MODES, nudgeStep } from "./components/Canvas.jsx";
import WidgetEditor from "./components/WidgetEditor.jsx";
import ServiceManager from "./components/ServiceManager.jsx";
import IntegrationManager from "./components/IntegrationManager.jsx";
import DashboardSettings from "./components/DashboardSettings.jsx";
import ImportExport from "./components/ImportExport.jsx";
import SettingsModal from "./components/SettingsModal.jsx";
import CommandPalette from "./components/CommandPalette.jsx";
import Modal from "./components/Modal.jsx";
import { WIDGET_TYPES, defaultConfig } from "./widgets/index.jsx";

const HEALTH_POLL_MS = 10_000;
const RELOAD_AFTER_HIDDEN_MS = 30_000; // refetch everything when a tab comes back after this long
const TOAST_MS = 6000;

/*
 * Last-known state, kept in localStorage so a new tab paints the page immediately
 * instead of waiting for the API. The network response replaces it a moment later.
 * index.html also reads `settings` from here to apply the theme before first paint.
 */
const CACHE_KEY = "sp.cache.v1";
const readCache = () => {
  try {
    return JSON.parse(localStorage.getItem(CACHE_KEY)) || {};
  } catch {
    return {};
  }
};
const boot = readCache();
const bootId = localStorage.getItem("sp.dashboard") || boot.dashboards?.[0]?.id || null;

const SHORTCUTS = [
  ["/", "focus the search bar, or open search"],
  ["Ctrl+K", "open search"],
  ["E", "toggle edit mode"],
  ["Esc", "deselect / leave edit mode / close dialog"],
  ["1–9", "switch to page N"],
  ["Ctrl+Z", "undo"],
  ["Ctrl+Shift+Z / Ctrl+Y", "redo"],
  ["S", "service registry (edit mode)"],
  ["W", "add widget (edit mode)"],
  ["Ctrl+V", "paste a URL to add a link (edit mode)"],
  ["Arrows", "nudge the selected widget (edit mode)"],
  ["Shift+Arrows", "resize the selected widget (edit mode)"],
  ["Enter", "settings of the selected widget"],
  ["Ctrl+D", "duplicate the selected widget"],
  ["Delete", "delete the selected widget"],
  ["?", "this help"],
];

/** The fields needed to recreate a widget exactly (same id) when undoing a delete. */
const widgetSnapshot = (w) => ({ id: w.id, type: w.type, x: w.x, y: w.y, w: w.w, h: w.h, service_id: w.service_id, config: w.config });

export default function App() {
  const [dashboards, setDashboards] = useState(boot.dashboards || []);
  const [currentId, setCurrentId] = useState(bootId);
  const [dashboard, setDashboard] = useState(boot.pages?.[bootId] || null);
  const [services, setServices] = useState(boot.services || []);
  const [health, setHealth] = useState({});
  const [integrations, setIntegrations] = useState(boot.integrations || []);
  const [integrationData, setIntegrationData] = useState({});
  const [settings, setSettings] = useState(boot.settings || null);
  const [editMode, setEditMode] = useState(false);
  const [modal, setModal] = useState(null);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [toast, setToast] = useState(null); // {text, actionLabel?, action?}
  const [renamingId, setRenamingId] = useState(null);
  const [dragTab, setDragTab] = useState(null); // {id, over?, before?}
  const history = useHistory();

  // ----- loading -----
  const loadDashboards = useCallback(async () => {
    const list = await api.listDashboards();
    setDashboards(list);
    if (list.length && !list.some((d) => d.id === currentIdRef.current)) setCurrentId(list[0].id);
    return list;
  }, []);

  const currentIdRef = useRef(currentId);
  currentIdRef.current = currentId;
  const loadDashboard = useCallback(async () => {
    if (!currentId) return;
    const d = await api.getDashboard(currentId);
    // Ignore a late response for a page the user already switched away from.
    if (currentIdRef.current === d.id) setDashboard(d);
  }, [currentId]);

  const loadServices = useCallback(async () => {
    const [s, h] = await Promise.all([api.listServices(), api.health()]);
    setServices(s);
    setHealth(h);
  }, []);

  const loadIntegrations = useCallback(async () => {
    const [i, d] = await Promise.all([api.listIntegrations(), api.integrationData()]);
    setIntegrations(i);
    setIntegrationData(d);
  }, []);

  const loadSettings = useCallback(async () => setSettings(await api.getSettings()), []);

  const reloadAll = useCallback(async () => {
    await Promise.all([loadDashboards(), loadServices(), loadIntegrations(), loadSettings(), loadDashboard()]);
  }, [loadDashboards, loadServices, loadIntegrations, loadSettings, loadDashboard]);
  const reloadRef = useRef(reloadAll);
  reloadRef.current = reloadAll;
  // History entries outlive renders, so they refresh through a ref (always the current page).
  const refreshPagesRef = useRef();
  refreshPagesRef.current = () => Promise.all([loadDashboards(), loadDashboard()]);
  const refreshPages = () => refreshPagesRef.current();

  const offlineMessage = (e) =>
    boot.settings && e instanceof TypeError ? "Can't reach the Start Page server. Showing the last saved copy." : e.message;

  useEffect(() => {
    Promise.all([loadDashboards(), loadServices(), loadIntegrations(), loadSettings()]).catch((e) => setError(offlineMessage(e)));
  }, []); // eslint-disable-line

  useEffect(() => {
    if (!currentId) return;
    localStorage.setItem("sp.dashboard", currentId);
    setSelectedId(null);
    const cached = readCache().pages?.[currentId];
    setDashboard((cur) => (cur?.id === currentId ? cur : cached || cur));
    loadDashboard().catch((e) => setError(offlineMessage(e)));
  }, [currentId, loadDashboard]); // eslint-disable-line

  useEffect(() => {
    if (!editMode) {
      setSelectedId(null);
      setRenamingId(null);
    }
  }, [editMode]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]);

  // Poll only while the tab is visible, and only for data the page actually has.
  const counts = useRef({});
  counts.current = { services: services.length, integrations: integrations.length };
  useEffect(() => {
    let hiddenAt = Date.now();
    const poll = () => {
      if (document.hidden) return;
      if (counts.current.services) api.health().then(setHealth).catch(() => {});
      if (counts.current.integrations) api.integrationData().then(setIntegrationData).catch(() => {});
    };
    const onVisibility = () => {
      if (document.hidden) hiddenAt = Date.now();
      else if (Date.now() - hiddenAt > RELOAD_AFTER_HIDDEN_MS) reloadRef.current().then(() => setError("")).catch(() => {});
      else poll();
    };
    const t = setInterval(poll, HEALTH_POLL_MS);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // ----- keep the local cache fresh -----
  useEffect(() => {
    if (!settings || !dashboards.length) return;
    const pages = { ...(readCache().pages || {}) };
    if (dashboard) pages[dashboard.id] = dashboard;
    for (const id of Object.keys(pages)) if (!dashboards.some((d) => d.id === id)) delete pages[id];
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ dashboards, services, integrations, settings, pages }));
    } catch {}
  }, [dashboards, dashboard, services, integrations, settings]);

  // ----- apply settings to the document -----
  useEffect(() => {
    if (!settings) return;
    const root = document.documentElement;
    root.dataset.theme = settings.theme || "system";
    root.style.setProperty("--accent", settings.accent || "#6c8cff");
    root.style.setProperty("--widget-opacity", String((settings.widget_opacity ?? 100) / 100));
    root.style.setProperty("--font", settings.font || 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif');
    if (settings.background_color) root.style.setProperty("--bg", settings.background_color);
    else root.style.removeProperty("--bg");
    document.title = settings.title || "Start Page";
  }, [settings]);

  const pageBg = dashboard?.settings?.background_image || settings?.background_image || "";
  const bgDim = dashboard?.settings?.background_dim ?? settings?.background_dim ?? 40;
  const bgBlur = dashboard?.settings?.background_blur ?? settings?.background_blur ?? 0;

  // ----- mutations -----
  const run = async (fn) => {
    try {
      setError("");
      await fn();
    } catch (e) {
      setError(e.message);
    }
  };

  const doUndo = () => run(async () => { const e = await history.undo(); if (e) setToast({ text: `Undid: ${e.label}`, actionLabel: "Redo", action: doRedo }); });
  const doRedo = () => run(async () => { const e = await history.redo(); if (e) setToast({ text: `Redid: ${e.label}`, actionLabel: "Undo", action: doUndo }); });

  /** Run a history step on the page it belongs to, so undo never changes a page you can't see. */
  const onPage = async (dashId, fn) => {
    await fn();
    if (dashId && currentIdRef.current !== dashId) setCurrentId(dashId);
    await refreshPages();
  };

  const applyLayout = (items) =>
    setDashboard((d) =>
      d && {
        ...d,
        widgets: d.widgets.map((w) => {
          const u = items.find((i) => i.id === w.id);
          return u ? { ...w, ...u } : w;
        }),
      }
    );

  // Layout saves go out one at a time, so a burst of nudges can't land out of order.
  const layoutQueue = useRef(Promise.resolve());
  const onLayoutChange = (items, { label = "Move widget", mergeKey } = {}) =>
    run(async () => {
      const dashId = currentId;
      const before = items
        .map((i) => dashboard.widgets.find((w) => w.id === i.id))
        .filter(Boolean)
        .map(({ id, x, y, w, h }) => ({ id, x, y, w, h }));
      applyLayout(items);
      const save = layoutQueue.current.catch(() => {}).then(() => api.saveLayout(dashId, items));
      layoutQueue.current = save;
      await save;
      history.push({
        label,
        mergeKey,
        undo: () => onPage(dashId, () => api.saveLayout(dashId, before)),
        redo: () => onPage(dashId, () => api.saveLayout(dashId, items)),
      });
    });

  /** Arrow-key nudge (dx/dy) or resize (dw/dh) of the selected widget, in steps of the snap size. */
  const nudge = (dx, dy, dw, dh) => {
    const w = dashboard?.widgets.find((x) => x.id === selectedId);
    if (!w) return;
    const step = nudgeStep(dashboard);
    const cols = dashboard.columns || 24;
    const round = (v) => Math.round(v * 1000) / 1000;
    const nw = round(Math.max(step, Math.min(cols, w.w + dw * step)));
    const nh = round(Math.max(step, w.h + dh * step));
    const nx = round(Math.max(0, Math.min(cols - nw, w.x + dx * step)));
    const ny = round(Math.max(0, w.y + dy * step));
    if (nx === w.x && ny === w.y && nw === w.w && nh === w.h) return;
    onLayoutChange([{ id: w.id, x: nx, y: ny, w: nw, h: nh }], { label: dw || dh ? "Resize widget" : "Move widget", mergeKey: `nudge:${w.id}:${dw || dh ? "size" : "pos"}` });
  };

  /** Save a widget's config in place (used by widgets that edit themselves, e.g. Notes). Not part of undo. */
  const updateWidgetConfig = useCallback(async (id, config, { keepalive = false } = {}) => {
    setDashboard((d) => d && { ...d, widgets: d.widgets.map((w) => (w.id === id ? { ...w, config } : w)) });
    try {
      // keepalive lets the save finish while the tab closes; browsers cap such bodies at 64 KB.
      await api.updateWidget(id, { config }, { keepalive: keepalive && JSON.stringify(config).length < 60_000 });
    } catch (e) {
      setError(`Couldn't save: ${e.message}`);
    }
  }, []);

  const findFreeSpot = (w, h, taken = dashboard?.widgets || [], cols = dashboard?.columns || 24) => {
    const hits = (x, y) => taken.some((t) => x < t.x + t.w && x + w > t.x && y < t.y + t.h && y + h > t.y);
    for (let y = 0; y < 200; y++) for (let x = 0; x + w <= cols; x++) if (!hits(x, y)) return { x, y };
    return { x: 0, y: 0 };
  };

  const addWidget = (type, config = {}) => {
    const def = WIDGET_TYPES[type];
    setModal({ kind: "widget", widget: { type, service_id: null, config: { ...defaultConfig(type), ...config }, ...def.size } });
  };

  const trackCreated = (label, dashId, created) => {
    const snap = widgetSnapshot(created);
    history.push({
      label,
      undo: () => onPage(dashId, () => api.deleteWidget(snap.id)),
      redo: () => onPage(dashId, () => api.createWidget(dashId, snap)),
    });
    setSelectedId(snap.id);
  };

  const saveWidget = (fields) =>
    run(async () => {
      const w = modal.widget;
      const dashId = currentId;
      if (w.id) {
        const before = { type: w.type, service_id: w.service_id, config: w.config };
        await api.updateWidget(w.id, fields);
        history.push({
          label: "Edit widget",
          undo: () => onPage(dashId, () => api.updateWidget(w.id, before)),
          redo: () => onPage(dashId, () => api.updateWidget(w.id, fields)),
        });
      } else {
        const size = WIDGET_TYPES[fields.type].size;
        const created = await api.createWidget(dashId, { ...fields, ...size, ...findFreeSpot(size.w, size.h) });
        trackCreated("Add widget", dashId, created);
      }
      setModal(null);
      await loadDashboard();
    });

  const deleteWidget = (widget) =>
    run(async () => {
      const dashId = currentId;
      const snap = widgetSnapshot(widget);
      await api.deleteWidget(widget.id);
      history.push({
        label: "Delete widget",
        undo: () => onPage(dashId, () => api.createWidget(dashId, snap)),
        redo: () => onPage(dashId, () => api.deleteWidget(snap.id)),
      });
      setModal(null);
      setSelectedId(null);
      setToast({ text: `${WIDGET_TYPES[widget.type]?.label || "Widget"} deleted`, actionLabel: "Undo", action: doUndo });
      await loadDashboard();
    });

  const duplicateWidget = (widget) =>
    run(async () => {
      const dashId = currentId;
      const clone = await api.duplicateWidget(widget.id);
      trackCreated("Duplicate widget", dashId, clone);
      setModal(null);
      await loadDashboard();
    });

  const moveWidget = (widget, targetId, copy) =>
    run(async () => {
      const fromId = currentId;
      const target = await api.getDashboard(targetId);
      const spot = findFreeSpot(widget.w, widget.h, target.widgets, target.columns || 24);
      if (copy) {
        const { id, ...fields } = widgetSnapshot(widget);
        const created = await api.createWidget(targetId, { ...fields, ...spot });
        const snap = widgetSnapshot(created);
        history.push({
          label: `Copy widget to ${target.name}`,
          undo: () => onPage(null, () => api.deleteWidget(snap.id)),
          redo: () => onPage(null, () => api.createWidget(targetId, snap)),
        });
      } else {
        const before = { dashboard_id: fromId, x: widget.x, y: widget.y };
        const after = { dashboard_id: targetId, ...spot };
        await api.updateWidget(widget.id, after);
        history.push({
          label: `Move widget to ${target.name}`,
          undo: () => onPage(fromId, () => api.updateWidget(widget.id, before)),
          redo: () => onPage(targetId, () => api.updateWidget(widget.id, after)),
        });
        setSelectedId(null);
      }
      setModal(null);
      setToast({ text: `${copy ? "Copied" : "Moved"} to ${target.name}`, actionLabel: "Go to page", action: () => setCurrentId(targetId) });
      await loadDashboard();
    });

  const addDashboard = () =>
    run(async () => {
      const name = prompt("Dashboard name", "New page");
      if (!name) return;
      const d = await api.createDashboard({ name });
      history.push({
        label: "Add page",
        undo: () => onPage(null, () => api.deleteDashboard(d.id)),
        redo: async () => { await api.createDashboard({ id: d.id, name: d.name, position: d.position }); await refreshPages(); setCurrentId(d.id); },
      });
      await loadDashboards();
      setCurrentId(d.id);
    });

  const saveDashboard = (data) =>
    run(async () => {
      const dashId = currentId;
      const before = Object.fromEntries(Object.keys(data).map((k) => [k, dashboard[k]]));
      await api.updateDashboard(dashId, data);
      history.push({
        label: data.name && data.name !== before.name ? "Rename page" : "Page settings",
        undo: () => onPage(dashId, () => api.updateDashboard(dashId, before)),
        redo: () => onPage(dashId, () => api.updateDashboard(dashId, data)),
      });
      setModal(null);
      await refreshPages();
    });

  const renameDashboard = (d, name) => {
    name = name.trim();
    if (!name || name === d.name) return;
    run(async () => {
      setDashboards((list) => list.map((x) => (x.id === d.id ? { ...x, name } : x)));
      await api.updateDashboard(d.id, { name });
      history.push({
        label: "Rename page",
        undo: () => onPage(null, () => api.updateDashboard(d.id, { name: d.name })),
        redo: () => onPage(null, () => api.updateDashboard(d.id, { name })),
      });
      await refreshPages();
    });
  };

  const applyOrder = async (ids) => {
    await Promise.all(ids.map((id, position) => api.updateDashboard(id, { position })));
  };

  const reorderDashboards = (ids) =>
    run(async () => {
      const before = dashboards.map((d) => d.id);
      if (before.join() === ids.join()) return;
      setDashboards((list) => ids.map((id) => list.find((d) => d.id === id)));
      await applyOrder(ids);
      history.push({
        label: "Reorder pages",
        undo: () => onPage(null, () => applyOrder(before)),
        redo: () => onPage(null, () => applyOrder(ids)),
      });
      await loadDashboards();
    });

  const dropTab = () => {
    const t = dragTab;
    setDragTab(null);
    if (!t?.over || t.over === t.id) return;
    const ids = dashboards.map((d) => d.id).filter((id) => id !== t.id);
    ids.splice(ids.indexOf(t.over) + (t.before ? 0 : 1), 0, t.id);
    reorderDashboards(ids);
  };

  const deleteDashboard = () =>
    run(async () => {
      const detail = dashboard; // includes its widgets, so the whole page can be restored
      const fallbackId = dashboards[Math.max(0, dashboards.findIndex((d) => d.id === detail.id) - 1)]?.id ?? null;
      await api.deleteDashboard(detail.id);
      const restore = async () => {
        await api.importConfig({ services: [], integrations: [], settings: {}, dashboards: [detail] }, false);
        await refreshPages();
        setCurrentId(detail.id);
      };
      history.push({
        label: `Delete page "${detail.name}"`,
        undo: restore,
        redo: async () => {
          await api.deleteDashboard(detail.id);
          const list = await api.listDashboards();
          setDashboards(list);
          setCurrentId(list.some((d) => d.id === fallbackId) ? fallbackId : list[0]?.id ?? null);
        },
      });
      setModal(null);
      const list = await api.listDashboards();
      setDashboards(list);
      setCurrentId(list.some((d) => d.id === fallbackId) ? fallbackId : list[0]?.id ?? null);
      setToast({ text: `Page "${detail.name}" deleted`, actionLabel: "Undo", action: doUndo });
    });

  const saveSettings = (data) => run(async () => { setSettings(await api.updateSettings(data)); setModal(null); });

  // ----- keyboard shortcuts (re-registered each render so handlers see current state) -----
  useEffect(() => {
    const onKey = (e) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.isContentEditable;
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (mod && key === "k") {
        e.preventDefault();
        setModal((m) => (m?.kind === "palette" ? null : { kind: "palette" }));
        return;
      }
      if (modal || typing) return;
      if (mod && (key === "z" || key === "y")) {
        e.preventDefault();
        if (key === "y" || e.shiftKey) doRedo();
        else doUndo();
        return;
      }
      const selected = editMode && dashboard?.widgets.find((w) => w.id === selectedId);
      if (selected) {
        const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        if (arrows[e.key]) {
          e.preventDefault();
          const [a, b] = arrows[e.key];
          if (e.shiftKey) nudge(0, 0, a, b);
          else nudge(a, b, 0, 0);
          return;
        }
        if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteWidget(selected); return; }
        if (e.key === "Enter") { e.preventDefault(); setModal({ kind: "widget", widget: selected }); return; }
        if (mod && key === "d") { e.preventDefault(); duplicateWidget(selected); return; }
        if (e.key === "Escape") { setSelectedId(null); return; }
      }
      if (mod || e.altKey) return;
      if (e.key === "/") {
        e.preventDefault();
        const searchInput = !editMode && document.querySelector(".search-widget input");
        if (searchInput) searchInput.focus();
        else setModal({ kind: "palette" });
      }
      else if (e.key === "?") setModal({ kind: "help" });
      else if (key === "e") setEditMode((m) => !m);
      else if (e.key === "Escape") setEditMode(false);
      else if (/^[1-9]$/.test(e.key)) { const d = dashboards[Number(e.key) - 1]; if (d) setCurrentId(d.id); }
      else if (editMode && key === "s") setModal({ kind: "services" });
      else if (editMode && key === "w") addWidget("link");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ----- paste a URL in edit mode to add a Link widget -----
  useEffect(() => {
    const onPaste = async (e) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.isContentEditable;
      if (!editMode || modal || typing || !dashboard) return;
      const text = (e.clipboardData?.getData("text") || "").trim();
      if (!looksLikeUrl(text)) return;
      e.preventDefault();
      const url = normalizeUrl(text);
      const meta = await Promise.race([api.siteMeta(url).catch(() => null), new Promise((r) => setTimeout(() => r(null), 2500))]);
      addWidget("link", { url, name: tidyTitle(meta?.title) || hostOf(url) });
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [editMode, modal, dashboard]); // eslint-disable-line

  const links = useMemo(() => collectLinks(dashboard?.widgets), [dashboard]);

  const ctx = useMemo(
    () => ({ settings: settings || {}, services, dashboards, links, health, editMode, goToPage: setCurrentId, updateWidgetConfig }),
    [settings, services, dashboards, links, health, editMode, updateWidgetConfig]
  );

  const renameDone = useRef(false);
  const startRename = (id) => {
    renameDone.current = false;
    setRenamingId(id);
  };
  const finishRename = (d, value) => {
    if (renameDone.current) return;
    renameDone.current = true;
    setRenamingId(null);
    if (value !== null) renameDashboard(d, value);
  };

  // ----- render -----
  return (
    <AppContext.Provider value={ctx}>
    <div className={`app ${editMode ? "app-edit" : ""}`}>
      {pageBg && (
        <div
          className="page-bg"
          style={{ backgroundImage: `url("${pageBg}")`, filter: bgBlur ? `blur(${bgBlur}px)` : undefined }}
        />
      )}
      {pageBg && <div className="page-bg-dim" style={{ opacity: bgDim / 100 }} />}

      <header className="topbar">
        <nav className="tabs">
          {dashboards.map((d, i) =>
            renamingId === d.id ? (
              <input
                key={d.id}
                className="tab tab-rename"
                autoFocus
                defaultValue={d.name}
                size={Math.max(6, d.name.length + 2)}
                onFocus={(e) => e.target.select()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") finishRename(d, e.currentTarget.value);
                  else if (e.key === "Escape") { e.stopPropagation(); finishRename(d, null); }
                }}
                onBlur={(e) => finishRename(d, e.currentTarget.value)}
              />
            ) : (
              <button
                key={d.id}
                className={[
                  "tab",
                  d.id === currentId ? "active" : "",
                  dragTab?.id === d.id ? "tab-dragging" : "",
                  dragTab?.over === d.id && dragTab.id !== d.id ? (dragTab.before ? "tab-drop-before" : "tab-drop-after") : "",
                ].join(" ")}
                onClick={() => setCurrentId(d.id)}
                onDoubleClick={() => editMode && startRename(d.id)}
                title={editMode ? "Drag to reorder · double-click to rename" : `Page ${i + 1}`}
                draggable={editMode}
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", d.id);
                  setDragTab({ id: d.id });
                }}
                onDragOver={(e) => {
                  if (!dragTab) return;
                  e.preventDefault();
                  const r = e.currentTarget.getBoundingClientRect();
                  const before = e.clientX < r.left + r.width / 2;
                  setDragTab((t) => (t && (t.over !== d.id || t.before !== before) ? { ...t, over: d.id, before } : t));
                }}
                onDrop={(e) => { e.preventDefault(); dropTab(); }}
                onDragEnd={() => setDragTab(null)}
              >
                {d.name}
              </button>
            )
          )}
          {editMode && <button className="tab tab-add" onClick={addDashboard} title="New dashboard">+</button>}
        </nav>
        <div className="spacer" />
        <div className="toolbar">
          <button className="btn-search" onClick={() => setModal({ kind: "palette" })} title="Search (Ctrl+K)">🔍 <span className="muted">Search…</span> <kbd>/</kbd></button>
          {editMode && (
            <>
              <div className="btn-group">
                <button onClick={doUndo} disabled={!history.canUndo} title={history.canUndo ? `Undo: ${history.undoLabel} (Ctrl+Z)` : "Nothing to undo"}>↶</button>
                <button onClick={doRedo} disabled={!history.canRedo} title={history.canRedo ? `Redo: ${history.redoLabel} (Ctrl+Shift+Z)` : "Nothing to redo"}>↷</button>
              </div>
              <div className="dropdown">
                <button className="btn-primary">+ Widget ▾</button>
                <div className="dropdown-menu">
                  {Object.entries(WIDGET_TYPES).map(([k, d]) => (
                    <button key={k} onClick={() => addWidget(k)}>{d.label}</button>
                  ))}
                </div>
              </div>
              {dashboard && (
                <select
                  className="snap-select"
                  title="Snap while dragging"
                  value={dashboard.settings?.snap || "cell"}
                  onChange={(e) => saveDashboard({ settings: { ...(dashboard.settings || {}), snap: e.target.value } })}
                >
                  {SNAP_MODES.map((m) => <option key={m.value} value={m.value}>Snap: {m.label}</option>)}
                </select>
              )}
              <button onClick={() => setModal({ kind: "services" })}>Services</button>
              <button onClick={() => setModal({ kind: "integrations" })}>Integrations</button>
              <div className="dropdown">
                <button>More ▾</button>
                <div className="dropdown-menu">
                  <button onClick={() => dashboard && setModal({ kind: "dashboard" })}>Page settings</button>
                  <button onClick={() => settings && setModal({ kind: "settings" })}>Settings &amp; theme</button>
                  <button onClick={() => setModal({ kind: "io" })}>Import / export</button>
                  <button onClick={() => setModal({ kind: "help" })}>Keyboard shortcuts</button>
                </div>
              </div>
            </>
          )}
          <button className={editMode ? "btn-primary" : ""} onClick={() => setEditMode((m) => !m)} title="Toggle edit mode (E)">
            {editMode ? "Done" : "Edit"}
          </button>
        </div>
      </header>

      {error && <div className="banner error" onClick={() => setError("")}>{error}</div>}

      {dashboard ? (
        <Canvas
          dashboard={dashboard}
          widgets={dashboard.widgets}
          services={services}
          health={health}
          integrations={integrations}
          integrationData={integrationData}
          onRefresh={loadIntegrations}
          editMode={editMode}
          onLayoutChange={(items) => onLayoutChange(items, { label: "Move widget" })}
          onEditWidget={(w) => setModal({ kind: "widget", widget: w })}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      ) : (
        <div className="canvas-empty"><p className="muted">{dashboards.length ? "Loading…" : "No dashboards. Enter edit mode and press + to create one."}</p></div>
      )}

      {toast && (
        <div className="toast" role="status">
          <span>{toast.text}</span>
          {toast.action && (
            <button className="btn-sm" onClick={() => { setToast(null); toast.action(); }}>{toast.actionLabel}</button>
          )}
          <button className="btn-icon toast-close" onClick={() => setToast(null)} title="Dismiss">✕</button>
        </div>
      )}

      {modal?.kind === "widget" && (
        <WidgetEditor
          widget={modal.widget}
          services={services}
          integrations={integrations}
          dashboards={dashboards}
          onSave={saveWidget}
          onDelete={() => deleteWidget(modal.widget)}
          onDuplicate={() => duplicateWidget(modal.widget)}
          onMoveTo={(targetId, copy) => moveWidget(modal.widget, targetId, copy)}
          onClose={() => setModal(null)}
          onManageServices={() => setModal({ kind: "services", back: modal })}
          onManageIntegrations={() => setModal({ kind: "integrations", back: modal })}
        />
      )}
      {modal?.kind === "services" && (
        <ServiceManager services={services} health={health} onChanged={loadServices} onClose={() => setModal(modal.back ?? null)} />
      )}
      {modal?.kind === "integrations" && (
        <IntegrationManager integrations={integrations} integrationData={integrationData} onChanged={loadIntegrations} onClose={() => setModal(modal.back ?? null)} />
      )}
      {modal?.kind === "dashboard" && dashboard && (
        <DashboardSettings dashboard={dashboard} canDelete={dashboards.length > 1} onSave={saveDashboard} onDelete={deleteDashboard} onClose={() => setModal(null)} />
      )}
      {modal?.kind === "settings" && settings && (
        <SettingsModal settings={settings} onSave={saveSettings} onRestored={reloadAll} onClose={() => setModal(null)} />
      )}
      {modal?.kind === "io" && <ImportExport onImported={reloadAll} onClose={() => setModal(null)} />}
      {modal?.kind === "palette" && (
        <CommandPalette
          services={services}
          dashboards={dashboards}
          links={links}
          health={health}
          settings={settings || {}}
          onGoToPage={setCurrentId}
          onClose={() => setModal(null)}
        />
      )}
      {modal?.kind === "help" && (
        <Modal title="Keyboard shortcuts" onClose={() => setModal(null)}>
          <table className="svc-table">
            <tbody>
              {SHORTCUTS.map(([k, d]) => (
                <tr key={k}><td className="nowrap"><kbd>{k}</kbd></td><td className="muted">{d}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="help">In edit mode: click a widget to select it, drag it by its body, resize from the edges/corner, ⚙ or double-click to configure. Drag page tabs to reorder them; double-click a tab to rename it.</p>
          <p className="help">While dragging or resizing, widgets snap first to grid lines, then to the edges and centres of other widgets (and the page edges/centre) — dashed guide lines show the match. Divider lines sit exactly on grid lines and join end-to-end. Hold <kbd>Alt</kbd> to move freely.</p>
        </Modal>
      )}
    </div>
    </AppContext.Provider>
  );
}
