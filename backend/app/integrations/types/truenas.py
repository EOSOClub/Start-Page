"""TrueNAS (SCALE and CORE) over the REST API v2.0 with an API key (`Authorization: Bearer <key>`).

Calls: GET /api/v2.0/pool, /api/v2.0/alert/list and /api/v2.0/system/info. Alerts and system info are
optional: when they fail (other than 401/403) their stats are left out.
Newer TrueNAS releases deprecate REST in favour of the WebSocket JSON-RPC API (25.04 deprecates it,
a later release removes it). This type only speaks REST: when the REST endpoints are gone (404 or a
non-JSON answer) it raises a clear, safe error instead of guessing.
"""
from typing import Any, Optional

import httpx

from ..base import Context, Spec, stat
from ..errors import AuthFailed, IntegrationError

MAX_ITEMS = 50
_LEVELS = ["INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
_GONE = "REST API not available (TrueNAS 25.04+ deprecates REST for the WebSocket API; use a release that still has /api/v2.0)"


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f or f in (float("inf"), float("-inf")) else f


def _pct(used: float, total: float) -> Optional[float]:
    return round(used / total * 100, 1) if total > 0 else None


def _tone(p: Optional[float]) -> Optional[str]:
    if p is None:
        return None
    return "bad" if p >= 90 else "warn" if p >= 80 else "ok"


def _bytes(n: Any) -> str:
    v = _num(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if v < 1024 or unit == "PiB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return "0 B"


def _duration(seconds: Any) -> str:
    s = int(_num(seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d {h}h" if d else f"{h}h {rem // 60}m"


async def _get(ctx: Context, path: str) -> Any:
    resp = await ctx.client.get(ctx.url("/api/v2.0" + path), headers={"Authorization": f"Bearer {ctx.cfg.get('api_key', '')}"})
    if resp.status_code in (401, 403):
        raise AuthFailed("login failed: check the TrueNAS API key")
    if resp.status_code == 404:
        raise IntegrationError(_GONE)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        raise IntegrationError(_GONE) from None


async def _optional(ctx: Context, path: str) -> Any:
    try:
        return await _get(ctx, path)
    except (IntegrationError, httpx.HTTPError, ValueError):
        return None  # /pool already accepted the key; a limited role may lack these, so their stats are left out


def _pool_status(p: dict) -> str:
    s = str(p.get("status") or "").upper()
    return "online" if s == "ONLINE" else "degraded" if s == "DEGRADED" else "offline"


def _alert_status(level: str) -> str:
    return "unknown" if level in ("INFO", "NOTICE") else "degraded" if level == "WARNING" else "offline"


async def fetch(ctx: Context) -> dict:
    pools = await _get(ctx, "/pool")
    if not isinstance(pools, list):
        raise IntegrationError("invalid response (is this a TrueNAS API?)")
    pools = [p for p in pools if isinstance(p, dict)]
    alerts_raw = await _optional(ctx, "/alert/list")
    info = await _optional(ctx, "/system/info")
    alerts = [a for a in alerts_raw if isinstance(a, dict) and not a.get("dismissed")] if isinstance(alerts_raw, list) else None
    info = info if isinstance(info, dict) else {}

    healthy = sum(1 for p in pools if _pool_status(p) == "online" and p.get("healthy", True))
    used = _pct(sum(_num(p.get("allocated")) for p in pools), sum(_num(p.get("size")) for p in pools))
    stats = [
        stat("pools", "Pools healthy", f"{healthy}/{len(pools)}", tone="ok" if healthy == len(pools) else "bad"),
        stat("used", "Used", used, "%", _tone(used)),
    ]
    if alerts is not None:
        top = max((_LEVELS.index(str(a.get("level")).upper()) for a in alerts if str(a.get("level")).upper() in _LEVELS), default=-1)
        tone = "ok" if not alerts else "info" if top <= 1 else "warn" if top == 2 else "bad"
        stats.append(stat("alerts", "Alerts", len(alerts), tone=tone))
    if info.get("uptime_seconds") is not None:
        stats.append(stat("uptime", "Uptime", _duration(info["uptime_seconds"])))
    if info.get("version"):
        stats.append(stat("version", "Version", str(info["version"])))
    load = info.get("loadavg")
    if isinstance(load, list) and load:
        stats.append(stat("load", "Load (1 min)", round(_num(load[0]), 2)))

    if (ctx.cfg.get("list") or "pools") == "alerts":
        items = [{
            "title": str(a.get("formatted") or a.get("klass") or "alert")[:200],
            "subtitle": str(a.get("level") or "").upper() or None,
            "status": _alert_status(str(a.get("level")).upper()),
            "meta": str(a.get("klass") or "") or None,
        } for a in (alerts or [])]
    else:
        items = []
        for p in pools:
            pct = _pct(_num(p.get("allocated")), _num(p.get("size")))
            items.append({
                "title": str(p.get("name") or "pool"),
                "subtitle": str(p.get("status") or "unknown"),
                "status": _pool_status(p),
                "progress": min(pct, 100.0) if pct is not None else None,
                "meta": f"{_bytes(p.get('allocated'))} / {_bytes(p.get('size'))}",
            })
    return {"stats": stats, "items": items[:MAX_ITEMS]}


SPEC = Spec(
    type="truenas",
    label="TrueNAS",
    description="TrueNAS SCALE/CORE pools, usage and alerts over the REST API v2.0 (API key).",
    fetch=fetch,
    default_interval=60,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "TrueNAS URL", "type": "url", "required": True, "placeholder": "https://truenas.local"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True,
         "help": "Credentials -> API Keys. Use https: TrueNAS revokes keys that are sent over plain http. Needs a release that still serves REST (/api/v2.0)."},
        {"key": "list", "label": "List shows", "type": "select", "default": "pools",
         "options": [{"value": "pools", "label": "Pools"}, {"value": "alerts", "label": "Active alerts"}]},
    ],
)
