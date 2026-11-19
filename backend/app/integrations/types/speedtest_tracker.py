"""Speedtest Tracker (alexjustesen/speedtest-tracker): last download, upload, ping and test time.

Current API (v1.x): GET /api/v1/results/latest with `Authorization: Bearer <token>` -> {"data": {...}};
the sparkline comes from GET /api/v1/results?per_page=20&sort=-created_at (newest first, reversed here).
Legacy API (0.x): GET /api/speedtest/latest, no auth -> {"data": {"download", "upload", "ping", ...}}.
The new endpoint is tried first; a 404 means "not that version" and the legacy one is tried (the choice is
remembered in ctx.state["api"]).

UNIT ASSUMPTIONS (output is always Mbit/s):
  v1: `download_bits` / `upload_bits` (bits per second) are preferred; otherwise `download` / `upload` are
      read as BYTES per second (Ookla's native bandwidth unit) and multiplied by 8.
  legacy: `download` / `upload` are read as megabits per second already (what 0.x stored), no conversion.
  ping is milliseconds in both.
"""
from typing import Any, Optional

import httpx

from ..base import Context, Spec, stat
from ..errors import AuthFailed, IntegrationError

SERIES_POINTS = 20


def _n(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return v if isinstance(v, (int, float)) else None


def _headers(ctx: Context) -> dict:
    token = ctx.cfg.get("token")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"} if token else {"Accept": "application/json"}


def _check(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise AuthFailed("login failed: check the Speedtest Tracker API token")
    resp.raise_for_status()


def _mbit(row: dict, key: str, legacy: bool) -> Optional[float]:
    if legacy:
        v = _n(row.get(key))
        return None if v is None else round(v, 2)
    bits = _n(row.get(f"{key}_bits"))
    if bits is not None:
        return round(bits / 1_000_000, 2)
    v = _n(row.get(key))
    return None if v is None else round(v * 8 / 1_000_000, 2)


def _row(body: Any) -> dict:
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else {}


async def _series(ctx: Context) -> dict[str, list]:
    out: dict[str, list] = {"download": [], "upload": [], "ping": []}
    try:
        resp = await ctx.client.get(ctx.url("/api/v1/results"), headers=_headers(ctx),
                                    params={"per_page": SERIES_POINTS, "sort": "-created_at"})
        if resp.status_code != 200:
            return out
        body = resp.json()
        rows = body.get("data") if isinstance(body, dict) else None
        for r in reversed(rows[:SERIES_POINTS] if isinstance(rows, list) else []):
            if not isinstance(r, dict):
                continue
            for key in ("download", "upload"):
                v = _mbit(r, key, False)
                if v is not None:
                    out[key].append(v)
            p = _n(r.get("ping"))
            if p is not None:
                out["ping"].append(round(p, 2))
    except (httpx.HTTPError, ValueError):
        pass  # the sparkline is optional
    return out


async def fetch(ctx: Context) -> dict:
    row: dict = {}
    legacy = ctx.state.get("api") == "legacy"
    if not legacy:
        resp = await ctx.client.get(ctx.url("/api/v1/results/latest"), headers=_headers(ctx))
        if resp.status_code == 404:
            legacy = True
        else:
            _check(resp)
            row = _row(resp.json())
            ctx.state["api"] = "v1"
    if legacy:
        resp = await ctx.client.get(ctx.url("/api/speedtest/latest"), headers={"Accept": "application/json"})
        if resp.status_code == 404 and ctx.state.get("api") != "legacy":
            raise IntegrationError("not found: is this a Speedtest Tracker URL?")
        _check(resp)
        row = _row(resp.json())
        ctx.state["api"] = "legacy"
    series = {} if legacy else await _series(ctx)
    ping = _n(row.get("ping"))
    return {
        "api": ctx.state["api"],
        "stats": [
            stat("download", "Download", _mbit(row, "download", legacy), "Mbit/s", series=series.get("download")),
            stat("upload", "Upload", _mbit(row, "upload", legacy), "Mbit/s", series=series.get("upload")),
            stat("ping", "Ping", None if ping is None else round(ping, 2), "ms", series=series.get("ping")),
            stat("last_test", "Last test", row.get("created_at") or row.get("updated_at")),
        ],
    }


SPEC = Spec(
    type="speedtest_tracker",
    label="Speedtest Tracker",
    description="Latest download, upload and ping from a Speedtest Tracker instance.",
    fetch=fetch,
    default_interval=300,
    provides=("stats",),
    fields=[
        {"key": "url", "label": "Speedtest Tracker URL", "type": "url", "required": True, "placeholder": "http://192.168.1.10:8080"},
        {"key": "token", "label": "API token", "type": "secret",
         "help": "Needed by current versions (Profile -> API tokens, ability results:read). Not used by 0.x versions."},
    ],
)
