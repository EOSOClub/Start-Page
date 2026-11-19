const BASE = "/api";

let onUnauthorized = null;
let unauthorizedPending = false; // cleared once the app has re-checked auth/status

/** FastAPI 422s carry a list of {loc, msg}: show "field: message" instead of raw JSON. */
function describeDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && d.msg ? `${(d.loc || []).filter((p) => p !== "body").join(".") || "request"}: ${d.msg}` : JSON.stringify(d))).join("; ");
  }
  return JSON.stringify(detail);
}

async function request(method, path, body, { keepalive = false } = {}) {
  // X-Requested-By marks the call as coming from this app; the backend rejects
  // state-changing requests without it (cross-site request protection).
  const headers = { "X-Requested-By": "startpage" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(BASE + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    keepalive,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    const err = new Error(describeDetail(detail));
    err.status = res.status;
    err.retryAfter = Number(res.headers.get("Retry-After")) || 0;
    // A 401 on any call means the session ended (or auth was switched on): tell the app once,
    // instead of every caller handling it. Login itself and the status probe report their own.
    if (res.status === 401 && !path.startsWith("/auth/") && onUnauthorized && !unauthorizedPending) {
      unauthorizedPending = true;
      onUnauthorized();
    }
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  // dashboards
  listDashboards: () => request("GET", "/dashboards"),
  getDashboard: (id) => request("GET", `/dashboards/${id}`),
  createDashboard: (data) => request("POST", "/dashboards", data),
  updateDashboard: (id, data) => request("PATCH", `/dashboards/${id}`, data),
  deleteDashboard: (id) => request("DELETE", `/dashboards/${id}`),
  reorderDashboards: (ids) => request("PUT", "/dashboards/order", ids),
  // breakpoint "sm" writes the phone layout (layout_sm); the default is the desktop grid, exactly as before.
  saveLayout: (id, items, breakpoint) => request("PUT", `/dashboards/${id}/layout${breakpoint === "sm" ? "?breakpoint=sm" : ""}`, items),
  clearPhoneLayout: (id) => request("DELETE", `/dashboards/${id}/layout?breakpoint=sm`),
  // widgets
  createWidget: (dashboardId, data) => request("POST", `/dashboards/${dashboardId}/widgets`, data),
  updateWidget: (id, data, opts) => request("PATCH", `/widgets/${id}`, data, opts),
  duplicateWidget: (id) => request("POST", `/widgets/${id}/duplicate`),
  deleteWidget: (id) => request("DELETE", `/widgets/${id}`),
  // services
  listServices: () => request("GET", "/services"),
  createService: (data) => request("POST", "/services", data),
  updateService: (id, data) => request("PATCH", `/services/${id}`, data),
  deleteService: (id) => request("DELETE", `/services/${id}`),
  // service discovery (signed-in writers only)
  discoverDocker: ({ integration_id, default_host } = {}) => {
    const q = new URLSearchParams();
    if (integration_id) q.set("integration_id", integration_id);
    if (default_host) q.set("default_host", default_host);
    return request("GET", `/discovery/docker${q.size ? `?${q}` : ""}`);
  },
  importDiscovered: (items) => request("POST", "/discovery/import", { items }),
  // health
  health: () => request("GET", "/health"),
  checkHealth: (serviceId) => request("POST", `/health/${serviceId}/check`),
  healthHistory: (hours = 24, buckets = 48) => request("GET", `/health/history?hours=${hours}&buckets=${buckets}`),
  healthEvents: ({ limit = 50, service_id } = {}) =>
    request("GET", `/health/events?limit=${limit}${service_id ? `&service_id=${encodeURIComponent(service_id)}` : ""}`),
  notifyTest: () => request("POST", "/notify/test"),
  // integrations
  listIntegrations: () => request("GET", "/integrations"),
  createIntegration: (data) => request("POST", "/integrations", data),
  updateIntegration: (id, data) => request("PATCH", `/integrations/${id}`, data),
  deleteIntegration: (id) => request("DELETE", `/integrations/${id}`),
  integrationData: () => request("GET", "/integrations/data"),
  refreshIntegration: (id) => request("POST", `/integrations/${id}/refresh`),
  dockerAction: (id, container, action) => request("POST", `/integrations/${id}/docker/${container}/${action}`),
  integrationTypes: () => request("GET", "/integrations/types"),
  testIntegration: (data) => request("POST", "/integrations/test", data), // {type, config, id?} -> {ok, error, sample}
  integrationAction: (id, name, payload = {}) => request("POST", `/integrations/${id}/action/${name}`, payload),
  // settings
  getSettings: () => request("GET", "/settings"),
  updateSettings: (data) => request("PATCH", "/settings", data),
  // backups
  listBackups: () => request("GET", "/backups"),
  createBackup: (label) => request("POST", `/backups?label=${encodeURIComponent(label || "manual")}`),
  restoreBackup: (name, replace = true) => request("POST", `/backups/${encodeURIComponent(name)}/restore?replace=${replace}`),
  deleteBackup: (name) => request("DELETE", `/backups/${encodeURIComponent(name)}`),
  // icons
  searchIcons: (q, limit = 60) => request("GET", `/icons?q=${encodeURIComponent(q)}&limit=${limit}`),
  faviconUrl: (url) => `${BASE}/favicon?url=${encodeURIComponent(url)}`,
  siteMeta: (url) => request("GET", `/sitemeta?url=${encodeURIComponent(url)}`),
  // weather
  weather: (location, units, days) =>
    request("GET", `/weather?location=${encodeURIComponent(location)}&units=${units}&days=${days}`),
  // changes made outside the app (e.g. by Claude through the MCP server)
  revision: () => request("GET", "/revision"),
  // authentication
  onUnauthorized: (fn) => {
    onUnauthorized = fn;
    unauthorizedPending = false;
  },
  authStatus: () =>
    request("GET", "/auth/status").finally(() => {
      unauthorizedPending = false;
    }),
  login: (password) => request("POST", "/auth/login", { password }),
  logout: () => request("POST", "/auth/logout"),
  setPassword: (current, next) => request("POST", "/auth/password", { current: current || undefined, new: next }),
  removePassword: (current) => request("DELETE", "/auth/password", { current }),
  setGuestAccess: (guest_access) => request("PATCH", "/auth/settings", { guest_access }),
  listTokens: () => request("GET", "/auth/tokens"),
  createToken: (name) => request("POST", "/auth/tokens", { name }),
  revokeAllTokens: () => request("DELETE", "/auth/tokens"),
  deleteToken: (id) => request("DELETE", `/auth/tokens/${encodeURIComponent(id)}`),
  // page templates and single-page sharing (signed-in writers only)
  listTemplates: () => request("GET", "/templates"),
  getTemplate: (id) => request("GET", `/templates/${encodeURIComponent(id)}`),
  applyTemplate: (id, opts = {}) => request("POST", `/templates/${encodeURIComponent(id)}/apply`, opts),
  exportPage: (id) => request("GET", `/dashboards/${encodeURIComponent(id)}/export`),
  importPage: (body) => request("POST", "/dashboards/import", body), // {page, name?, service_mode?, integration_map?}
  previewImport: (body) => request("POST", "/dashboards/import/preview", body),
  // config
  exportConfig: () => request("GET", "/config/export"),
  importConfig: (bundle, replace) => request("POST", `/config/import?replace=${replace ? "true" : "false"}`, bundle),
};
