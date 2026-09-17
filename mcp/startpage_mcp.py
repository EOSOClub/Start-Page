# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.10,<2", "httpx>=0.27"]
# ///
"""MCP server that lets Claude Code build the running Start Page.

It is a thin client of the app's HTTP API (the app must be running), registered in
the repo's .mcp.json and started with `uv run --script mcp/startpage_mcp.py`.
Writes carry `X-Requested-By: claude`, so the backend counts them as outside edits
and open tabs reload within a few seconds (see /api/revision in backend/app/main.py).
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

BASE_URL = os.environ.get("STARTPAGE_URL", "http://localhost:8000").rstrip("/")
HEADERS = {"X-Requested-By": "claude"}
CATALOG_PATH = Path(__file__).with_name("widget_catalog.json")
MAX_ROWS = 200  # same search depth as the app's findFreeSpot
MAX_SHOWN = 2000  # longer strings are abbreviated in read results

# Module level so tests can swap in FastAPI's TestClient (also an httpx.Client).
client: httpx.Client = httpx.Client(base_url=BASE_URL, timeout=20)

INSTRUCTIONS = """\
Builds the user's Start Page dashboard through its API. Changes appear in open browser tabs within ~3 s.
- Start with get_overview; call list_widget_types(type) before adding or configuring a widget type.
- Layout: each page is a grid of `columns` (default 24) with rows `row_height` px tall (default 40).
  x/y/w/h are in grid cells. Omit x/y in add_widget to auto-place in the first free spot.
- Service-backed widgets (usesService) need a service_id from list_services; create the service first if needed.
- Integration widgets need an integration id of the matching type (see get_overview).
- Before deleting anything, restoring, or changing global settings: call create_backup, and confirm deletions with the user.
"""

mcp = FastMCP("startpage", instructions=INSTRUCTIONS)


# ---------- helpers ----------
def _call(method: str, path: str, body: Any = None, params: Optional[dict] = None) -> Any:
    try:
        res = client.request(method, f"/api{path}", json=body, params=params, headers=HEADERS)
    except httpx.TransportError as e:
        raise ToolError(
            f"Start Page isn't reachable at {BASE_URL} ({e.__class__.__name__}). "
            "Start it with `docker compose up -d` or `./dev.ps1`, or set STARTPAGE_URL in .mcp.json."
        ) from e
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail", res.text)
        except ValueError:
            detail = res.text
        raise ToolError(f"{method} {path} failed with HTTP {res.status_code}: {detail}")
    if res.status_code == 204 or not res.content:
        return None
    return res.json()


def _catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _widget_type(type: str) -> dict:
    catalog = _catalog()
    if type not in catalog:
        raise ToolError(f"Unknown widget type {type!r}. Valid types: {', '.join(catalog)}")
    return catalog[type]


def _find_widget(widget_id: str) -> tuple[dict, dict]:
    """(page, widget) for a widget id; the API has no single-widget GET."""
    for summary in _call("GET", "/dashboards"):
        page = _call("GET", f"/dashboards/{summary['id']}")
        for w in page["widgets"]:
            if w["id"] == widget_id:
                return page, w
    raise ToolError(f"No widget with id {widget_id!r}")


def find_free_spot(widgets: list[dict], w: float, h: float, cols: int) -> dict:
    """First top-left cell where a w x h widget overlaps nothing (mirrors App.jsx findFreeSpot)."""

    def hits(x: int, y: int) -> bool:
        return any(x < t["x"] + t["w"] and x + w > t["x"] and y < t["y"] + t["h"] and y + h > t["y"] for t in widgets)

    for y in range(MAX_ROWS):
        for x in range(int(cols - w) + 1):
            if not hits(x, y):
                return {"x": x, "y": y}
    return {"x": 0, "y": max((t["y"] + t["h"] for t in widgets), default=0)}


def _trim(value: Any) -> Any:
    """Shorten huge strings (uploaded images are stored as data: URLs) for display only."""
    if isinstance(value, str) and len(value) > MAX_SHOWN:
        return f"{value[:60]}… <{len(value)} chars>"
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trim(v) for v in value]
    return value


def _merge(current: dict, changes: dict) -> dict:
    """Shallow merge where a None value removes the key."""
    out = {**current, **changes}
    return {k: v for k, v in out.items() if v is not None}


# ---------- reading ----------
@mcp.tool()
def get_overview() -> dict:
    """Pages (with widget counts), services, integrations and global settings."""
    pages = []
    for summary in _call("GET", "/dashboards"):
        page = _call("GET", f"/dashboards/{summary['id']}")
        types: dict[str, int] = {}
        for w in page["widgets"]:
            types[w["type"]] = types.get(w["type"], 0) + 1
        pages.append({k: page[k] for k in ("id", "name", "position", "columns", "row_height")} | {"widgets": types})
    return {
        "url": BASE_URL,
        "pages": pages,
        "services": [
            {k: s.get(k) for k in ("id", "name", "url", "category", "icon")} for s in _call("GET", "/services")
        ],
        # Only identity fields: integration configs can hold API keys.
        "integrations": [{k: i.get(k) for k in ("id", "name", "type")} for i in _call("GET", "/integrations")],
        "settings": _trim(_call("GET", "/settings")),
    }


@mcp.tool()
def get_page(page_id: str) -> dict:
    """A page with its settings and every widget (id, type, x, y, w, h, service_id, config)."""
    return _trim(_call("GET", f"/dashboards/{page_id}"))


@mcp.tool()
def list_widget_types(type: Optional[str] = None) -> dict:
    """Widget types. Without `type`: a summary of all. With `type`: its full config fields.

    Field entries give key, type, options, default and the expected `value` shape;
    `conditional: true` means the form only shows the field for some other settings.
    """
    if type:
        return {type: _widget_type(type)}
    return {
        name: {"label": d["label"], "size": d["size"], "usesService": d["usesService"],
               "fields": [f["key"] for f in d["fields"]]}
        for name, d in _catalog().items()
    }


# ---------- widgets ----------
@mcp.tool()
def add_widget(
    page_id: str,
    type: str,
    config: Optional[dict[str, Any]] = None,
    x: Optional[float] = None,
    y: Optional[float] = None,
    w: Optional[float] = None,
    h: Optional[float] = None,
    service_id: Optional[str] = None,
) -> dict:
    """Add a widget to a page. Config fields you omit get the type's defaults;
    w/h default to the type's size; omit x/y to auto-place in the first free spot."""
    spec = _widget_type(type)
    known = {f["key"] for f in spec["fields"]}
    defaults = {f["key"]: f["default"] for f in spec["fields"] if "default" in f}
    config = {**defaults, **(config or {})}
    body: dict[str, Any] = {
        "type": type,
        "w": w if w is not None else spec["size"]["w"],
        "h": h if h is not None else spec["size"]["h"],
        "service_id": service_id,
        "config": config,
    }
    if x is None or y is None:
        page = _call("GET", f"/dashboards/{page_id}")
        spot = find_free_spot(page["widgets"], body["w"], body["h"], page.get("columns") or 24)
        body["x"] = spot["x"] if x is None else x
        body["y"] = spot["y"] if y is None else y
    else:
        body["x"], body["y"] = x, y
    created = _call("POST", f"/dashboards/{page_id}/widgets", body)
    warnings = []
    unknown = sorted(set(config) - known)
    if unknown:
        warnings.append(f"config keys not in the {type} form (ignored by the UI): {unknown}")
    if spec["usesService"] and not service_id:
        warnings.append(f"{type} widgets show a service; set service_id with update_widget")
    return {"widget": created, "warnings": warnings} if warnings else {"widget": created}


@mcp.tool()
def update_widget(
    widget_id: str,
    config: Optional[dict[str, Any]] = None,
    x: Optional[float] = None,
    y: Optional[float] = None,
    w: Optional[float] = None,
    h: Optional[float] = None,
    service_id: Optional[str] = None,
    page_id: Optional[str] = None,
) -> dict:
    """Change a widget. `config` is merged into the current config (a null value removes
    that key). `page_id` moves the widget to another page."""
    _, current = _find_widget(widget_id)
    body: dict[str, Any] = {k: v for k, v in {"x": x, "y": y, "w": w, "h": h, "service_id": service_id}.items() if v is not None}
    if config is not None:
        body["config"] = _merge(current.get("config") or {}, config)
    if page_id is not None:
        body["dashboard_id"] = page_id
    if not body:
        raise ToolError("Nothing to change")
    return _call("PATCH", f"/widgets/{widget_id}", body)


@mcp.tool()
def set_layout(page_id: str, items: list[dict[str, float | str]]) -> str:
    """Move/resize several widgets at once: items = [{id, x, y, w, h}, ...]."""
    _call("PUT", f"/dashboards/{page_id}/layout", items)
    return f"Saved layout for {len(items)} widget(s)"


@mcp.tool()
def duplicate_widget(widget_id: str) -> dict:
    """Copy a widget onto the same page, offset one cell (so it overlaps): move it with update_widget."""
    return _call("POST", f"/widgets/{widget_id}/duplicate")


@mcp.tool()
def delete_widget(widget_id: str) -> str:
    """Delete a widget. Confirm with the user and create_backup first."""
    _call("DELETE", f"/widgets/{widget_id}")
    return f"Deleted widget {widget_id}"


# ---------- pages ----------
@mcp.tool()
def create_page(name: str, columns: int = 24, row_height: int = 40, settings: Optional[dict[str, Any]] = None) -> dict:
    """Create a page (appended after the existing ones)."""
    return _call("POST", "/dashboards", {"name": name, "columns": columns, "row_height": row_height, "settings": settings or {}})


@mcp.tool()
def update_page(
    page_id: str,
    name: Optional[str] = None,
    position: Optional[int] = None,
    columns: Optional[int] = None,
    row_height: Optional[int] = None,
    settings: Optional[dict[str, Any]] = None,
) -> dict:
    """Rename, reorder or reconfigure a page. `settings` (gap, snap, show_grid,
    background_image, background_dim, ...) is merged into the current page settings."""
    body: dict[str, Any] = {
        k: v for k, v in {"name": name, "position": position, "columns": columns, "row_height": row_height}.items() if v is not None
    }
    if settings is not None:
        current = _call("GET", f"/dashboards/{page_id}").get("settings") or {}
        body["settings"] = _merge(current, settings)
    if not body:
        raise ToolError("Nothing to change")
    return _call("PATCH", f"/dashboards/{page_id}", body)


@mcp.tool()
def delete_page(page_id: str) -> str:
    """Delete a page and all its widgets. Confirm with the user and create_backup first."""
    _call("DELETE", f"/dashboards/{page_id}")
    return f"Deleted page {page_id}"


# ---------- services ----------
@mcp.tool()
def list_services() -> list[dict]:
    """The service registry (hosts that service widgets point at and health checks watch)."""
    return _call("GET", "/services")


@mcp.tool()
def create_service(
    name: str,
    host: str,
    protocol: str = "http",
    port: Optional[int] = None,
    path: str = "",
    icon: str = "",
    category: str = "",
    description: str = "",
    health_enabled: bool = True,
) -> dict:
    """Register a service, e.g. name="Jellyfin", host="192.168.1.20", port=8096."""
    return _call("POST", "/services", {
        "name": name, "host": host, "protocol": protocol, "port": port, "path": path, "icon": icon,
        "category": category, "description": description, "health_enabled": health_enabled,
    })


@mcp.tool()
def update_service(service_id: str, changes: dict[str, Any]) -> dict:
    """Update service fields (name, protocol, host, port, path, icon, color, category,
    description, health_enabled, health_type, health_url, health_interval, depends_on)."""
    return _call("PATCH", f"/services/{service_id}", changes)


# ---------- settings & backups ----------
@mcp.tool()
def update_settings(patch: dict[str, Any]) -> dict:
    """Merge-update global settings (title, theme, accent, font, background_*, widget_opacity,
    search_url, open_new_tab, ...). Call get_overview to see current values; back up first."""
    return _call("PATCH", "/settings", patch)


@mcp.tool()
def create_backup(label: str = "claude") -> dict:
    """Snapshot the whole configuration (restorable from the app's Backups dialog)."""
    return _call("POST", "/backups", params={"label": label})


@mcp.tool()
def list_backups() -> list[dict]:
    """Saved backups, newest first."""
    return _call("GET", "/backups")


@mcp.tool()
def restore_backup(name: str) -> dict:
    """Replace the whole configuration with a backup (the app saves a pre-restore backup first).
    Only on the user's explicit request."""
    return _call("POST", f"/backups/{name}/restore", params={"replace": "true"})


# ---------- lookups ----------
@mcp.tool()
def search_icons(query: str, limit: int = 20) -> Any:
    """Search dashboard-icons names for a widget or service `icon` field."""
    return _call("GET", "/icons", params={"q": query, "limit": limit})


@mcp.tool()
def site_meta(url: str) -> Any:
    """Title and icon details for a URL, useful for naming link and bookmark entries."""
    return _call("GET", "/sitemeta", params={"url": url})


if __name__ == "__main__":
    mcp.run()
