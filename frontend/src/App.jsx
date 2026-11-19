import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { api } from "./api.js";
import { AppContext } from "./context.js";
import { useHistory } from "./history.js";
import { collectLinks, hostOf, looksLikeUrl, normalizeUrl, tidyTitle } from "./search.js";
import { NARROW_PX, derivePhoneLayout, fitToGrid, phoneOptions, phoneSpotOn, seedPhoneLayout } from "./phone.js";
import { UPDATE_EVENT } from "./sw/register.js";
import { cssUrl } from "./safeUrl.js";
import Canvas, { SNAP_MODES, gridOptions, nudgeStep } from "./components/Canvas.jsx";
import WidgetEditor from "./components/WidgetEditor.jsx";
import WidgetMenu from "./components/WidgetMenu.jsx";
import ServiceManager from "./components/ServiceManager.jsx";
import IntegrationManager from "./components/IntegrationManager.jsx";
import DashboardSettings from "./components/DashboardSettings.jsx";
import ImportExport from "./components/ImportExport.jsx";
import NewPageDialog from "./components/NewPageDialog.jsx";
import ExportPageDialog from "./components/ExportPageDialog.jsx";
import SettingsModal from "./components/SettingsModal.jsx";
import CommandPalette from "./components/CommandPalette.jsx";
import Modal from "./components/Modal.jsx";
import LoginScreen, { LoginForm } from "./components/Login.jsx";
import { WIDGET_GROUPS, WIDGET_TYPES, defaultConfig } from "./widgets/index.jsx";

const HEALTH_POLL_MS = 10_000;
const HISTORY_POLL_MS = 60_000;
const RELOAD_AFTER_HIDDEN_MS = 30_000; // refetch everything when a tab comes back after this long
const REVISION_POLL_MS = 3000; // how quickly edits made outside the app (Claude via MCP) show up
const TOAST_MS = 6000;

/*
 * Last-known state, kept in localStorage so a new tab paints the page immediately
 * instead of waiting for the API. The network response replaces it a moment later.
 * index.html also reads `settings` from here to apply the theme before first paint.
 */
const CACHE_KEY = "sp.cache.v1";
/** Integration configs hold API keys and headers: they never go into localStorage. View mode only needs name/type. */
const stripSecrets = (c) => (Array.isArray(c?.integrations) ? { ...c, integrations: c.integrations.map((i) => ({ ...i, config: {} })) } : c);
const hasSecrets = (c) => Array.isArray(c?.integrations) && c.integrations.some((i) => i?.config && Object.keys(i.config).length > 0);
const readCache = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(CACHE_KEY)) || {};
    if (!hasSecrets(raw)) return raw;
    // A cache written before configs were stripped: scrub it now rather than wait for the next save.
    const clean = stripSecrets(raw);
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(clean));
    } catch {}
    return clean;
  } catch {
    return {};
  }
};
let boot = readCache();
// Mirror readCache: storage can throw (blocked cookies / storage-disabled iframes) and a throw
// here at module scope would blank the whole bundle.
const readBootId = () => {
  try {
    return localStorage.getItem("sp.dashboard");
  } catch {
    return null;
  }
};
let bootId = readBootId() || boot.dashboards?.[0]?.id || null;

/** Drop the cached page (sign-out, or the server turned out to require a login): nothing may paint from it. */
const forgetCache = () => {
  boot = {};
  bootId = null;
  try {
    localStorage.removeItem(CACHE_KEY);
    localStorage.removeItem("sp.dashboard");
  } catch {}
};

/* Last auth/status, so a reload paints the right thing at once (login screen vs. page). */
const AUTH_KEY = "sp.auth.v1";
const AUTH_OFF = { enabled: false, has_password: false, authenticated: false, guest_access: "none", can_write: true, user: null, method: null };
const readAuth = () => {
  try {
    return { ...AUTH_OFF, ...(JSON.parse(localStorage.getItem(AUTH_KEY)) || {}) };
  } catch {
    return AUTH_OFF;
  }
};
const needsLogin = (s) => s.enabled && !s.authenticated && s.guest_access !== "read";

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
  ["Shift+W", "open the widget menu (edit mode)"],
  ["Ctrl+V", "paste a URL to add a link (edit mode)"],
  ["Arrows", "nudge the selected widget (edit mode)"],
  ["Shift+Arrows", "resize the selected widget (edit mode)"],
  ["Enter", "settings of the selected widget"],
  ["Ctrl+D", "duplicate the selected widget"],
  ["Delete", "delete the selected widget"],
  ["?", "this help"],
];

/** The fields needed to recreate a widget exactly (same id) when undoing a delete. */
const widgetSnapshot = (w) => ({ id: w.id, type: w.type, x: w.x, y: w.y, w: w.w, h: w.h, service_id: w.service_id, config: w.config, layout_sm: w.layout_sm ?? null });

function Dashboard({ access, onSignOut, onLoggedIn, onAuthChanged }) {
  const canWrite = access.can_write;
  const [dashboards, setDashboards] = useState(boot.dashboards || []);
  const [currentId, setCurrentId] = useState(bootId);
  const [dashboard, setDashboard] = useState(boot.pages?.[bootId] || null);
  // True once a page came from the server (not just the cache). Published as <html data-ready> for the MCP screenshot tool.
  const [loaded, setLoaded] = useState(false);
  const ready = loaded && !!dashboard;
  useEffect(() => {
    if (!ready) return;
    document.documentElement.dataset.ready = "page";
    return () => { delete document.documentElement.dataset.ready; };
  }, [ready]);
  const [services, setServices] = useState(boot.services || []);
  const [health, setHealth] = useState({});
  const [healthHistory, setHealthHistory] = useState({}); // service id -> 24 h history
  const [integrations, setIntegrations] = useState(boot.integrations || []);
  const [integrationData, setIntegrationData] = useState({});
  const [settings, setSettings] = useState(boot.settings || null);
  const [wantEdit, setEditMode] = useState(false);
  const editMode = canWrite && wantEdit; // view-only guests can never enter edit mode
  // Which layout edit mode works on. Each page keeps a desktop layout and an optional phone layout;
  // a narrow window opens edit mode on the phone one.
  const [device, setDevice] = useState("desktop");
  useEffect(() => { setDevice(editMode && window.innerWidth < NARROW_PX ? "phone" : "desktop"); }, [editMode]);
  const [modal, setModal] = useState(null);
  const [widgetMenuOpen, setWidgetMenuOpen] = useState(false);
  useEffect(() => { if (!editMode) setWidgetMenuOpen(false); }, [editMode]);
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
  const loadDashboard = useCallback(async (id = currentId) => {
    if (!id) return;
    const d = await api.getDashboard(id);
    // Ignore a late response for a page the user already switched away from.
    if (currentIdRef.current === d.id) {
      setDashboard(d);
      setLoaded(true);
      setError(""); // a successful load clears any stale banner
    }
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

  // The page list first, then the current page only if it still exists: after an undo, import or
  // restore removed it, loadDashboards has already switched pages and a fetch of the old id would 404.
  const reloadPages = useCallback(async () => {
    const list = await loadDashboards();
    const id = currentIdRef.current;
    if (list.some((d) => d.id === id)) await loadDashboard(id);
  }, [loadDashboards, loadDashboard]);

  const reloadAll = useCallback(async () => {
    await Promise.all([reloadPages(), loadServices(), loadIntegrations(), loadSettings()]);
  }, [reloadPages, loadServices, loadIntegrations, loadSettings]);
  const reloadRef = useRef(reloadAll);
  reloadRef.current = reloadAll;
  // History entries outlive renders, so they refresh through a ref (always the current page).
  const refreshPagesRef = useRef();
  refreshPagesRef.current = reloadPages;
  const refreshPages = () => refreshPagesRef.current();

  const offlineMessage = (e) =>
    boot.settings && e instanceof TypeError ? "Can't reach the Start Page server. Showing the last saved copy when one exists." : e.message;

  useEffect(() => {
    Promise.all([loadDashboards(), loadServices(), loadIntegrations(), loadSettings()]).catch((e) => setError(offlineMessage(e)));
  }, []); // eslint-disable-line

  useEffect(() => {
    if (!currentId) return;
    try {
      localStorage.setItem("sp.dashboard", currentId);
    } catch {} // storage can be blocked; remembering the page is optional
    setSelectedId(null);
    const cached = readCache().pages?.[currentId];
    setDashboard((cur) => (cur?.id === currentId ? cur : cached || null));
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
    const t = setTimeout(() => setToast(null), toast.stay ? TOAST_MS * 5 : TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => setSelectedId(null), [device]); // a selection belongs to one layout

  // A new service worker took over (a new build is live): offer a reload, never force one.
  useEffect(() => {
    const onUpdated = () => setToast({ text: "Updated — reload to use the new version.", actionLabel: "Reload", action: () => location.reload(), stay: true });
    window.addEventListener(UPDATE_EVENT, onUpdated);
    return () => window.removeEventListener(UPDATE_EVENT, onUpdated);
  }, []);

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

  // Health history (uptime, sparklines) changes slowly: once on load, then at most once a minute
  // while the tab is visible.
  useEffect(() => {
    let last = 0;
    const load = () => {
      if (document.hidden) return;
      last = Date.now();
      api
        .healthHistory(24, 48)
        .then((list) => setHealthHistory(Object.fromEntries((list || []).map((h) => [h.service_id, h]))))
        .catch(() => {});
    };
    const onVisibility = () => {
      if (!document.hidden && Date.now() - last >= HISTORY_POLL_MS) load();
    };
    load();
    const t = setInterval(load, HISTORY_POLL_MS);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // Pick up edits made outside the app (the backend only counts those, never our own writes).
  const clearHistory = history.clear;
  useEffect(() => {
    let seen = null; // "boot:rev" last acted on
    let running = false;
    const check = async () => {
      if (document.hidden || running) return;
      running = true;
      try {
        const r = await api.revision();
        if (!r) return;
        const key = `${r.boot}:${r.rev}`;
        if (seen === null) seen = key;
        if (key === seen) return;
        // Never swap the page out from under a drag or resize; the next tick retries.
        if (document.querySelector(".react-draggable-dragging, .resizing")) return;
        await reloadRef.current();
        seen = key;
        clearHistory();
        setToast({ text: "Page updated outside this tab — undo history cleared." });
      } catch {
        // offline or mid-restart: try again next tick
      } finally {
        running = false;
      }
    };
    check();
    const t = setInterval(check, REVISION_POLL_MS);
    return () => clearInterval(t);
  }, [clearHistory]);

  // ----- keep the local cache fresh -----
  useEffect(() => {
    if (!settings || !dashboards.length) return;
    const pages = { ...(readCache().pages || {}) };
    if (dashboard) pages[dashboard.id] = dashboard;
    for (const id of Object.keys(pages)) if (!dashboards.some((d) => d.id === id)) delete pages[id];
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(stripSecrets({ dashboards, services, integrations, settings, pages })));
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
    // Browser/OS chrome colour: the accent, the same colour the manifest's theme_color carries.
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", settings.accent || "#6c8cff");
  }, [settings]);

  const page = dashboard ? gridOptions(dashboard) : {};
  const pageBg = page.background_image || settings?.background_image || "";
  // A page's dim/blur belong to its own image. Page settings always stores them (40 / 0 by default),
  // so without a page image they must not override the global background's values.
  const bgDim = (page.background_image ? page.background_dim : undefined) ?? settings?.background_dim ?? 40;
  const bgBlur = (page.background_image ? page.background_blur : undefined) ?? settings?.background_blur ?? 0;

  // ----- mutations -----
  const run = async (fn) => {
    try {
      setError("");
      await fn();
    } catch (e) {
      setError(e.message);
    }
  };

  /** A toast's Undo/Redo button is about one specific step: only act while that step is still next in line. */
  const ifNext = (entry, fn) => () =>
    history.isTop(entry) ? fn() : setToast({ text: "Other changes came after that one. Use Ctrl+Z to step back through them." });
  const doUndo = () => run(async () => { const e = await history.undo(); if (e) setToast({ text: `Undid: ${e.label}`, actionLabel: "Redo", action: ifNext(e, doRedo) }); });
  const doRedo = () => run(async () => { const e = await history.redo(); if (e) setToast({ text: `Redid: ${e.label}`, actionLabel: "Undo", action: ifNext(e, doUndo) }); });
  const doSkip = () => { const e = history.skip(); if (e) setToast({ text: `Skipped: ${e.label}` }); };

  /** Run a history step on the page it belongs to, so undo never changes a page you can't see. */
  const onPage = async (dashId, fn) => {
    await fn();
    if (dashId && currentIdRef.current !== dashId) setCurrentId(dashId);
    await refreshPages();
  };

  /** Where each widget sits on the phone grid as the Canvas shows it (derived where none is stored), hidden ones included. */
  const phoneItems = (d = dashboard) =>
    derivePhoneLayout(d.widgets, { cols: d.columns || 24, colsSm: phoneOptions(d).colsSm }, { withHidden: true });

  // breakpoint "sm" edits a widget's phone placement (layout_sm); a hidden flag only when the item carries one.
  const applyLayout = (items, breakpoint = "lg") =>
    setDashboard((d) =>
      d && {
        ...d,
        widgets: d.widgets.map((w) => {
          const u = items.find((i) => i.id === w.id);
          if (!u) return w;
          if (breakpoint === "sm") {
            const { id, ...geo } = u;
            return { ...w, layout_sm: { hidden: w.layout_sm?.hidden ?? false, ...geo } };
          }
          return { ...w, ...u };
        }),
      }
    );

  // Layout saves go out one at a time, so a burst of nudges can't land out of order.
  // Desktop and phone entries carry their own breakpoint and before/after values, so undoing one
  // never writes the other layout.
  const layoutQueue = useRef(Promise.resolve());
  const onLayoutChange = (items, { label = "Move widget", mergeKey, breakpoint = "lg" } = {}) =>
    run(async () => {
      const dashId = currentId;
      const before =
        breakpoint === "sm"
          ? phoneItems()
              .filter((p) => items.some((i) => i.id === p.id))
              .map(({ id, x, y, w, h, hidden, pinned }) => ({ id, x, y, w, h, hidden, pinned }))
          : items
              .map((i) => dashboard.widgets.find((w) => w.id === i.id))
              .filter(Boolean)
              .map(({ id, x, y, w, h }) => ({ id, x, y, w, h }));
      // Phone "before": a widget that had no stored phone position goes back to none, not to the spot derived for it.
      const restoreBefore = async () => {
        if (breakpoint !== "sm") return api.saveLayout(dashId, before, breakpoint);
        const pinned = before.filter((b) => b.pinned).map(({ pinned: _p, ...geo }) => geo);
        if (pinned.length) await api.saveLayout(dashId, pinned, "sm");
        for (const b of before) if (!b.pinned) await api.updateWidget(b.id, { layout_sm: null });
      };
      const rollbackLocal = () => {
        if (breakpoint !== "sm") return applyLayout(before, breakpoint);
        applyLayout(before.filter((b) => b.pinned), "sm");
        const loose = new Set(before.filter((b) => !b.pinned).map((b) => b.id));
        setDashboard((d) => d && { ...d, widgets: d.widgets.map((w) => (loose.has(w.id) ? { ...w, layout_sm: null } : w)) });
      };
      applyLayout(items, breakpoint);
      const save = layoutQueue.current.catch(() => {}).then(() => api.saveLayout(dashId, items, breakpoint));
      layoutQueue.current = save;
      try {
        await save;
      } catch (e) {
        rollbackLocal(); // the save failed: don't leave the widget where the server never put it
        throw e;
      }
      history.push({
        label,
        mergeKey,
        undo: () => onPage(dashId, restoreBefore),
        redo: () => onPage(dashId, () => api.saveLayout(dashId, items, breakpoint)),
      });
    });

  const phoneChange = (items) => onLayoutChange(items, { label: "Move widget (phone)", breakpoint: "sm" });

  const togglePhoneHidden = (widget, hidden) => {
    const cur = phoneItems().find((p) => p.id === widget.id);
    if (!cur) return;
    onLayoutChange([{ id: cur.id, x: cur.x, y: cur.y, w: cur.w, h: cur.h, hidden }], { label: hidden ? "Hide on phone" : "Show on phone", breakpoint: "sm" });
  };

  /** Switch a page to its custom phone layout (seeded from the automatic stack) or back to automatic; one undo step each. */
  // `extra` carries the other page fields of a settings-dialog save that switched the page to Custom.
  const changePhoneLayout = (mode, extra = {}) =>
    run(async () => {
      await layoutQueue.current.catch(() => {});
      const d = dashboard;
      const dashId = currentId;
      const prevSettings = d.settings || {};
      const nextSettings = { ...prevSettings, ...(extra.settings || {}), phone_layout: mode };
      const nextData = { ...extra, settings: nextSettings };
      const prevData = Object.fromEntries(Object.keys(nextData).map((k) => [k, d[k]]));
      prevData.settings = prevSettings;
      const seed = mode === "custom" ? seedPhoneLayout(d.widgets, { cols: nextData.columns || d.columns || 24, colsSm: phoneOptions({ settings: nextSettings }).colsSm }) : null;
      const before = d.widgets.filter((w) => w.layout_sm).map((w) => ({ id: w.id, ...w.layout_sm }));
      // Undo: the saved positions go back first (clamped to the page's current phone columns, which may have
      // shrunk since: the server refuses a position that overflows), then the positions this step created are
      // removed one widget at a time. A failure part-way never leaves the page with no layout at all.
      const restore = async () => {
        const cur = await api.getDashboard(dashId);
        const fitted = fitToGrid(before, phoneOptions(cur).colsSm);
        if (fitted.length) await api.saveLayout(dashId, fitted, "sm");
        const keep = new Set(fitted.map((i) => i.id));
        for (const w of cur.widgets || []) if (w.layout_sm && !keep.has(w.id)) await api.updateWidget(w.id, { layout_sm: null });
        await api.updateDashboard(dashId, prevData);
      };
      const apply = async () => {
        if (seed) await api.saveLayout(dashId, seed, "sm");
        else await api.clearPhoneLayout(dashId);
        await api.updateDashboard(dashId, nextData);
      };
      await apply();
      history.push({
        label: mode === "custom" ? "Start phone layout" : "Reset phone layout",
        undo: () => onPage(dashId, restore),
        redo: () => onPage(dashId, apply),
      });
      setModal(null);
      await refreshPages();
    });

  /** Arrow-key nudge (dx/dy) or resize (dw/dh) of the selected widget, in steps of the snap size. */
  const nudge = (dx, dy, dw, dh) => {
    const phone = device === "phone";
    const w = phone ? phoneItems().find((x) => x.id === selectedId) : dashboard?.widgets.find((x) => x.id === selectedId);
    if (!w) return;
    const step = nudgeStep(dashboard);
    const cols = phone ? phoneOptions(dashboard).colsSm : dashboard.columns || 24;
    const round = (v) => Math.round(v * 1000) / 1000;
    const nw = round(Math.max(step, Math.min(cols, w.w + dw * step)));
    const nh = round(Math.max(step, w.h + dh * step));
    const nx = round(Math.max(0, Math.min(cols - nw, w.x + dx * step)));
    const ny = round(Math.max(0, w.y + dy * step));
    if (nx === w.x && ny === w.y && nw === w.w && nh === w.h) return;
    onLayoutChange([{ id: w.id, x: nx, y: ny, w: nw, h: nh }], {
      label: `${dw || dh ? "Resize" : "Move"} widget${phone ? " (phone)" : ""}`,
      mergeKey: `nudge:${phone ? "sm:" : ""}${w.id}:${dw || dh ? "size" : "pos"}`,
      breakpoint: phone ? "sm" : "lg",
    });
  };

  /** Save a widget's config in place (used by widgets that edit themselves, e.g. Notes). Not part of undo. */
  const updateWidgetConfig = useCallback(async (id, config, { keepalive = false } = {}) => {
    setDashboard((d) => d && { ...d, widgets: d.widgets.map((w) => (w.id === id ? { ...w, config } : w)) });
    try {
      // keepalive lets the save finish while the tab closes; browsers cap such bodies at 64 KB.
      await api.updateWidget(id, { config }, { keepalive: keepalive && JSON.stringify(config).length < 60_000 });
    } catch (e) {
      setError(`Couldn't save: ${e.message}`);
      throw e;
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
      const entry = history.push({
        label: "Delete widget",
        undo: () => onPage(dashId, () => api.createWidget(dashId, snap)),
        redo: () => onPage(dashId, () => api.deleteWidget(snap.id)),
      });
      setModal(null);
      setSelectedId(null);
      setToast({ text: `${WIDGET_TYPES[widget.type]?.label || "Widget"} deleted`, actionLabel: "Undo", action: ifNext(entry, doUndo) });
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
        const created = await api.createWidget(targetId, { ...fields, ...spot, layout_sm: phoneSpotOn(widget.layout_sm, target) });
        const snap = widgetSnapshot(created);
        history.push({
          label: `Copy widget to ${target.name}`,
          undo: () => onPage(null, () => api.deleteWidget(snap.id)),
          redo: () => onPage(null, () => api.createWidget(targetId, snap)),
        });
      } else {
        // The phone placement travels with the widget (to a free spot on a page with a custom phone layout).
        const before = { dashboard_id: fromId, x: widget.x, y: widget.y, layout_sm: widget.layout_sm ?? null };
        const after = { dashboard_id: targetId, ...spot, layout_sm: phoneSpotOn(widget.layout_sm, target) };
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

  const addDashboard = (given) =>
    run(async () => {
      const name = typeof given === "string" ? given : prompt("Dashboard name", "New page");
      if (!name) return;
      setModal(null);
      const d = await api.createDashboard({ name });
      let detail = null; // this page's full detail (widgets included), captured by its undo for its redo to restore
      history.push({
        label: "Add page",
        undo: () =>
          onPage(null, async () => {
            detail = await api.getDashboard(d.id);
            await api.deleteDashboard(d.id);
          }),
        redo: async () => {
          if (!detail) {
            await api.createDashboard({ id: d.id, name: d.name, position: d.position });
          } else {
            await api.importConfig({ services: [], integrations: [], settings: {}, dashboards: [detail] }, false);
          }
          await refreshPages();
          setCurrentId(d.id);
        },
      });
      await loadDashboards();
      setCurrentId(d.id);
    });

  /** A page created from a template or a page file (the import response): undone like "Add page" (the page is deleted,
   *  its full detail is kept for redo). Services it created stay: other widgets may use them by now. */
  const addImportedPage = async (result) => {
    const d = result.dashboard;
    let detail = result.dashboard;
    history.push({
      label: "Add page from template",
      undo: () =>
        onPage(null, async () => {
          detail = await api.getDashboard(d.id);
          await api.deleteDashboard(d.id);
        }),
      redo: async () => {
        await api.importConfig({ services: [], integrations: [], settings: {}, dashboards: [detail] }, false);
        await refreshPages();
        setCurrentId(d.id);
      },
    });
    setModal(null);
    await Promise.all([loadDashboards(), loadServices()]);
    setCurrentId(d.id);
    const later = result.unresolved_integrations?.length;
    setToast({ text: `Created "${d.name}"${later ? ` — ${later} integration${later === 1 ? "" : "s"} still to set up` : ""}` });
  };

  const saveDashboard = (data) => {
    // Switching a page to Custom runs the same seed as the toolbar button: otherwise its widgets have no phone positions.
    if (data.settings?.phone_layout === "custom" && !phoneOptions(dashboard).custom) return changePhoneLayout("custom", data);
    return saveDashboardPlain(data);
  };
  const saveDashboardPlain = (data) =>
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
      try {
        await api.updateDashboard(d.id, { name });
      } catch (e) {
        setDashboards((list) => list.map((x) => (x.id === d.id ? { ...x, name: d.name } : x)));
        throw e;
      }
      history.push({
        label: "Rename page",
        undo: () => onPage(null, () => api.updateDashboard(d.id, { name: d.name })),
        redo: () => onPage(null, () => api.updateDashboard(d.id, { name })),
      });
      await refreshPages();
    });
  };

  const applyOrder = (ids) => api.reorderDashboards(ids); // one request, all or nothing

  const reorderDashboards = (ids) =>
    run(async () => {
      const before = dashboards.map((d) => d.id);
      if (before.join() === ids.join()) return;
      setDashboards((list) => ids.map((id) => list.find((d) => d.id === id)));
      try {
        await applyOrder(ids);
      } catch (e) {
        loadDashboards().catch(() => {}); // show the order the server still has
        throw e;
      }
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
      const entry = history.push({
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
      setToast({ text: `Page "${detail.name}" deleted`, actionLabel: "Undo", action: ifNext(entry, doUndo) });
    });

  /** After an import or backup restore: undo steps captured the old server state, so they are all stale. */
  const reloadReplaced = async () => {
    history.clear();
    await reloadAll();
  };

  const saveSettings = (data) => run(async () => { setSettings(await api.updateSettings(data)); setModal(null); });

  // ----- keyboard shortcuts (re-registered each render so handlers see current state) -----
  useEffect(() => {
    const onKey = (e) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.isContentEditable;
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (mod && key === "k") {
        e.preventDefault();
        // Never replace another open dialog (and its unsaved form) with the palette.
        setModal((m) => (m?.kind === "palette" ? null : m ? m : { kind: "palette" }));
        return;
      }
      if (modal || typing) return;
      if (mod && (key === "z" || key === "y")) {
        if (!canWrite) return;
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
      else if (key === "e") { if (canWrite) setEditMode((m) => !m); }
      else if (e.key === "Escape") setEditMode(false);
      else if (/^[1-9]$/.test(e.key)) { const d = dashboards[Number(e.key) - 1]; if (d) setCurrentId(d.id); }
      else if (editMode && key === "s") setModal({ kind: "services" });
      else if (editMode && key === "w") { if (e.shiftKey) setWidgetMenuOpen(true); else addWidget("link"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ----- paste a URL in edit mode to add a Link widget -----
  const pasteGuard = useRef({});
  pasteGuard.current = { modal, editMode };
  useEffect(() => {
    const onPaste = async (e) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName) || e.target.isContentEditable;
      if (!editMode || modal || typing || !dashboard) return;
      const text = (e.clipboardData?.getData("text") || "").trim();
      if (!looksLikeUrl(text)) return;
      e.preventDefault();
      const url = normalizeUrl(text);
      const meta = await Promise.race([api.siteMeta(url).catch(() => null), new Promise((r) => setTimeout(() => r(null), 2500))]);
      // The title lookup took a moment: don't open over a dialog the user opened meanwhile, or after edit mode ended.
      if (pasteGuard.current.modal || !pasteGuard.current.editMode) return;
      addWidget("link", { url, name: tidyTitle(meta?.title) || hostOf(url) });
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [editMode, modal, dashboard]); // eslint-disable-line

  const links = useMemo(() => collectLinks(dashboard?.widgets), [dashboard]);

  const ctx = useMemo(
    () => ({
      settings: settings || {}, services, dashboards, links, health, editMode, canWrite, goToPage: setCurrentId,
      updateWidgetConfig: canWrite ? updateWidgetConfig : undefined, // widgets skip saving when it is missing
    }),
    [settings, services, dashboards, links, health, editMode, canWrite, updateWidgetConfig]
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
          style={{ backgroundImage: cssUrl(pageBg), filter: bgBlur ? `blur(${bgBlur}px)` : undefined }}
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
          {editMode && <button className="tab tab-add" onClick={() => setModal({ kind: "newpage" })} title="New page: blank, from a template or from a file">+</button>}
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
              <div className="btn-group" role="group" aria-label="Layout being edited">
                <button className={device === "desktop" ? "btn-primary" : ""} aria-pressed={device === "desktop"} onClick={() => setDevice("desktop")} title="Edit the desktop layout">Desktop</button>
                <button className={device === "phone" ? "btn-primary" : ""} aria-pressed={device === "phone"} onClick={() => setDevice("phone")} title="Edit the phone layout">Phone</button>
              </div>
              {device === "phone" && dashboard && phoneOptions(dashboard).custom && (
                <button className="nowrap" onClick={() => changePhoneLayout("auto")} title="Go back to stacking widgets automatically on phones (undoable)">Reset to automatic</button>
              )}
              <WidgetMenu types={WIDGET_TYPES} groups={WIDGET_GROUPS} open={widgetMenuOpen} onOpenChange={setWidgetMenuOpen} onPick={addWidget} />
              {dashboard && (
                <select
                  className="snap-select"
                  title="Snap while dragging"
                  value={page.snap}
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
          {canWrite && (
            <button className={editMode ? "btn-primary" : ""} onClick={() => setEditMode((m) => !m)} title="Toggle edit mode (E)">
              {editMode ? "Done" : "Edit"}
            </button>
          )}
          {!canWrite && <button onClick={() => setModal({ kind: "signin" })}>Sign in</button>}
          {access.enabled && access.authenticated && access.method === "session" && (
            <button className="btn-sm btn-quiet" onClick={onSignOut} title="Sign out of this browser">Sign out</button>
          )}
        </div>
      </header>

      {error && (
        <div className="banner error" onClick={() => setError("")}>
          {error}
          {history.failedLabel && (
            <>
              {" "}
              <button className="btn-sm" onClick={doSkip} title="Drop this step from undo history. Its change stays as it is; older steps become reachable again.">
                Skip stuck {history.failedAction}: "{history.failedLabel}"
              </button>
            </>
          )}
        </div>
      )}

      {dashboard ? (
        <Canvas
          dashboard={dashboard}
          widgets={dashboard.widgets}
          services={services}
          health={health}
          history={healthHistory}
          integrations={integrations}
          integrationData={integrationData}
          onRefresh={loadIntegrations}
          editMode={editMode}
          onLayoutChange={(items) => onLayoutChange(items, { label: "Move widget" })}
          device={device}
          onPhoneLayoutChange={phoneChange}
          onTogglePhoneHidden={togglePhoneHidden}
          onStartPhoneLayout={() => changePhoneLayout("custom")}
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

      {/* Stays mounted under the service/integration manager it opened, so its unsaved edits survive. */}
      {(modal?.kind === "widget" || modal?.back?.kind === "widget") && (
        <WidgetEditor
          widget={(modal.back ?? modal).widget}
          services={services}
          integrations={integrations}
          integrationData={integrationData}
          dashboards={dashboards}
          onSave={saveWidget}
          onDelete={() => deleteWidget(modal.widget)}
          onDuplicate={() => duplicateWidget(modal.widget)}
          onMoveTo={(targetId, copy) => moveWidget(modal.widget, targetId, copy)}
          onClose={() => modal.kind === "widget" && setModal(null)} // Escape belongs to the manager on top
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
        <DashboardSettings dashboard={dashboard} canDelete={dashboards.length > 1} onSave={saveDashboard} onDelete={deleteDashboard} onExport={() => setModal({ kind: "exportpage" })} onClose={() => setModal(null)} />
      )}
      {modal?.kind === "newpage" && <NewPageDialog integrations={integrations} onBlank={addDashboard} onCreated={addImportedPage} onClose={() => setModal(null)} />}
      {modal?.kind === "exportpage" && dashboard && <ExportPageDialog dashboard={dashboard} onClose={() => setModal(null)} />}
      {modal?.kind === "settings" && settings && (
        <SettingsModal settings={settings} auth={access} onAuthChanged={onAuthChanged} onSave={saveSettings} onSettingsSaved={setSettings} onRestored={reloadReplaced} onClose={() => setModal(null)} />
      )}
      {modal?.kind === "signin" && (
        <Modal title="Sign in" onClose={() => setModal(null)}>
          <LoginForm onLoggedIn={onLoggedIn} />
        </Modal>
      )}
      {modal?.kind === "io" && <ImportExport onImported={reloadReplaced} onClose={() => setModal(null)} />}
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
          <p className="help">In edit mode: click a widget to select it, drag it by its body, resize from any edge or corner (lines from either end), ⚙ or double-click to configure. Drag page tabs to reorder them; double-click a tab to rename it.</p>
          <p className="help">While dragging or resizing, widgets snap first to grid lines, then to the edges and centres of other widgets (and the page edges/centre) — dashed guide lines show the match. Divider lines run through the middle of their box and join end-to-end. Hold <kbd>Alt</kbd> to move freely.</p>
        </Modal>
      )}
    </div>
    </AppContext.Provider>
  );
}

/*
 * Auth gate. Authentication is optional and off by default: with no password this renders the
 * page exactly as before. When the server requires a login (and guests are not allowed), the
 * dashboard is not mounted at all, so nothing is polled and nothing is painted from the cache.
 */
export default function App() {
  const [status, setStatus] = useState(readAuth);
  const [epoch, setEpoch] = useState(0); // remounts the page after sign-in/out so it reloads what the new role may see
  const [signingOut, setSigningOut] = useState(false);

  const applyStatus = useCallback((s) => {
    const next = { ...AUTH_OFF, ...(s || {}) };
    try {
      localStorage.setItem(AUTH_KEY, JSON.stringify(next));
    } catch {}
    if (needsLogin(next)) {
      // Unmount the page first (synchronously), so a late poll cannot write the cache back after it is cleared.
      flushSync(() => setStatus(next));
      forgetCache();
    } else {
      setStatus(next);
    }
    return next;
  }, []);

  // "pending" until the first auth/status answer. With proxy/SSO auth in front, the cached copy may only be
  // painted once the server confirms the session: a cached shell served offline must not show cached pages.
  const [probe, setProbe] = useState("pending"); // pending | ok | failed
  const viaProxy = status.method === "proxy";
  // A failed probe (server restarting, offline) keeps the last known state.
  const refreshStatus = useCallback(
    () =>
      api
        .authStatus()
        .then((s) => {
          setProbe("ok");
          return applyStatus(s);
        })
        .catch(() => {
          setProbe("failed");
          return status;
        }),
    [applyStatus] // eslint-disable-line
  );

  useEffect(() => {
    refreshStatus();
    api.onUnauthorized(refreshStatus);
    return () => api.onUnauthorized(null);
  }, [refreshStatus]);

  const loginShown = needsLogin(status);
  useEffect(() => {
    if (!loginShown) return;
    document.documentElement.dataset.ready = "login"; // lets the MCP screenshot tool tell a login screen from a loading page
    return () => { delete document.documentElement.dataset.ready; };
  }, [loginShown]);

  const onLoggedIn = async () => {
    await refreshStatus();
    setEpoch((e) => e + 1);
  };
  const onSignOut = async () => {
    try {
      await api.logout();
    } catch {}
    flushSync(() => setSigningOut(true)); // unmount the page before the cache goes, so nothing can rewrite it
    forgetCache();
    await refreshStatus();
    setEpoch((e) => e + 1);
    setSigningOut(false);
  };

  if (signingOut) return null;
  if (viaProxy && probe !== "ok") {
    return (
      <div className="login-screen" role="status" data-ready={probe === "failed" ? "offline" : undefined}>
        <div className="login-card">
          <h1>{probe === "failed" ? "Can't reach the server" : "Connecting…"}</h1>
          {probe === "failed" && (
            <>
              <p className="muted">Your sign-in is handled by a proxy, so the saved copy is not shown until the server answers.</p>
              <button className="btn" onClick={() => { setProbe("pending"); refreshStatus(); }}>Try again</button>
            </>
          )}
        </div>
      </div>
    );
  }
  if (needsLogin(status)) return <LoginScreen onLoggedIn={onLoggedIn} />;
  return <Dashboard key={epoch} access={status} onSignOut={onSignOut} onLoggedIn={onLoggedIn} onAuthChanged={refreshStatus} />;
}
