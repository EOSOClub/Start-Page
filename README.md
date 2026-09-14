# Start Page

A self-hosted, fully configurable dashboard / start page. Nothing is hardcoded:
services, pages and widgets are all managed from the UI and stored in SQLite.

- **Freeform grid canvas** – drag and resize widgets anywhere; nothing snaps to
  neighbours or auto-rearranges (`compactType=null`, overlap allowed).
- **Service registry** – define each service once (host, port, protocol, icon,
  category, health check). Widgets *reference* services, so changing an IP in one
  place updates every tile, group and table that uses it.
- **Widget types** – Link, Bookmarks (many links in one box), Search bar, Greeting,
  Clock, Weather (Open-Meteo, no API key), Notes / to-do (autosaves as you type),
  Service (with live health dot + latency), Group (by category or hand-picked),
  Service table, Text/Markdown (sanitized), Iframe, Image.
- **Site icons** – links without an icon show the site's favicon, fetched and cached by
  the backend (`data/favicons/`), so LAN services work and no third party sees your links.
  In edit mode, paste a URL anywhere to add it as a Link (the page title becomes its name).
- **Fast new tabs** – the last-known layout and theme are cached in `localStorage`, so the
  page paints instantly and refreshes from the API in the background (it still shows the
  saved copy when the server is down). Polling pauses in hidden tabs; hashed assets are
  served gzipped with long-lived cache headers.
- **Cross-site protection** – state-changing API calls must carry an `X-Requested-By`
  header and not be cross-site, so websites you visit can't modify the page or control
  Docker through it. No CORS by default; allow extra origins with `STARTPAGE_CORS_ORIGINS`.
- **Multiple pages**, edit mode (`E` toggles, `Esc` exits), import/export of the
  whole config as JSON.
- Background health checker in the backend (HTTP, TCP connect or ICMP ping, per-service interval).
- **Integrations** – the backend polls Docker (socket or TCP API), Uptime Kuma status
  pages and arbitrary JSON endpoints; widgets: Docker (containers + start/stop/restart),
  Uptime Kuma (monitors + heartbeat bars), Custom status (pick fields out of any JSON).
- **Service dependencies** (`depends_on`) and a Topology widget that draws the tree.
- **Themes**: dark/light/system, accent colour, font, background image with dim/blur
  (global or per page), widget opacity.
- **Command palette** (`/` or `Ctrl+K`): fuzzy-search services, links and pages; falls
  back to opening a URL or a web search. Icon picker searching 1,600+ dashboard-icons.
- **Automatic daily JSON backups** with one-click restore, plus manual import/export.

## Stack

`backend/` FastAPI + SQLAlchemy + SQLite · `frontend/` React 18 + Vite + react-grid-layout

## Run (development)

```powershell
cd backend;  pip install -r requirements.txt;  python -m uvicorn app.main:app --reload --port 8000
cd frontend; npm install;                      npm run dev      # http://localhost:5173 (proxies /api)
```
or just `.\dev.ps1` to open both.

## Run (production / single process)

```powershell
cd frontend; npm run build        # emits to backend/static
cd ../backend; python -m uvicorn app.main:app --port 8000
```
Open http://localhost:8000 — the backend serves the built frontend.

## Run (Docker)

```bash
docker compose up -d --build      # http://localhost:8000, data persisted in ./data
```

## Usage

1. Click **Edit** (or press `E`).
2. **Services** → add your services (IP/host, port, icon, category). Icons accept an
   emoji, an image URL, or a [dashboard-icons](https://github.com/walkxcode/dashboard-icons) name like `plex`.
3. **+ Widget** → pick a type; Service/Group widgets reference registry services.
4. Drag widgets by their body, resize with the corner/edge handles, ⚙ or
   double-click to configure. Positions are saved as grid cells (x, y, w, h),
   not pixels, so they survive resolution changes.
5. **Page settings** to rename a page or change its grid density; `+` in the tab
   bar adds pages.
6. **Import / export** to back up or restore `startpage.json`.

## Integrations

Edit → **Integrations** → New:

| Type | Config | Notes |
|---|---|---|
| Docker | `unix:///var/run/docker.sock` or `http://host:2375` | Mount the socket into the container (see `docker-compose.yml`) or expose the TCP API / a socket proxy. Start/stop/restart buttons are optional per widget. |
| Uptime Kuma | Kuma URL + status-page slug | Uses Kuma's public status-page API, so no API key is needed — add monitors to a status page in Kuma. |
| JSON | Any URL (+ optional headers) | The *Custom status* widget maps `Label = dot.path` lines onto the response, e.g. `Players = players.online`. |

## Keyboard shortcuts

`/` focus the Search bar widget (or open search) · `Ctrl+K` search · `E` edit mode · `Esc` deselect / leave edit / close · `1–9` switch page · `Ctrl+Z` undo · `Ctrl+Shift+Z` / `Ctrl+Y` redo · `?` help

Edit mode: `S` services · `W` add widget · `Ctrl+V` paste a URL as a link · click a widget to
select it, then `Arrows` nudge (by the snap step) · `Shift+Arrows` resize · `Enter` settings ·
`Ctrl+D` duplicate · `Delete` delete. Drag page tabs to reorder, double-click to rename.

Undo covers layout changes, adding/editing/deleting/duplicating widgets, moving or copying
them to another page (widget settings → *To page…*), and page renames, reordering, settings
and deletion. History lasts until the page is reloaded. Typing in Notes isn't part of it.

On windows narrower than 720 px, view mode stacks widgets in reading order instead of
shrinking the grid (dividers and frames are hidden there); edit mode keeps the grid.

Search bar *Focus when the page opens* works when the start page is your homepage; on a new
tab most browsers keep focus in the address bar, so press `/`.

## API

Interactive docs at `/docs`. Key routes:

| Route | Purpose |
|---|---|
| `GET/POST /api/dashboards`, `PATCH/DELETE /api/dashboards/{id}` | pages |
| `PUT /api/dashboards/{id}/layout` | bulk save positions after drag/resize |
| `POST /api/dashboards/{id}/widgets`, `PATCH/DELETE /api/widgets/{id}`, `POST /api/widgets/{id}/duplicate` | widgets |
| `GET/POST /api/services`, `PATCH/DELETE /api/services/{id}` | service registry |
| `GET /api/health`, `POST /api/health/{id}/check` | cached status / force check |
| `GET /api/config/export`, `POST /api/config/import?replace=` | JSON backup |
| `GET/POST /api/integrations`, `GET /api/integrations/data`, `POST /api/integrations/{id}/refresh` | integrations + cached data |
| `POST /api/integrations/{id}/docker/{container}/{start\|stop\|restart}` | Docker actions |
| `GET/PATCH /api/settings` | global UI settings |
| `GET/POST /api/backups`, `POST /api/backups/{name}/restore` | server-side backups (`data/backups/`) |
| `GET /api/icons?q=` | dashboard-icons search |
| `GET /api/favicon?url=`, `GET /api/sitemeta?url=` | cached site icon / page title |
| `GET /api/weather?location=&units=&days=` | Open-Meteo current weather + forecast (15 min cache) |

All `POST/PUT/PATCH/DELETE` calls need the header `X-Requested-By: startpage` (e.g. for scripts using `curl`).

## Adding a widget type

Add an entry to `WIDGET_TYPES` in `frontend/src/widgets/index.jsx` with a `label`,
default `size`, a `fields` schema (drives the settings form automatically) and a
`Render` component. No backend changes needed — widget config is free-form JSON.
Fields may declare a `default` (seeded into new widgets). Renderers can call `useApp()`
(`src/context.js`) for settings, services, links, `goToPage` and `updateWidgetConfig`
(for widgets that edit themselves, like Notes).

## Data

`backend/data/startpage.db` plus `backend/data/backups/*.json` (override the directory with
`STARTPAGE_DATA_DIR`). Delete the DB to start fresh; existing databases are migrated
automatically on startup.
