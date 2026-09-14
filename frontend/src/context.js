import { createContext, useContext } from "react";

/**
 * App-wide values widgets may need without prop drilling:
 *   settings, services, dashboards, links, health, editMode,
 *   goToPage(id), updateWidgetConfig(id, config, {keepalive})
 */
export const AppContext = createContext({ settings: {}, services: [], dashboards: [], links: [], health: {} });

export const useApp = () => useContext(AppContext);
