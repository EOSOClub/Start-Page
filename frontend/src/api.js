const BASE = "/api";

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
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
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
  saveLayout: (id, items) => request("PUT", `/dashboards/${id}/layout`, items),
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
  // health
  health: () => request("GET", "/health"),
  checkHealth: (serviceId) => request("POST", `/health/${serviceId}/check`),
  // integrations
  listIntegrations: () => request("GET", "/integrations"),
  createIntegration: (data) => request("POST", "/integrations", data),
  updateIntegration: (id, data) => request("PATCH", `/integrations/${id}`, data),
  deleteIntegration: (id) => request("DELETE", `/integrations/${id}`),
  integrationData: () => request("GET", "/integrations/data"),
  refreshIntegration: (id) => request("POST", `/integrations/${id}/refresh`),
  dockerAction: (id, container, action) => request("POST", `/integrations/${id}/docker/${container}/${action}`),
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
  // config
  exportConfig: () => request("GET", "/config/export"),
  importConfig: (bundle, replace) => request("POST", `/config/import?replace=${replace ? "true" : "false"}`, bundle),
};
