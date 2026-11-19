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
  Service table, Topology, Uptime, Incidents, Text/Markdown (sanitized), Iframe, Image,
  Countdown, Heading / Divider / Box for layout, a sandboxed Custom (HTML/JS) widget,
  and the integration widgets below.
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
  It also applies to API tokens. Optional password login on top, see [Authentication](#authentication).
- **Multiple pages**, edit mode (`E` toggles, `Esc` exits), import/export of the
  whole config as JSON.
- Background health checker in the backend (HTTP, TCP connect or ICMP ping, per-service interval).
- **Health history and notifications** – every check is recorded (7 days by default) and
  every state change goes into an incident log (90 days). Service tiles can show 24 h
  uptime and a latency sparkline; the Uptime and Incidents widgets chart them. ntfy,
  Discord or a generic webhook can alert you when a service goes down or recovers.
- **Integrations** – the backend polls Docker (socket or TCP API), Uptime Kuma status
  pages, Pi-hole, Prometheus, RSS, the host machine and arbitrary JSON endpoints; widgets:
  Docker (containers + start/stop/restart), Uptime Kuma (monitors + heartbeat bars), Pi-hole
  (stats + blocking switch), Prometheus, Custom status (pick fields out of any JSON), and
  generic *Integration stats* / *Integration list* widgets for any type that returns them.
  Fifteen more types (Sonarr, Radarr, qBittorrent, SABnzbd, Jellyfin, Plex, Proxmox, TrueNAS,
  Portainer, Unraid, AdGuard Home, UniFi, Speedtest Tracker, Home Assistant, ICS calendars)
  each have a ready-made widget in the **Integrations** section of the *+ Widget* menu
  (filter box, `Shift+W` opens it). Writers get pause/resume (qBittorrent, SABnzbd), a
  protection switch (AdGuard Home, like Pi-hole), switches and buttons for Home Assistant
  entities, and optional per-row start/shut down/restart menus (Proxmox guests, Portainer
  containers) that always ask for confirmation.
- **Calendar, Countdown and Gauge widgets** – an agenda of one or more ICS feeds grouped by
  day (time zone aware, with events in progress marked); a countdown (or count-up) to a date
  or time, optionally repeating yearly for birthdays; a gauge (arc, bar or number) for one
  stat of any integration, with warn/bad thresholds.
- **Service dependencies** (`depends_on`) and a Topology widget that draws the tree.
- **Themes**: dark/light/system, accent colour, font, background image with dim/blur
  (global or per page), widget opacity.
- **Command palette** (`/` or `Ctrl+K`): fuzzy-search services, links and pages; falls
  back to opening a URL or a web search. Icon picker searching 1,600+ dashboard-icons.
- **Automatic daily JSON backups** with one-click restore, plus manual import/export.
- **Also**: [page templates and shareable page files](#page-templates-and-sharing-a-page), a
  [phone layout](#phones), an [installable offline-capable app](#install-as-an-app), service
  discovery from Docker labels ([Service discovery](#service-discovery)) and an optional
  [password](#authentication).

## Stack

`backend/` FastAPI + SQLAlchemy + SQLite · `frontend/` React 19 + Vite + react-grid-layout

## Run (development)

```powershell
cd backend;  pip install -r requirements.txt;  python -m uvicorn app.main:app --reload --port 8471 --no-proxy-headers
cd frontend; npm install;                      npm run dev      # http://localhost:5173 (proxies /api)
```
or just `.\dev.ps1` to open both.

## Run (production / single process)

```powershell
cd frontend; npm run build        # emits to backend/static
cd ../backend; python -m uvicorn app.main:app --port 8471 --no-proxy-headers
```
Open http://localhost:8471 — the backend serves the built frontend. Keep `--no-proxy-headers`
(the Docker image sets it): see Authentication below.

## Run (Docker)

```bash
docker compose up -d --build      # http://localhost:8471, data persisted in ./data
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

## Build your page with Claude Code

With the app running, open [Claude Code](https://claude.com/claude-code) in this folder and
describe what you want, for example: *"add a clock, London weather, and a row of links to my
Jellyfin and Home Assistant services"*.

- The repo's `.mcp.json` registers the `startpage` MCP server (`mcp/startpage_mcp.py`). Approve it
  when Claude Code asks on first launch.
- The server needs [uv](https://docs.astral.sh/uv/), which installs its dependencies on first run.
- It talks to `http://localhost:8471` (change `STARTPAGE_URL` in `.mcp.json` for another host).
  In development that's the backend started by `dev.ps1`.
- Claude can read pages, add, configure, move and delete widgets, manage pages, services and
  global settings, and take backups.
- Open tabs show its changes within about 3 s. Your own undo history is cleared at that point,
  because it would otherwise undo against stale state.
- Restore from **Backups** if you don't like a result. Claude takes a backup before destructive changes.

### Screenshots

Claude can look at its work with `screenshot_page` (a whole page, at any width; `width=390` shows the
phone layout, `theme` forces light or dark for that shot only) and `screenshot_widget`. This needs a
Chromium-family browser (Chrome, Edge, Brave or Chromium) on the machine that runs Claude Code. It is
found automatically; set `STARTPAGE_BROWSER` to its path in `.mcp.json` otherwise. The browser is
headless, uses a throwaway profile and is closed afterwards (also when the MCP server is killed: it is
driven over a private pipe, not a network port, so no other local process can attach to it, and it exits
when the pipe closes). With authentication on, it works through `STARTPAGE_TOKEN`: the token is added only
to `/api/` reads that the page itself makes to `STARTPAGE_URL`, never to a widget's own frame, and never
put in a URL, on disk or in the browser's environment. The session is view-only (no writes). Other sites
can only supply images, fonts, stylesheets and media, fetched without credentials; their pages, iframes,
scripts, requests and WebSockets are blocked. A local-network host can therefore receive a credential-free
GET for an image or font that a page asks for.

## Authentication

Off by default: with no configuration anyone who can reach the page has full access, as before.
Turn it on before exposing the page beyond your LAN. **The app serves plain HTTP. TLS must come
from a reverse proxy** (Caddy, Traefik, nginx...); without it the password and session cookie
cross the network in clear text.

- **Enable**: Edit mode -> More -> Settings & theme -> **Security** -> set a password (8+
  characters). Authentication turns on at once and you stay signed in. Sessions are HttpOnly
  `sp_session` cookies (SameSite=Lax, `Secure` over HTTPS) that last 30 days from last use and
  never more than 90 days in total. The password must not be only whitespace.
  Setting the **first** password wipes any API tokens and sessions that already exist and resets
  guest access to *Nobody*: nothing created while authentication was off carries over.
  Removing the password (or `python -m app.auth reset`) likewise deletes all tokens and sessions
  and resets guest access to *Nobody*.
- **Login throttle**: five attempts per address, then a doubling wait (2, 4, 8 ... capped at
  60 s; HTTP 429 with `Retry-After`; attempts made while blocked do not extend the block).
  IPv6 clients count per /64. If more than 30 attempts arrive across all addresses within a
  minute, logins are slowed to one every 5 s for everyone. State is in memory only: a restart
  clears it. Behind a reverse proxy set `STARTPAGE_TRUSTED_PROXIES` to the proxy's address;
  otherwise every visitor shares the proxy's address and one attacker throttles everyone
  (the server logs a warning, at most once a minute, when the throttle engages).
- **Guest access** (same tab): *Nobody* shows a login screen to everyone else. *View only* lets
  anyone look at the page but not change anything: no editing, no Docker buttons or checks, no
  exports or backups, and integration keys and the notification URL are hidden. Guests only
  get site icons and weather that are already cached; page-title lookups are refused.
- **API tokens** (same tab): for scripts and the Claude MCP server. Only available once a
  password is set (before that, creating a token or changing guest access answers 409 "Set a
  password first"). Send `Authorization: Bearer sp_...`; writes still need an `X-Requested-By`
  header. For Claude, put the token in your environment as `STARTPAGE_TOKEN` (the repo's
  `.mcp.json` passes it through). Changing the password signs out other browsers but keeps tokens
  (they are separate credentials): use **Revoke all tokens** (or `DELETE /api/auth/tokens`) to
  drop them.
- **Behind an auth proxy** (Authelia, Authentik, oauth2-proxy...): set
  `STARTPAGE_TRUSTED_PROXIES` (comma-separated IPs/CIDRs of the proxy as the app sees it) and
  `STARTPAGE_AUTH_HEADER` (e.g. `Remote-User`). A request from a trusted address carrying that
  header is signed in as that user; the header is ignored from every other address. Setting
  both turns authentication on even without a password. When the direct peer is in
  `STARTPAGE_TRUSTED_PROXIES`, the app reads `X-Forwarded-For` (the nearest entry that is not
  itself a trusted proxy is the client, used for the login throttle) and `X-Forwarded-Proto`
  (for the cookie's `Secure` flag); from any other peer both headers are ignored. This is the
  only place forwarded headers are trusted, so **uvicorn must run with `--no-proxy-headers`**
  (the Dockerfile and `dev.ps1` do) and you must not enable `--proxy-headers` or set
  `FORWARDED_ALLOW_IPS`: uvicorn would then rewrite the peer address from a header the client
  controls before the app can check it. Make sure the app is not reachable except through the
  proxy.
- **Locked out?** Set `STARTPAGE_AUTH_DISABLE=1` and restart (enforcement off, nothing deleted),
  or run `python -m app.auth reset` from `backend/` (in Docker:
  `docker compose exec startpage python -m app.auth reset`), which removes the password, all
  sessions and all tokens.
- The password is stored as a salted scrypt hash in the database, separately from the settings,
  so it is never part of an export or backup. Single user, single password.

## Integrations

Edit → **Integrations** → New:

| Type | Config | Notes |
|---|---|---|
| Docker | `unix:///var/run/docker.sock` or `http://host:2375` | Mount the socket into the container (see `docker-compose.yml`) or expose the TCP API / a socket proxy. Start/stop/restart buttons are optional per widget. |
| Uptime Kuma | Kuma URL + status-page slug | Uses Kuma's public status-page API, so no API key is needed — add monitors to a status page in Kuma. |
| JSON | Any URL (+ optional headers) | The *Custom status* widget maps `Label = dot.path` lines onto the response, e.g. `Players = players.online`. |
| System | none | CPU, memory and disk of the machine the backend runs on (psutil). |
| RSS / Atom | Feed URL | Headlines; the *RSS / Atom* widget. |
| Pi-hole | URL + password (v6) or API token (v5) | Queries today, blocked, percent blocked, domains on the blocklist, blocking status. v5 and v6 are detected automatically (or set `version`). The *Pi-hole* widget can switch blocking on, off, or off for 5 minutes (writers only). |
| Prometheus | URL, optional basic auth or bearer token, `queries` (`Label = promql \| unit` per line), optional `range_minutes` | The first sample of each instant query becomes a stat; with `range_minutes` a `query_range` also draws a sparkline. Use the *Prometheus* or *Integration stats* widget. |
| Sonarr | URL + API key, `list` (upcoming episodes or download queue) | Queue, missing, episodes in the next 7 days, series count. *Sonarr* widget: stats row above the list. |
| Radarr | URL + API key, `list` (upcoming movies or download queue) | Queue, missing, movies, releases in the next 7 days. *Radarr* widget. |
| qBittorrent | URL, user name + password (empty when auth is bypassed) | Speeds, torrent counts, active torrents. *qBittorrent* widget: Pause all (asks to confirm) / Resume all for writers. v4 and v5 Web API. |
| SABnzbd | URL + API key | Speed, remaining size and time, queue, paused flag, queued jobs. *SABnzbd* widget: Pause (asks to confirm) / Resume for writers. |
| Jellyfin | URL + API key | Streams, transcodes, library counts; the list is the sessions now playing, with progress. Emby uses the same API and should work too. *Jellyfin* widget. |
| Plex | URL + `X-Plex-Token`, optional library counts | Streams, transcodes, sessions now playing with progress. *Plex* widget. |
| Proxmox VE | URL + API token (token id + secret), `list` (guests, nodes or storage), optional node filter | Nodes online, VMs / LXCs running, CPU, memory and storage use. Tick *show actions* in the *Proxmox* widget for a per-guest start / shut down / restart / force stop menu (writers only, confirmation required; the token needs `VM.PowerMgmt`). Guest rows carry a stable `ref` `{node, vmid, kind}`, so the menu works on the VMs-and-containers list only. |
| TrueNAS | URL + API key, `list` (pools or alerts) | Pool health, usage, alerts, uptime, load. **REST only:** TrueNAS 25.04 deprecates the REST API in favour of WebSocket JSON-RPC and a later release removes it, so this type needs a release that still serves `/api/v2.0`. |
| Portainer | URL + API key, optional environment id, `list` (containers, environments or stacks) | Environments up, running / stopped / unhealthy containers, stacks. Tick *show actions* in the *Portainer* widget for start / stop / restart menus (writers only, confirmation required). Each container row carries a `ref` `{endpoint_id, container_id}` naming the environment it was listed from, so no environment id has to be set here; the menu works on the containers list only. |
| Unraid | URL + API key, `list` (disks, containers or VMs) | Array state and usage, parity, disk errors, containers, VMs, CPU, memory through the Unraid GraphQL API. **Needs Unraid 7.2+, or the Unraid Connect plugin on older 7.x.** |
| AdGuard Home | URL, user name + password | Queries, blocked, percent blocked, average time, protection status, top blocked domains. The *AdGuard Home* widget switches protection on, off, or off for 5 minutes (writers only). |
| UniFi | URL, user name + password or API key, site, `variant` (auto, UniFi OS or legacy), `list` (devices or clients) | WAN status and throughput, clients, adopted / offline devices, latency. Read-only. |
| Speedtest Tracker | URL + API token (the legacy 0.x API needs none) | Last download, upload and ping with sparklines, time of the last test. *Speedtest Tracker* widget. |
| Home Assistant | URL + long-lived access token, `entities` (one `entity_id` or `Label = entity_id` per line, at most 50) | States of the listed entities. The *Home Assistant* widget shows switches, press buttons and read-only values. **Actions work only on entities you list, and only for light, switch, input_boolean, fan, cover, scene, script, button, automation and media_player**; locks and alarms can never be controlled. |
| ICS calendar | Feed URL (`https://` or `webcal://`), optional basic auth, `days_ahead`, `max_events`, `timezone` | Upcoming events for the *Calendar agenda* widget (pick one or several feeds); stats for today, this week and the next event. Recurring events support the common `RRULE` forms (daily, weekly, monthly, yearly with `INTERVAL`, `COUNT`, `UNTIL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH`, `BYSETPOS`, `EXDATE`, overrides); `RDATE`, `BYWEEKNO`, `BYYEARDAY`, `BYHOUR`/`BYMINUTE`/`BYSECOND`, `WKST`, hourly-or-finer frequencies and `VTIMEZONE` definitions are not supported. |

**These fifteen integrations were written from API documentation knowledge and tested against
recorded fixtures only, not against live servers.** Expect to adjust field details on the first
real run, and use each type's **Test** button to see what comes back.

Every type has a **Test** button that runs one fetch without saving. Types that return the
common `stats` / `items` shape work with the *Integration stats* and *Integration list* widgets.
TLS certificates are verified by default for new integrations. For a self-signed or private-CA
homelab host, paste its certificate (PEM) into *Trusted CA certificate*: it is trusted for that
integration only, alongside the system's CAs, and the host name must still match the certificate.
Unticking *Verify TLS certificate* turns checking off altogether (integrations that existed before
this default keep verification off).

**Secrets are write-only.** API keys, passwords and the JSON type's headers read back as
`********` (or `""` when unset) from `GET /api/integrations` and the create/update responses, for
everyone. Sending the mask back, or leaving the key out, keeps the saved value; `""` clears it; a
new value replaces it. Exports and backups keep the real values (so a restore works); an import
never stores the mask. View-only guests get an empty `config` and only "Unavailable" instead of
error details; everyone else sees a short message ("HTTP 401 Unauthorized (host:port)",
"timeout", "connection refused", ...) never a URL, path or key.

## Service discovery

**Services → Discover** (shown once a `docker` integration exists) lists your containers as
candidate services. Nothing is added or changed until you tick rows and press **Import**; you can
edit name, category and URL parts first. Over MCP: `discover_services` / `import_discovered_services`.

Each candidate is derived from its own container, in this priority order:

1. **Labels.** `startpage.*` first, then the Homepage labels as a fallback.
2. **Traefik** router rules (`Host(...)`, https when the router has TLS or a `websecure` entrypoint;
   the port is left empty because the proxy serves 80/443).
3. **Published TCP ports**: the image's known web port (a built-in map of about 60 image names of
   common homelab apps: Plex, Jellyfin, Sonarr, Radarr, Portainer, Pi-hole, Grafana, Uptime Kuma, ...), else a
   well-known web port (80, 443, 8080, 3000, ...), else the lowest published port (confidence
   `guess`). `host` network mode only works for images in the map.

| Label | Meaning |
|---|---|
| `startpage.name` (`homepage.name`) | display name; default: compose service, else container name, title-cased |
| `startpage.url` (`homepage.href`) | full `http(s)://` URL; wins over the four parts below |
| `startpage.protocol`, `.host`, `.port`, `.path` | URL parts (`http`/`https`, hostname or IPv4, 1-65535, path) |
| `startpage.icon` (`homepage.icon`) | dashboard-icons name, emoji, `http(s)` URL or `/path`; default: the image name when it exists in dashboard-icons |
| `startpage.category` / `.group` (`homepage.group`) | category; default: compose project |
| `startpage.description` (`homepage.description`) | description |
| `startpage.health` | `http`, `tcp`, `ping` or `off` (default: an HTTP check on the URL) |
| `startpage.enable` | `false` leaves the container out |

Traefik: `traefik.http.routers.<name>.rule` with `Host(`a.example`)`, `Host(`a`) || Host(`b`)`,
`Host(`a`, `b`)` (the first host is used) and an optional `&& PathPrefix(`/x`)`. Routers without a
`Host` are ignored; `traefik.enable=false` skips Traefik.

```yaml
services:
  jellyfin:
    image: jellyfin/jellyfin
    labels:
      startpage.name: Jellyfin
      startpage.group: Media
      startpage.url: https://jellyfin.example.com
      startpage.health: http
      # or let Traefik say where it lives:
      traefik.http.routers.jellyfin.rule: Host(`jellyfin.example.com`)
      traefik.http.routers.jellyfin.tls.certresolver: letsencrypt
```

**Host.** For a Docker integration with an `http(s)://` URL the host is that URL's host. Over the
unix socket, containers are published on the machine running the app: the host is the
`default_host` query parameter, else the name you opened the page with. Check it before importing.

**Left out.** Databases, caches, brokers, Watchtower, the start page itself, containers with no
published TCP port and no labels, and `startpage.enable=false` are listed under *Skipped* with a
reason. An explicit `startpage.*` / `homepage.*` label overrides the exclusion by image name.

**Matching and updates.** A candidate matches an existing service when its host and port match (a
missing port means 80/443 by protocol), otherwise when the container name or the derived name
equals the service name (case-insensitive). A match is shown under "Already in your services", with
`changes` as `{field: [current, proposed]}`. Only fields with real evidence (labels, Traefik, or
the URL labels) are proposed; guesses (icon, category from the compose project, an address derived
from ports) never overwrite anything. Updates are unchecked by default, as are `guess` rows, and
an update sends only the fields you leave in.

**Import** is per item: an item that fails validation (protocol other than http/https, bad host,
port outside 1-65535, ...) is reported in `errors` and the others still go through; the valid ones
are saved in one transaction.

**Security.** Discovery reveals your infrastructure and makes the server talk to Docker, so
`/api/discovery/*` is for signed-in writers only: view-only guests get 401, including on the GET.
Label values are untrusted: they are validated, length-capped, and never leave their container.

## Notifications

Edit → **Settings** → **Notifications**. Pick a type and paste its URL:

| Type | URL | Sent as |
|---|---|---|
| ntfy | `https://ntfy.sh/my-topic` (or your own server) | plain-text body with `Title`, `Priority` and `Tags` headers |
| Discord | a channel webhook URL | JSON `{"content": ...}` |
| Webhook | any `http(s)` URL | JSON `{event, service: {id, name, url}, status, previous, error, at}` |

A service alerts once after *N* consecutive failed checks (default 2, so a single blip
is ignored), and once more when it is back online if "notify when it recovers" is on.
**Send test** saves the notification settings first, then sends.

- **Failures.** An offline result, or an HTTP 5xx answer (shown as *degraded*), counts as a
  failed check. Any other degraded result resets the counter, sends no alert and is not a
  recovery. Uptime percentages are unchanged (degraded still counts as up) and are floored to
  two decimals, so only a perfect record reads 100.
- **Manual checks** (the check button, `POST /api/health/{id}/check`) are recorded in the
  history but never count towards *N* and never notify. One per service per 5 seconds; inside
  that window the cached status is returned.
- **Delivery.** One worker sends alerts one at a time, with up to 3 attempts, exponential
  backoff and `Retry-After` honoured on HTTP 429 (waits are capped at 30 s). A service counts
  as notified only after a successful send; otherwise it stays pending and is retried on a
  later failed check, at most once a minute. The queue holds 50 messages (oldest dropped, with
  one log line).
- **Batching.** All alerts produced by one check round go out as one message ("3 services
  down: A, B, C"). The generic webhook then receives
  `{"event": "batch", "events": [{event, service, status, previous, error, at}, ...], "at"}`;
  a single alert keeps the single-event shape above.
- **Restarts.** Failure counts and "already notified" state are stored in the database
  (`health_alert_state`): a restart during an outage does not repeat the "down" alert, and a
  recovery that happens while the app is down is still announced once.
- **The notification URL is write-only.** It is a secret (a webhook URL grants posting rights).
  `GET`/`PATCH /api/settings` return `"notify_url": "********"` plus `"notify_url_set": true`
  when one is saved, and `""` / `false` otherwise (for everyone, guests included; the MCP
  server sees the same). Sending the mask back, or omitting the key, keeps the saved URL;
  `""` clears it; a new URL replaces it.
- **Exports and backups contain secrets** (the webhook URL, integration keys): store them
  accordingly. The notification URL is kept unmasked in them so a restore brings alerts back;
  an import never stores the mask itself. History, incidents and alert state are not part of
  exports; a *replace* import keeps the history of every service whose id is in the file and
  removes only the services that are not.
- Retention of history and the incident log is set in the same tab.

## Keyboard shortcuts

`/` focus the Search bar widget (or open search) · `Ctrl+K` search · `E` edit mode · `Esc` deselect / leave edit / close · `1–9` switch page · `Ctrl+Z` undo · `Ctrl+Shift+Z` / `Ctrl+Y` redo · `?` help

Edit mode: `S` services · `W` add a Link widget · `Shift+W` open the widget menu · `Ctrl+V` paste a
URL as a link · click a widget to select it, then `Arrows` nudge (by the snap step) ·
`Shift+Arrows` resize · `Enter` settings · `Ctrl+D` duplicate · `Delete` delete. Drag page tabs to reorder, double-click to rename.

Undo covers layout changes, adding/editing/deleting/duplicating widgets, moving or copying
them to another page (widget settings → *To page…*), and page renames, reordering, settings
and deletion. History lasts until the page is reloaded. Typing in Notes isn't part of it.

Search bar *Focus when the page opens* works when the start page is your homepage; on a new
tab most browsers keep focus in the address bar, so press `/`.

## Phones

Below 720 px wide, view mode shows the page's **phone layout**. Every page has one of two:

- **Automatic** (the default): widgets stack in reading order (top to bottom, then left to
  right) in a 4-column flow. Small widgets take half the width, the rest the full width;
  dividers and frames are hidden. Nothing to set up.
- **Custom**: a real grid with its own columns (*Phone grid columns* in page settings, 1-12,
  default 4) and its own position and size for every widget. Widgets you haven't placed yet
  are appended below in the automatic order, so a widget you add later still shows up.

To edit it, enter edit mode and press **Phone** next to **Desktop** in the toolbar (on a
phone-sized window edit mode opens in Phone mode). The page is drawn in a 390 px phone frame
and dragging/resizing changes only the phone layout; the desktop layout is never touched.

- On an automatic page the frame previews the stack and offers **Start from the automatic
  layout**: it gives every widget a phone position that reproduces what phones show today,
  and switches the page to custom. Dividers and frames start out hidden.
- **Hide on phone** (button on each widget in Phone mode) keeps a widget off phones; hidden
  widgets stay in the frame, dimmed, with **Show on phone** to bring them back.
- **Reset to automatic** drops the phone layout and goes back to stacking.
- Widgets added in Desktop mode have no phone position; they are appended at the bottom of
  the phone layout until you place them. Duplicate, move/copy to another page, export/import
  and backups all carry the phone layout; bundles from older versions import as before.
- Undo/redo covers all of it (start, reset, hide/show, moves) and keeps the two layouts apart:
  undoing a phone move never changes the desktop layout.

Through the API/MCP: `PUT /api/dashboards/{id}/layout?breakpoint=sm` saves phone positions
(`{id, x, y, w, h, hidden?}`; `x + w` must fit `columns_sm`), `DELETE` on the same URL clears
them, and the page settings are `phone_layout` (`auto` | `custom`) and `columns_sm`. A widget's
`layout_sm` is `{x, y, w, h, hidden}` or `null`. The desktop call without `breakpoint` is
unchanged.

## Install as an app

Start Page is an installable web app (PWA): Chrome/Edge/Android offer **Install**
(*Settings → Behaviour* has the button, once the browser offers it), iOS Safari uses
*Share → Add to Home Screen*. **Installing and offline support need HTTPS** (or
`http://localhost`); over plain `http://` on another host the browser refuses both, and the
rest of the app works as always. Put it behind a reverse proxy with a certificate to use it
on your phone.

- The manifest is generated at `/manifest.webmanifest` from your settings: name from *Title*,
  theme colour from the accent colour, background from the background colour (or the theme's
  default). It is open without login, but with authentication on and guest access *none*
  anonymous callers get a generic "Start Page" manifest, so nothing leaks.
- Offline: the app shell and its scripts/styles are cached, so the app opens without the
  server and paints the last page it saw from its local copy (the same copy new tabs use).
  Live data (health, integrations, weather) and saving need the server; **nothing from
  `/api` is ever cached by the service worker** and nothing is cached for non-GET requests.
  Signing out clears the local copy as before; the worker only holds the public app shell.
- Only `/` and `/index.html` are the worker's business. Other paths on the same host (another
  app behind the same reverse proxy, such as `/grafana/`) are never answered or cached by it.
  A page is stored as the shell only when it carries the app's own build marker, so a proxy
  error page or an SSO login page can never become the offline shell. When the proxy answers
  502/503/504 (the container is down), the cached shell opens instead of the error.
- With **proxy/SSO authentication** in front, the local copy is not painted until the server
  confirms the session: offline, the app shows "Can't reach the server" instead of your pages.
  With a Start Page password or no authentication, an offline start still paints the last
  saved copy (page names, service names and addresses). That copy lives in this browser's
  storage, as it always has; sign out (or clear site data) on a shared device.
- **Not supported: serving the app under a sub-path** such as `/startpage/`. `/sw.js`,
  `/assets`, `start_url` and `scope` are absolute paths from the root of the host. Use a
  host name (`start.example.com`) for the proxy instead.
- Updates: the cache is named after the build, so a new version takes over at once and the old
  cache is deleted. Open pages keep running the old code until they reload; a small
  "Updated - reload" toast offers that, it never reloads by itself (and it is not shown on the visit
  that already loaded the new version).
- **Kill switch:** set `STARTPAGE_DISABLE_SW=1` (docker compose `environment:`) and restart.
  `/sw.js` then answers 404, and every browser that opens the page unregisters its worker and
  deletes the app's caches on the next load. Remove the variable to turn it back on.

## Page templates and sharing a page

The **+** in the tab bar (edit mode) opens *New page*: a blank page, a **template**, or a page
**file**. Templates: `home`, `media`, `network`, `infrastructure`, `monitoring`, `smart-home`
(files in `backend/app/templates/`). Before anything is created you see what will happen: the
services that will be created or matched to yours, and for each integration the page needs a
picker of your existing integrations of that type, or *Set up later* (the widget then shows its
normal "select an integration" state). The new page is always a **new** page; undo deletes it.
The templates use the service categories (for example *Media*) instead of fixed services.

*Page settings > Export page* downloads `<name>.startpage-page.json`. The file is page content
only: widgets refer to services and integrations by a local `ref`, never by database id, and an
integration is only `{ref, type, name}`: **its settings and keys are never exported**. Service
entries are included, with secret-looking parts of their addresses removed. Widget settings that
look secret (an address with `user:pass@` or `?token=`) are exported as they are but listed as
warnings first, so you can remove them. Importing treats the file as untrusted: unknown widget
types, oversized or malformed values, widgets outside the grid, ids instead of refs and a newer
file version are refused with a message, services must be plain `http`/`https` hosts (the same
rules as service discovery), and widget addresses may only be http, https or relative.
Everything is created in one transaction or not at all.

Service handling on import: *match* (default) reuses an existing service with the same
host and port, or the same name (case-insensitive), and creates the rest; *create* always
creates; *skip* leaves services unlinked. A file with **custom widgets** is flagged
("Contains custom code; it runs sandboxed"), and its custom widgets are imported without
integration or service links until you set them in the widget's settings. Page files use
`format: "startpage-page"`, `version: 1`.

## Custom widget

*Add widget > Advanced > Custom (HTML/JS)* lets you write a small widget in HTML, CSS and
JavaScript without touching the source. Settings: `title`, `html`, `css`, `js`, `integration_id`
(optional, any integration), `service_ids` (optional), `refresh_seconds` (0 = only when the data
changes), `transparent`, `allow_network`. The settings form has an *Insert example* menu (a stat
from integration data, a service status list, a live clock).

**Data contract.** The page sends your code one message,
`{type: "startpage:data", theme, integration, services, now}`, when the widget loads and again whenever
something changes (at most 4 times a second), or every `refresh_seconds`:

- `theme`: `{mode: "dark"|"light", accent, text, background, muted, border, font}`. These are also
  set as CSS variables inside the widget: `--sp-accent`, `--sp-text`, `--sp-bg`, `--sp-muted`,
  `--sp-border`, `--sp-font`.
- `integration`: the data of the chosen integration (`{stats: [...], items: [...]}`, see
  *Integrations*), or `null`. Never its settings or keys.
- `services`: `[{name, url, status, latency_ms}]` for the chosen services.
- `now`: milliseconds since 1970.

```js
StartPage.onData(function (data) { /* called with the message above */ });
StartPage.resize(height); // optional: ask for a taller (or shorter) frame; omit height to fit the content
```

Errors your code throws are shown in a small red box inside the widget (click to dismiss).
Links do nothing (no navigation). Nothing else can be asked of the page: the only messages it
accepts are the widget's `ready` and `resize`. The one thing your code can learn about the page
itself is its origin (scheme, host and port), through `location.ancestorOrigins` where the browser
provides it; nothing else about the page is readable.

**Security model, in plain words.**

- The code runs in an `<iframe sandbox="allow-scripts">`. Without `allow-same-origin` the
  browser gives it an empty, unique origin: it cannot read the page, its cookies, local storage
  or the Start Page API, and cannot open windows, submit forms, navigate the page or download.
  The `sandbox` attribute is fixed; no setting can change it.
- A Content-Security-Policy inside the widget blocks `fetch`, XHR and WebSockets
  (`connect-src 'none'`), other frames and external scripts or styles. Images and fonts may
  still load from the web, so an author can still put data into an image address: **the policy
  is a guard rail, not a way to run code you do not trust with data you care about.**
  *Allow network requests* switches `connect-src` to `https: http:`; the code can then send what
  it receives anywhere.
- Data reaches the widget only by `postMessage`, never inside the widget's HTML. Anyone can see
  what the code receives, so link only integrations and services you are happy for it to see.
  View-only guests get the same widget with the data they are allowed to see anyway.
- Only use code you wrote or read. Pages from other people are flagged on import, and their
  custom widgets start unlinked. The built-in templates contain no custom code.

## API

Interactive docs at `/api/docs` (schema at `/api/openapi.json`); with authentication on they need
a sign-in and are never shown to view-only guests. Key routes:

| Route | Purpose |
|---|---|
| `GET/POST /api/dashboards`, `GET/PATCH/DELETE /api/dashboards/{id}` | pages |
| `PUT /api/dashboards/{id}/layout[?breakpoint=sm]` | bulk save positions after drag/resize; `sm` = the phone layout (`DELETE` with `breakpoint=sm` clears it) |
| `GET /manifest.webmanifest`, `GET /sw.js`, `/icon-*.png`, `/apple-touch-icon.png` | PWA files at the site root (outside `/api`, no login needed; the manifest is generic for anonymous callers when guests are refused) |
| `PUT /api/dashboards/order` | reorder pages: a list of page ids, saved all-or-nothing |
| `POST /api/dashboards/{id}/widgets`, `PATCH/DELETE /api/widgets/{id}`, `POST /api/widgets/{id}/duplicate` | widgets |
| `GET/POST /api/services`, `GET/PATCH/DELETE /api/services/{id}` | service registry (writes are validated, see below) |
| `GET /api/discovery/docker?integration_id=&default_host=` | writers only: candidate services from Docker containers `{candidates, skipped, errors}` (all docker integrations when the id is omitted) |
| `POST /api/discovery/import` | writers only: `{items: [{key, action: create\|update, service_id?, fields}]}` -> `{created, updated, errors}` |
| `GET /api/health`, `POST /api/health/{id}/check` | cached status / force check |
| `GET /api/health/history?hours=&buckets=` | per-service uptime %, average latency and time buckets |
| `GET /api/health/events?limit=&service_id=` | incident log (state changes), newest first |
| `POST /api/notify/test` | send a test notification with the saved settings |
| `GET /api/config/export`, `POST /api/config/import?replace=` | JSON backup; the import (and a backup restore) answers with the new state plus `warnings` for anything it had to neutralise, see below |
| `GET /api/dashboards/{id}/export` | writers only: one page as portable `startpage-page` JSON (plus `warnings`) |
| `POST /api/dashboards/import`, `POST /api/dashboards/import/preview` | writers only: `{page, name?, service_mode: match\|create\|skip, integration_map?: {ref: id\|null}}` -> creates a NEW page (or previews) `{dashboard, created_services, matched_services, unresolved_integrations, warnings, ...}` |
| `GET /api/templates`, `GET /api/templates/{id}`, `POST /api/templates/{id}/apply` | writers only: built-in templates (list without widget configs, whole file, create a page with the import options) |
| `GET/POST /api/integrations`, `PATCH/DELETE /api/integrations/{id}`, `GET /api/integrations/data`, `GET /api/integrations/{id}/data`, `POST /api/integrations/{id}/refresh` | integrations (secrets masked) + cached data |
| `GET /api/integrations/types` | every type: label, description, fields (secret / required / default), `provides`, actions |
| `POST /api/integrations/test` | `{type, config, id?}` -> `{ok, error, sample}`: one fetch, nothing saved (`id`: masked secrets come from that integration) |
| `POST /api/integrations/{id}/action/{name}` | run a type's action (body = its payload; 404 unknown, 422 bad payload, 502 upstream failure); returns fresh data |
| `POST /api/integrations/{id}/docker/{container}/{start\|stop\|restart}` | Docker actions (same as the `container` action) |
| `GET/PATCH /api/settings` | global UI settings |
| `GET/POST /api/backups`, `GET/DELETE /api/backups/{name}`, `POST /api/backups/{name}/restore` | server-side backups (`data/backups/`) |
| `GET /api/auth/status`, `POST /api/auth/login`, `POST /api/auth/logout` | sign-in state (always reachable), login, logout |
| `POST/DELETE /api/auth/password`, `PATCH /api/auth/settings` | set/change/remove the password, guest access (`none` or `read`) |
| `GET/POST/DELETE /api/auth/tokens`, `DELETE /api/auth/tokens/{id}` | list, create, revoke-all and revoke API tokens (need a password to be set, see Authentication) |
| `GET /api/icons?q=` | dashboard-icons search |
| `GET /api/favicon?url=`, `GET /api/sitemeta?url=` | cached site icon / page title |
| `GET /api/weather?location=&units=&days=` | Open-Meteo current weather + forecast (15 min cache) |
| `GET /api/revision` | change counter that open tabs poll (see below) |

**Validation on write.** Creating or editing a service holds it to the same rules as discovery and
page import: `protocol` is `http` or `https` (the health check `health_type` is separate: `http`,
`tcp` or `ping`), `host` a plain hostname or IPv4 address, `port` 1-65535, `path` a plain path
(stored with a leading `/`, so `javascript:alert(1)` becomes the harmless path `/javascript:alert(1)`
after `protocol://host`), `icon` a name, emoji, `http(s)` URL or `/path`, `health_url` an `http(s)`
URL. A bad value is a 422 with one message per field. Widget settings written through the API
(create, patch) refuse `javascript:`, `vbscript:` and non-image `data:` URLs in `url`, `link`,
`href`, `src`, `customUrl`, `icon` and `background_image` (app schemes such as `obsidian://` or
`ssh://` stay allowed in link widgets; page files stay stricter, http(s) or relative only).
Reads never fail because of what is already stored, and the page escapes the same schemes again
before it renders a link, image, frame or background.

**Older data is repaired, not refused.** A whole-config import and a backup restore never fail on a
value the rules above reject (a database or backup from before them may hold one): only the offending
field is neutralised (a bad `protocol` becomes `http`; a bad host, path, icon or health URL is
emptied; a dangerous widget URL is emptied) and the response lists each change under `warnings`.

All `POST/PUT/PATCH/DELETE` calls need an `X-Requested-By` header. The app itself sends
`startpage`. Scripts should send any other value (e.g. `X-Requested-By: script`): writes
from other clients bump `GET /api/revision`, and open tabs reload within about 3 s when it changes.

## Adding a widget type

Add an entry to `WIDGET_TYPES` in `frontend/src/widgets/index.jsx` with a `label`,
default `size`, a `fields` schema (drives the settings form automatically) and a
`Render` component. No backend changes needed — widget config is free-form JSON.
Fields may declare a `default` (seeded into new widgets). Renderers can call `useApp()`
(`src/context.js`) for settings, services, links, `goToPage` and `updateWidgetConfig`
(for widgets that edit themselves, like Notes).

Then run `npm run catalog` in `frontend/` to refresh `mcp/widget_catalog.json`, the copy of the
registry that Claude reads. `npm run test` fails until the copy matches.

## Adding an integration type

One module, nothing else to register:

1. Create `backend/app/integrations/types/<name>.py` that defines `SPEC = Spec(type=..., label=...,
   description=..., fetch=..., fields=[...], provides=("stats",), actions={...})`. `fetch(ctx)`
   gets the config (`ctx.cfg`), one shared `httpx` client (`ctx.client`), URL helpers
   (`ctx.url(path)`) and a state dict that survives between polls (`ctx.state`, for logins), and
   returns a dict, ideally with the common `stats` / `items` keys. Mark API keys `type: "secret"`.
   A list item may carry a structured `ref` (the ids a row action needs); widgets use it instead
   of parsing the item's text.
2. Write a fixture test with `tests/integration_utils.py` (`Harness(type, config, {(method, url-regex): response})`
   runs `fetch` on an `httpx.MockTransport` and checks the shape).
3. Optionally add a widget; the generic *Integration stats* / *Integration list* widgets already
   show anything that returns `stats` / `items`.

The form, validation, secret masking, `/api/integrations/types`, the **Test** button and the MCP
tools follow from the spec. The exact API and a checklist are in
`backend/app/integrations/__init__.py` (its docstring).

## Data

`backend/data/startpage.db` plus `backend/data/backups/*.json` (override the directory with
`STARTPAGE_DATA_DIR`). Delete the DB to start fresh; existing databases are migrated
automatically on startup.
