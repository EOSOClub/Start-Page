"""Pi-hole v6 (session API) and v5 (admin/api.php): query stats and a blocking on/off action.

v6: POST /api/auth {"password"} -> session.sid, sent as the X-FTL-SID header; GET /api/stats/summary;
    GET|POST /api/dns/blocking. A 401 means the session expired: log in again once and retry.
v5: GET /admin/api.php?summaryRaw[&auth=TOKEN]; ?disable[=SECONDS] / ?enable need &auth=TOKEN.
The version is auto-detected (v6 answers GET /api/auth with JSON; v5 has no such route) unless set.
"""
from typing import Any

from ..base import Action, Context, Spec, stat
from ..errors import IntegrationError


# ---------------------------------------------------------------- v6
_FAILED = "login failed: check the Pi-hole password"


async def _login(ctx: Context) -> None:
    ctx.auth_gate(_FAILED)  # no request while the backoff after a refused login runs
    resp = await ctx.client.post(ctx.url("/api/auth"), json={"password": ctx.cfg.get("password") or ""})
    if resp.status_code in (401, 403):
        raise ctx.auth_fail(_FAILED)
    resp.raise_for_status()
    session = (resp.json() or {}).get("session") or {}
    if not session.get("valid"):
        raise ctx.auth_fail(_FAILED)
    ctx.state["sid"] = session.get("sid")  # None when Pi-hole has no password set
    ctx.state["logged_in"] = True


async def close(ctx: Context) -> None:
    """Release the v6 session (Pi-hole keeps a limited number of them): DELETE /api/auth."""
    sid = ctx.state.pop("sid", None)
    ctx.state.pop("logged_in", None)
    if sid:
        await ctx.client.delete(ctx.url("/api/auth"), headers={"X-FTL-SID": sid})


async def _v6(ctx: Context, method: str, path: str, json: Any = None) -> dict:
    for attempt in (0, 1):
        if not ctx.state.get("logged_in"):
            await _login(ctx)
        sid = ctx.state.get("sid")
        resp = await ctx.client.request(method, ctx.url(path), json=json, headers={"X-FTL-SID": sid} if sid else {})
        if resp.status_code == 401:
            ctx.state.pop("logged_in", None)
            if attempt == 0:
                continue  # session expired or was revoked: one fresh login; a second 401 is a failure with backoff
            raise ctx.auth_fail(_FAILED)
        resp.raise_for_status()
        return resp.json()
    raise IntegrationError("login failed")  # unreachable; keeps the type checker honest


async def _fetch_v6(ctx: Context) -> dict:
    summary = await _v6(ctx, "GET", "/api/stats/summary")
    blocking = await _v6(ctx, "GET", "/api/dns/blocking")
    q = summary.get("queries") or {}
    return _result(
        6,
        queries=q.get("total"),
        blocked=q.get("blocked"),
        percent=q.get("percent_blocked"),
        domains=(summary.get("gravity") or {}).get("domains_being_blocked"),
        status=blocking.get("blocking"),
        timer=blocking.get("timer"),
        clients=(summary.get("clients") or {}).get("active"),
    )


# ---------------------------------------------------------------- v5
async def _v5(ctx: Context, **params: Any) -> Any:
    token = ctx.cfg.get("api_token")
    if token:
        params["auth"] = token
    resp = await ctx.client.get(ctx.url("/admin/api.php"), params=params)
    resp.raise_for_status()
    return resp.json()


async def _fetch_v5(ctx: Context) -> dict:
    s = await _v5(ctx, summaryRaw="")
    if not isinstance(s, dict) or "dns_queries_today" not in s:
        raise IntegrationError("invalid response (is the API token right?)")
    return _result(
        5,
        queries=s.get("dns_queries_today"),
        blocked=s.get("ads_blocked_today"),
        percent=s.get("ads_percentage_today"),
        domains=s.get("domains_being_blocked"),
        status=s.get("status"),
        timer=None,
        clients=s.get("unique_clients"),
    )


# ---------------------------------------------------------------- shared
def _result(version: int, *, queries, blocked, percent, domains, status, timer, clients) -> dict:
    enabled = None if status not in ("enabled", "disabled") else status == "enabled"
    return {
        "version": version,
        "blocking": {"enabled": enabled, "timer": timer},
        "stats": [
            stat("queries", "Queries today", queries),
            stat("blocked", "Blocked", blocked),
            stat("percent_blocked", "Percent blocked",round(float(percent), 1) if percent is not None else None, "%"),
            stat("domains", "On blocklist", domains),
            stat("status", "Blocking", status or "unknown",
                 tone="ok" if status == "enabled" else "warn" if status == "disabled" else "bad"),
            *([stat("clients", "Clients", clients)] if clients is not None else []),
        ],
    }


async def _version(ctx: Context) -> int:
    chosen = str(ctx.cfg.get("version") or "auto")
    if chosen in ("5", "6"):
        return int(chosen)
    if "version" not in ctx.state:
        resp = await ctx.client.get(ctx.url("/api/auth"))
        try:
            body = resp.json()
        except ValueError:
            body = None
        v6 = resp.status_code in (200, 401) and isinstance(body, dict) and ("session" in body or "error" in body)
        ctx.state["version"] = 6 if v6 else 5
    return ctx.state["version"]


async def fetch(ctx: Context) -> dict:
    return await (_fetch_v6(ctx) if await _version(ctx) == 6 else _fetch_v5(ctx))


async def set_blocking(ctx: Context, payload: dict) -> None:
    enabled, seconds = payload.get("enabled"), payload.get("seconds")
    if not isinstance(enabled, bool):  # validated here too so a direct call is safe
        raise IntegrationError("invalid payload: enabled must be true or false")
    if seconds in (None, "", 0):
        seconds = None
    elif isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 1 <= seconds <= 86400:
        raise IntegrationError("invalid payload: seconds must be between 1 and 86400")
    else:
        seconds = int(seconds)
    if await _version(ctx) == 6:
        await _v6(ctx, "POST", "/api/dns/blocking", json={"blocking": enabled, "timer": None if enabled else seconds})
        return
    if not ctx.cfg.get("api_token"):
        raise IntegrationError("an API token is required to change blocking on Pi-hole v5")
    result = await (_v5(ctx, enable="") if enabled else _v5(ctx, disable=seconds if seconds else ""))
    if not isinstance(result, dict) or "status" not in result:
        raise IntegrationError("Pi-hole refused the change (is the API token right?)")


SPEC = Spec(
    type="pihole",
    label="Pi-hole",
    description="DNS query and block stats from Pi-hole v5 or v6, with a blocking on/off switch.",
    fetch=fetch,
    close=close,
    default_interval=15,
    provides=("stats",),
    fields=[
        {"key": "url", "label": "Pi-hole URL", "type": "url", "required": True, "placeholder": "http://192.168.1.2"},
        {"key": "password", "label": "Password (v6)", "type": "secret",
         "help": "Pi-hole v6: the web interface password or an app password. Leave empty if none is set."},
        {"key": "api_token", "label": "API token (v5)", "type": "secret",
         "help": "Pi-hole v5: Settings -> API -> Show API token. Needed only to switch blocking."},
        {"key": "version", "label": "Pi-hole version", "type": "select", "default": "auto",
         "options": [{"value": "auto", "label": "Detect automatically"}, {"value": "6", "label": "v6"}, {"value": "5", "label": "v5"}]},
    ],
    actions={
        "set_blocking": Action(
            set_blocking,
            label="Set blocking",
            description="Turn ad blocking on or off, optionally for a number of seconds.",
            payload=[
                {"key": "enabled", "label": "Blocking enabled", "type": "checkbox", "required": True},
                {"key": "seconds", "label": "Disable for (seconds)", "type": "number", "min": 1, "max": 86400},
            ],
        )
    },
)
