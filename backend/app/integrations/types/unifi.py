"""UniFi Network controller (read-only): WAN, client and device numbers from a local account or API key.

Two flavours, auto-detected (or forced with `variant`):
  os      UniFi OS console (UDM, UDR, Cloud Gateway, ...): POST /api/auth/login {"username","password"}
          answers with a TOKEN cookie and an X-CSRF-Token header; the Network API sits under
          /proxy/network/api/s/{site}/...
  legacy  standalone controller / Network Application: POST /api/login, API under /api/s/{site}/...
Detection (variant "auto"): try the UniFi OS login; a 404 / 405 means a legacy controller. The result is
kept in ctx.state["variant"]. The session cookie is kept in ctx.state["cookie"] and sent explicitly
(the client's cookie jar is cleared after login). A 401 on a data request triggers exactly one re-login.
An API key (X-API-KEY header) replaces the login; keys exist on UniFi OS consoles only, so "auto" then
means "os". The CSRF token is only needed for writes; this type has none, so it is stored but unused.

Endpoints: stat/health (wan, wlan, lan, www subsystems), stat/sta (clients), stat/device (devices,
only when list = devices).
"""
from typing import Any, Optional
from urllib.parse import quote

import httpx

from ..base import Context, Spec, stat
from ..errors import AuthFailed, IntegrationError

MAX_CLIENTS = 25
MAX_DEVICES = 50
_LOGIN_FAILED = "login failed: check the UniFi username and password (or API key)"

# stat/device `state`: 0 disconnected, 1 connected, 4 upgrading, 5 provisioning, 6 heartbeat missed,
# 7 adopting, 9 adoption failed, 11 isolated
_DEVICE_STATUS = {0: "offline", 1: "online", 4: "maintenance", 5: "maintenance", 6: "degraded", 7: "maintenance",
                  9: "offline", 11: "degraded"}


def _uses_key(ctx: Context) -> bool:
    return bool(ctx.cfg.get("api_key"))


def _validate(cfg: dict) -> list[tuple[str, str]]:
    if cfg.get("api_key") or (cfg.get("username") and cfg.get("password")):
        return []
    return [("username", "enter a username and password, or an API key")]


def _cookie_header(resp: httpx.Response) -> str:
    pairs = []
    for line in resp.headers.get_list("set-cookie"):
        first = line.split(";", 1)[0].strip()
        if "=" in first:
            pairs.append(first)
    return "; ".join(pairs)


async def _login(ctx: Context) -> None:
    chosen = str(ctx.cfg.get("variant") or "auto")
    state = ctx.state
    if _uses_key(ctx):
        state["variant"] = "os" if chosen == "auto" else chosen
        state["logged_in"] = True
        return
    ctx.auth_gate(_LOGIN_FAILED)  # no request while the backoff after a refused login runs
    creds = {"username": ctx.cfg.get("username") or "", "password": ctx.cfg.get("password") or ""}
    variant = chosen if chosen != "auto" else state.get("variant") or "os"
    if variant == "os":
        resp = await ctx.client.post(ctx.url("/api/auth/login"), json={**creds, "remember": True})
        if resp.status_code in (404, 405) and chosen == "auto":
            variant = "legacy"  # no UniFi OS login route: a standalone controller
        elif resp.status_code in (400, 401, 403):
            raise ctx.auth_fail(_LOGIN_FAILED)
        else:
            resp.raise_for_status()
            state["csrf"] = resp.headers.get("x-csrf-token") or resp.headers.get("x-updated-csrf-token")
            state["cookie"] = _cookie_header(resp)
    if variant == "legacy":
        resp = await ctx.client.post(ctx.url("/api/login"), json={**creds, "remember": True})
        if resp.status_code in (400, 401, 403):
            raise ctx.auth_fail(_LOGIN_FAILED)
        resp.raise_for_status()
        state["csrf"] = None
        state["cookie"] = _cookie_header(resp)
    ctx.client.cookies.clear()
    if not state.get("cookie"):
        raise ctx.auth_fail("login failed: the controller sent no session cookie")
    state["variant"] = variant
    state["logged_in"] = True


async def close(ctx: Context) -> None:
    """Log the session out of the controller (not needed with an API key)."""
    cookie = ctx.state.pop("cookie", None)
    ctx.state.pop("logged_in", None)
    if cookie and not _uses_key(ctx):
        headers = {"Cookie": cookie, **({"X-CSRF-Token": ctx.state["csrf"]} if ctx.state.get("csrf") else {})}
        await ctx.client.post(ctx.url("/api/auth/logout" if ctx.state.get("variant") == "os" else "/api/logout"), headers=headers)


def _prefix(ctx: Context) -> str:
    site = quote(str(ctx.cfg.get("site") or "default"), safe="")
    base = "/proxy/network" if ctx.state.get("variant") == "os" else ""
    return f"{base}/api/s/{site}"


async def _get(ctx: Context, path: str) -> list:
    for attempt in (0, 1):
        if not ctx.state.get("logged_in"):
            await _login(ctx)
        headers: dict = {}
        if _uses_key(ctx):
            headers["X-API-KEY"] = str(ctx.cfg["api_key"])
        elif ctx.state.get("cookie"):
            headers["Cookie"] = ctx.state["cookie"]
        resp = await ctx.client.get(ctx.url(_prefix(ctx) + path), headers=headers)
        if resp.status_code == 401 and not _uses_key(ctx):
            for k in ("logged_in", "cookie", "csrf"):
                ctx.state.pop(k, None)
            if attempt == 0:
                continue  # expired session: one fresh login; a second 401 is a failure with backoff
            raise ctx.auth_fail(_LOGIN_FAILED)
        if resp.status_code in (401, 403):
            raise AuthFailed(_LOGIN_FAILED)
        resp.raise_for_status()
        if resp.headers.get("x-updated-csrf-token"):
            ctx.state["csrf"] = resp.headers["x-updated-csrf-token"]
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise IntegrationError("invalid response")
        return data
    raise IntegrationError(_LOGIN_FAILED)  # unreachable


def _n(v: Any) -> Optional[float]:
    return None if isinstance(v, bool) or not isinstance(v, (int, float)) else v


def _mbit(bytes_per_s: Any) -> Optional[float]:
    v = _n(bytes_per_s)
    return None if v is None else round(v * 8 / 1_000_000, 2)


def _uptime(seconds: Any) -> Optional[str]:
    s = _n(seconds)
    if s is None or s < 0:
        return None
    s = int(s)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def _bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def _health(rows: list) -> dict:
    return {r.get("subsystem"): r for r in rows if isinstance(r, dict)}


def _device_items(rows: list) -> list[dict]:
    items = []
    for d in rows:
        if not isinstance(d, dict):
            continue
        item = {"title": d.get("name") or d.get("hostname") or d.get("mac") or "device",
                "status": _DEVICE_STATUS.get(d.get("state"), "unknown")}
        sub = " ".join(str(x) for x in (d.get("model"), ) if x)
        if sub:
            item["subtitle"] = sub
        up = _uptime(d.get("uptime"))
        if up and item["status"] != "offline":
            item["meta"] = up
        items.append(item)
    items.sort(key=lambda i: (i["status"] == "online", i["title"].lower()))  # trouble first
    return items[:MAX_DEVICES]


def _client_items(rows: list) -> list[dict]:
    def traffic(c: dict) -> float:
        return (_n(c.get("tx_bytes")) or 0) + (_n(c.get("rx_bytes")) or 0)

    ranked = sorted((c for c in rows if isinstance(c, dict)), key=traffic, reverse=True)[:MAX_CLIENTS]
    items = []
    for c in ranked:
        item = {"title": c.get("name") or c.get("hostname") or c.get("mac") or "client", "status": "online"}
        sub = " / ".join(str(x) for x in (c.get("ip"), "wired" if c.get("is_wired") else "wifi") if x)
        if sub:
            item["subtitle"] = sub
        if traffic(c) > 0:
            item["meta"] = _bytes(traffic(c))
        items.append(item)
    return items


async def fetch(ctx: Context) -> dict:
    health = _health(await _get(ctx, "/stat/health"))
    clients = await _get(ctx, "/stat/sta")
    clients = [c for c in clients if isinstance(c, dict)]
    wan, www = health.get("wan") or {}, health.get("www") or {}
    wan_status = wan.get("status") or www.get("status")
    down = _mbit(wan.get("rx_bytes-r", www.get("rx_bytes-r")))
    up = _mbit(wan.get("tx_bytes-r", www.get("tx_bytes-r")))
    wired = sum(1 for c in clients if c.get("is_wired"))
    adopted = sum(int(_n(health.get(s, {}).get("num_adopted")) or 0) for s in ("wan", "wlan", "lan"))
    offline = sum(int(_n(health.get(s, {}).get("num_disconnected")) or 0) for s in ("wan", "wlan", "lan"))
    latency = _n(www.get("latency"))
    stats = [
        stat("wan_status", "WAN", wan_status or "unknown",
             tone="ok" if wan_status == "ok" else "bad" if wan_status in ("error", "unknown", None) else "warn"),
        stat("wan_down", "WAN down", down, "Mbit/s"),
        stat("wan_up", "WAN up", up, "Mbit/s"),
        stat("clients", "Clients", len(clients)),
        stat("clients_wired", "Wired clients", wired),
        stat("clients_wireless", "Wireless clients", len(clients) - wired),
        stat("devices_adopted", "Devices", adopted),
        stat("devices_offline", "Devices offline", offline, tone="bad" if offline else "ok"),
    ]
    if latency is not None:
        stats.append(stat("latency", "Latency", round(latency, 1), "ms"))
    if ctx.cfg.get("list") == "clients":
        items = _client_items(clients)
    else:
        items = _device_items(await _get(ctx, "/stat/device"))
    return {"variant": ctx.state.get("variant"), "stats": stats, "items": items}


SPEC = Spec(
    type="unifi",
    label="UniFi Network",
    description="WAN rate, client and device counts from a UniFi Network controller or UniFi OS console.",
    fetch=fetch,
    close=close,
    default_interval=30,
    provides=("stats", "items"),
    validate=_validate,
    fields=[
        {"key": "url", "label": "Controller URL", "type": "url", "required": True, "placeholder": "https://192.168.1.1"},
        {"key": "username", "label": "Username", "type": "text", "help": "A local account (read-only is enough)."},
        {"key": "password", "label": "Password", "type": "secret"},
        {"key": "api_key", "label": "API key", "type": "secret",
         "help": "Alternative to username and password on UniFi OS consoles that support API keys."},
        {"key": "site", "label": "Site", "type": "text", "default": "default",
         "help": "The site name in the URL (usually default)."},
        {"key": "variant", "label": "Controller type", "type": "select", "default": "auto",
         "options": [{"value": "auto", "label": "Detect automatically"},
                     {"value": "os", "label": "UniFi OS console"},
                     {"value": "legacy", "label": "Standalone controller"}]},
        {"key": "list", "label": "List shows", "type": "select", "default": "devices",
         "options": [{"value": "devices", "label": "Devices"}, {"value": "clients", "label": "Busiest clients"}]},
    ],
)
