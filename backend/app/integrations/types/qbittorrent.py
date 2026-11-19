"""qBittorrent (Web API v2): transfer speeds, torrent counts, active torrents, pause/resume all.

Login: POST /api/v2/auth/login (form username/password) answers "Ok." and a SID cookie, kept in
ctx.state["sid"]. A 403 on any request means the cookie expired: log in once more and retry.
With no username/password configured no login is attempted (qBittorrent's "bypass authentication
for clients on localhost / in a subnet" setting); a 403 then says credentials are needed.
Speeds: `value` is scaled to a readable number and `unit` is "KB/s" / "MB/s" (the frontend prints
value + unit as is); the exact bytes per second are in the top-level `speeds` dict.
Pause/resume: v5 renamed /torrents/pause|resume to /torrents/stop|start; the new one is tried first
and the old one is used when it answers 404.
"""
from typing import Any

import httpx

from ..base import Action, Context, Spec, stat
from ..errors import IntegrationError

MAX_ITEMS = 25
_FAILED = "login failed: check the qBittorrent user name and password"
BIG_ETA = 8_640_000  # qBittorrent's "infinity"
_DOWNLOADING = {"downloading", "forceddl", "metadl", "forcedmetadl"}
_DL_ALL = _DOWNLOADING | {"stalleddl", "queueddl", "checkingdl", "allocating"}
_SEEDING = {"uploading", "forcedup", "stalledup", "queuedup", "checkingup"}
_STATUS = {
    "downloading": "online", "forceddl": "online", "metadl": "online", "forcedmetadl": "online",
    "uploading": "online", "forcedup": "online",
    "stalleddl": "degraded", "stalledup": "degraded",
    "queueddl": "maintenance", "queuedup": "maintenance", "checkingdl": "maintenance", "checkingup": "maintenance",
    "checkingresumedata": "maintenance", "allocating": "maintenance", "moving": "maintenance",
    "pauseddl": "maintenance", "pausedup": "maintenance", "stoppeddl": "maintenance", "stoppedup": "maintenance",
    "error": "offline", "missingfiles": "offline",
}


def rate(bytes_per_s: Any) -> tuple[float, str]:
    """(value, unit) for a bytes/s speed: 1.5 MB/s -> (1.5, "MB/s")."""
    v = float(bytes_per_s or 0)
    for unit in ("B/s", "KB/s", "MB/s"):
        if v < 1024:
            return round(v, 1), unit
        v /= 1024
    return round(v, 1), "GB/s"


def _size(n: Any) -> str:
    v = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.1f} {unit}" if unit != "B" else f"{int(v)} B"
        v /= 1024
    return f"{v:.1f} TB"


def _eta(seconds: Any) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 0 or s >= BIG_ETA:
        return ""
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


async def _login(ctx: Context) -> None:
    ctx.auth_gate(_FAILED)  # no request while the backoff after a refused login runs
    resp = await ctx.client.post(
        ctx.url("/api/v2/auth/login"),
        data={"username": ctx.cfg.get("username") or "", "password": ctx.cfg.get("password") or ""},
        headers={"Referer": ctx.base_url()},
    )
    if resp.status_code in (401, 403):
        raise ctx.auth_fail("login failed: check the qBittorrent user name and password, or the IP ban")
    resp.raise_for_status()
    sid = resp.cookies.get("SID")
    if resp.text.strip().lower().startswith("fails") or not sid:
        raise ctx.auth_fail(_FAILED)
    ctx.state["sid"] = sid


async def close(ctx: Context) -> None:
    """Log the session out (qBittorrent keeps sessions until they expire)."""
    sid = ctx.state.pop("sid", None)
    if sid:
        await ctx.client.post(ctx.url("/api/v2/auth/logout"), headers={"Referer": ctx.base_url(), "Cookie": f"SID={sid}"})


def _has_credentials(ctx: Context) -> bool:
    return bool(ctx.cfg.get("username") or ctx.cfg.get("password"))


async def _request(ctx: Context, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One call with the session cookie; a 403 triggers one fresh login and a retry."""
    for attempt in (0, 1):
        if _has_credentials(ctx) and not ctx.state.get("sid"):
            await _login(ctx)
        headers = {"Referer": ctx.base_url()}
        if ctx.state.get("sid"):
            headers["Cookie"] = f"SID={ctx.state['sid']}"
        resp = await ctx.client.request(method, ctx.url(path), headers=headers, **kwargs)
        if resp.status_code == 403:
            if not _has_credentials(ctx):
                raise IntegrationError("authentication required: set the qBittorrent user name and password")
            ctx.state.pop("sid", None)
            if attempt == 0:
                continue  # cookie expired: one fresh login; a second 403 is a failure with backoff
            raise ctx.auth_fail(_FAILED)
        return resp
    raise IntegrationError("login failed")  # unreachable


async def _json(ctx: Context, path: str, params: dict | None = None) -> Any:
    resp = await _request(ctx, "GET", path, params=params)
    resp.raise_for_status()
    return resp.json()


async def fetch(ctx: Context) -> dict:
    info = await _json(ctx, "/api/v2/transfer/info")
    torrents = await _json(ctx, "/api/v2/torrents/info")
    info = info if isinstance(info, dict) else {}
    torrents = [t for t in torrents if isinstance(t, dict)] if isinstance(torrents, list) else []
    dl, up = info.get("dl_info_speed") or 0, info.get("up_info_speed") or 0

    def state(t: dict) -> str:
        return str(t.get("state") or "").lower()

    active = [t for t in torrents if (t.get("dlspeed") or 0) > 0 or (t.get("upspeed") or 0) > 0 or state(t) in _DOWNLOADING]
    downloading = sum(1 for t in torrents if state(t) in _DL_ALL)
    seeding = sum(1 for t in torrents if state(t) in _SEEDING)
    listed = [t for t in torrents if t in active or state(t) in _DL_ALL]
    listed.sort(key=lambda t: ((t.get("dlspeed") or 0) + (t.get("upspeed") or 0)), reverse=True)

    items = []
    for t in listed[:MAX_ITEMS]:
        parts = []
        if (t.get("dlspeed") or 0) > 0:
            parts.append("down {} {}".format(*rate(t["dlspeed"])))
        if (t.get("upspeed") or 0) > 0:
            parts.append("up {} {}".format(*rate(t["upspeed"])))
        eta = _eta(t.get("eta")) if state(t) in _DL_ALL else ""
        if eta:
            parts.append(f"ETA {eta}")
        item: dict = {
            "title": str(t.get("name") or "Unnamed torrent"),
            "status": _STATUS.get(state(t), "unknown"),
            "subtitle": _size(t.get("size") or t.get("total_size")),
        }
        if isinstance(t.get("progress"), (int, float)):
            item["progress"] = round(min(100.0, max(0.0, t["progress"] * 100)), 1)
        if parts:
            item["meta"] = " | ".join(parts)
        items.append(item)

    dv, du = rate(dl)
    uv, uu = rate(up)
    return {
        "speeds": {"download": dl, "upload": up},
        "stats": [
            stat("download", "Download", dv, du, "info" if dl else None),
            stat("upload", "Upload", uv, uu, "info" if up else None),
            stat("active", "Active", len(active)),
            stat("downloading", "Downloading", downloading),
            stat("seeding", "Seeding", seeding),
            stat("total", "Torrents", len(torrents)),
        ],
        "items": items,
    }


async def _all(ctx: Context, new: str, old: str) -> None:
    resp = await _request(ctx, "POST", f"/api/v2/torrents/{new}", data={"hashes": "all"})
    if resp.status_code == 404:  # qBittorrent v4: pause / resume
        resp = await _request(ctx, "POST", f"/api/v2/torrents/{old}", data={"hashes": "all"})
    resp.raise_for_status()


async def pause_all(ctx: Context, payload: dict) -> None:
    await _all(ctx, "stop", "pause")


async def resume_all(ctx: Context, payload: dict) -> None:
    await _all(ctx, "start", "resume")


SPEC = Spec(
    type="qbittorrent",
    label="qBittorrent",
    description="Transfer speeds, torrent counts and active downloads from qBittorrent, with pause and resume all.",
    fetch=fetch,
    close=close,
    default_interval=10,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "qBittorrent URL", "type": "url", "required": True, "placeholder": "http://192.168.1.10:8080"},
        {"key": "username", "label": "User name", "type": "text",
         "help": "Leave user name and password empty when authentication is bypassed for this host."},
        {"key": "password", "label": "Password", "type": "secret"},
    ],
    actions={
        "pause_all": Action(pause_all, label="Pause all", description="Pause every torrent."),
        "resume_all": Action(resume_all, label="Resume all", description="Resume every torrent."),
    },
)
