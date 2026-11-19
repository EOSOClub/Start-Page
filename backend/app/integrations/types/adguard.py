"""AdGuard Home: query stats and a protection on/off action (HTTP basic auth).

GET /control/stats   num_dns_queries, num_blocked_filtering, avg_processing_time (seconds),
                     dns_queries / blocked_filtering (arrays per hour or day), top_blocked_domains
GET /control/status  protection_enabled, protection_disabled_duration (ms left on a timed pause)
Stat keys mirror the Pi-hole type (queries, blocked, percent_blocked, status) so one widget fits both;
`status` is "enabled" / "disabled" like Pi-hole. `blocking` = {"enabled": bool|None, "timer": seconds|None}.

Action set_protection {enabled, seconds?}: POST /control/protection {"enabled", "duration": ms}
(AdGuard Home >= 0.107.30). When that route answers 404/405 (older versions) it falls back to
POST /control/dns_config {"protection_enabled"}, which has no timer: a timed pause is then refused
rather than silently turning protection off for good. The action returns which route it used.
"""
from typing import Any, Optional

import httpx

from ..base import Action, Context, Spec, stat
from ..errors import AuthFailed, IntegrationError

MAX_ITEMS = 50
MAX_SERIES = 60


def _auth(ctx: Context) -> Any:
    user = ctx.cfg.get("username")
    if not user:
        return httpx.USE_CLIENT_DEFAULT
    return httpx.BasicAuth(user, ctx.cfg.get("password") or "")


def _check(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise AuthFailed("login failed: check the AdGuard Home username and password")
    resp.raise_for_status()


async def _get(ctx: Context, path: str) -> Any:
    resp = await ctx.client.get(ctx.url(path), auth=_auth(ctx))
    _check(resp)
    return resp.json()


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _series(raw: Any) -> Optional[list]:
    if not isinstance(raw, list):
        return None
    vals = [float(v) for v in raw if _num(v) is not None][-MAX_SERIES:]
    return vals or None


def _top_blocked(raw: Any) -> list[dict]:
    """top_blocked_domains is [{"domain": count}, ...] (one key per entry)."""
    items: list[dict] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        for domain, count in entry.items():
            items.append({"title": str(domain), "subtitle": "blocked", "status": "offline",
                          "meta": f"{int(count)}" if _num(count) is not None else None})
    return items[:MAX_ITEMS]


async def fetch(ctx: Context) -> dict:
    stats = await _get(ctx, "/control/stats")
    status = await _get(ctx, "/control/status")
    if not isinstance(stats, dict):
        raise IntegrationError("invalid response")
    status = status if isinstance(status, dict) else {}
    queries = _num(stats.get("num_dns_queries"))
    blocked = _num(stats.get("num_blocked_filtering"))
    percent = round(blocked / queries * 100, 1) if queries and blocked is not None else (0.0 if queries == 0 else None)
    avg = _num(stats.get("avg_processing_time"))
    enabled = status.get("protection_enabled")
    enabled = enabled if isinstance(enabled, bool) else None
    pause_ms = _num(status.get("protection_disabled_duration"))
    timer = round(pause_ms / 1000) if enabled is False and pause_ms and pause_ms > 0 else None
    state = None if enabled is None else "enabled" if enabled else "disabled"
    return {
        "blocking": {"enabled": enabled, "timer": timer},
        "version": status.get("version"),
        "stats": [
            stat("queries", "Queries today", queries, series=_series(stats.get("dns_queries"))),
            stat("blocked", "Blocked", blocked, series=_series(stats.get("blocked_filtering"))),
            stat("percent_blocked", "Percent blocked", percent, "%"),
            stat("avg_time", "Avg processing time", round(avg * 1000, 1) if avg is not None else None, "ms"),
            stat("status", "Blocking", state or "unknown",
                 tone="ok" if state == "enabled" else "warn" if state == "disabled" else "bad"),
        ],
        "items": _top_blocked(stats.get("top_blocked_domains")),
    }


def _protection_payload(payload: dict) -> tuple[bool, Optional[int]]:
    """(enabled, seconds) from an action payload, validated here too so a direct call is safe."""
    enabled, seconds = payload.get("enabled"), payload.get("seconds")
    if not isinstance(enabled, bool):
        raise IntegrationError("invalid payload: enabled must be true or false")
    if seconds in (None, "", 0):
        return enabled, None
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 1 <= seconds <= 86400:
        raise IntegrationError("invalid payload: seconds must be between 1 and 86400")
    return enabled, None if enabled else int(seconds)


async def set_protection(ctx: Context, payload: dict) -> dict:
    enabled, seconds = _protection_payload(payload)
    body: dict = {"enabled": enabled}
    if seconds:
        body["duration"] = seconds * 1000
    resp = await ctx.client.post(ctx.url("/control/protection"), json=body, auth=_auth(ctx))
    if resp.status_code in (404, 405):
        if seconds:
            raise IntegrationError("this AdGuard Home version cannot pause protection for a set time")
        resp = await ctx.client.post(ctx.url("/control/dns_config"), json={"protection_enabled": enabled}, auth=_auth(ctx))
        _check(resp)
        return {"enabled": enabled, "seconds": None, "route": "dns_config"}
    _check(resp)
    return {"enabled": enabled, "seconds": seconds, "route": "protection"}


SPEC = Spec(
    type="adguard",
    label="AdGuard Home",
    description="DNS query and block stats from AdGuard Home, with a protection on/off switch.",
    fetch=fetch,
    default_interval=15,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "AdGuard Home URL", "type": "url", "required": True, "placeholder": "http://192.168.1.2:3000"},
        {"key": "username", "label": "Username", "type": "text"},
        {"key": "password", "label": "Password", "type": "secret"},
    ],
    actions={
        "set_protection": Action(
            set_protection,
            label="Set protection",
            description="Turn DNS filtering on or off, optionally off for a number of seconds.",
            payload=[
                {"key": "enabled", "label": "Protection enabled", "type": "checkbox", "required": True},
                {"key": "seconds", "label": "Disable for (seconds)", "type": "number", "min": 1, "max": 86400},
            ],
        )
    },
)
