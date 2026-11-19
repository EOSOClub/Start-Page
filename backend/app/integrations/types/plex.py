"""Plex Media Server: active streams, transcodes, library counts and now playing.

Auth: header X-Plex-Token, with Accept: application/json (Plex answers XML otherwise).
Requests: GET /status/sessions (MediaContainer.Metadata, absent when idle), and, when `library_counts`
is on, GET /library/sections plus one /library/sections/{key}/all with X-Plex-Container-Size=0 per
movie / show section (only MediaContainer.totalSize is read). A failing section count is skipped.
Progress is viewOffset / duration (milliseconds).
"""
import asyncio
from typing import Any, Optional

import httpx

from ..base import Context, Spec, stat

MAX_ITEMS = 25
MAX_SECTIONS = 12
_STATUS = {"playing": "online", "paused": "maintenance", "buffering": "degraded"}


async def _get(ctx: Context, path: str, params: Optional[dict] = None) -> Any:
    resp = await ctx.client.get(
        ctx.url(path), params=params,
        headers={"X-Plex-Token": str(ctx.cfg.get("token") or ""), "Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def _title(m: dict) -> str:
    title = m.get("title") or "Unknown"
    if m.get("type") == "episode":
        season, number = m.get("parentIndex"), m.get("index")
        code = f"S{season:02d}E{number:02d}" if isinstance(season, int) and isinstance(number, int) else ""
        return " - ".join(p for p in (m.get("grandparentTitle"), code, title) if p)
    if m.get("type") == "track" and m.get("grandparentTitle"):
        return f"{m['grandparentTitle']} - {title}"
    return f"{title} ({m['year']})" if m.get("type") == "movie" and m.get("year") else title


def _progress(m: dict) -> Optional[float]:
    duration, offset = m.get("duration"), m.get("viewOffset")
    if not isinstance(duration, (int, float)) or duration <= 0:
        return None
    return round(min(100.0, max(0.0, (offset if isinstance(offset, (int, float)) else 0) / duration * 100)), 1)


def _transcoding(m: dict) -> bool:
    t = m.get("TranscodeSession")
    if not isinstance(t, dict):
        return False
    decisions = (t.get("videoDecision"), t.get("audioDecision"))
    return "transcode" in decisions or not any(decisions)


async def _section_count(ctx: Context, key: Any) -> Optional[int]:
    try:
        body = await _get(ctx, f"/library/sections/{key}/all", {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 0})
        total = (body.get("MediaContainer") or {}).get("totalSize")
        return int(total) if total is not None else None
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return None


async def _library_stats(ctx: Context) -> list[dict]:
    body = await _get(ctx, "/library/sections")
    sections = [d for d in ((body.get("MediaContainer") or {}).get("Directory") or []) if isinstance(d, dict) and d.get("type") in ("movie", "show")]
    sections = sections[:MAX_SECTIONS]
    counts = await asyncio.gather(*(_section_count(ctx, d.get("key")) for d in sections))
    out = []
    for kind, key, label in (("movie", "movies", "Movies"), ("show", "shows", "Shows")):
        mine = [c for d, c in zip(sections, counts) if d["type"] == kind and c is not None]
        if mine:
            out.append(stat(key, label, sum(mine)))
    return out


async def fetch(ctx: Context) -> dict:
    body = await _get(ctx, "/status/sessions")
    container = (body or {}).get("MediaContainer") or {}
    sessions = [m for m in (container.get("Metadata") or []) if isinstance(m, dict)]
    transcodes = sum(1 for m in sessions if _transcoding(m))
    items = []
    for m in sessions[:MAX_ITEMS]:
        player = m.get("Player") or {}
        state = str(player.get("state") or "").lower()
        mode = "Transcode" if _transcoding(m) else "Direct play"
        item: dict = {"title": _title(m), "status": _STATUS.get(state, "unknown")}
        user = (m.get("User") or {}).get("title")
        if user:
            item["subtitle"] = str(user)
        pct = _progress(m)
        if pct is not None:
            item["progress"] = pct
        item["meta"] = " | ".join(str(p) for p in (player.get("title") or player.get("product"), mode) if p)
        items.append(item)
    stats = [
        stat("streams", "Streams", len(sessions), tone="info" if sessions else None),
        stat("transcodes", "Transcoding", transcodes, tone="warn" if transcodes else None),
    ]
    if ctx.cfg.get("library_counts"):
        stats += await _library_stats(ctx)
    return {"stats": stats, "items": items}


SPEC = Spec(
    type="plex",
    label="Plex",
    description="Active streams, transcodes, now playing and library counts from Plex Media Server.",
    fetch=fetch,
    default_interval=15,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "Plex URL", "type": "url", "required": True, "placeholder": "http://192.168.1.10:32400"},
        {"key": "token", "label": "Plex token", "type": "secret", "required": True,
         "help": "An X-Plex-Token: open any item in Plex Web -> Get Info -> View XML and copy the token from the URL."},
        {"key": "library_counts", "label": "Show library counts", "type": "checkbox", "default": True,
         "help": "One small extra request per movie / show library on every poll."},
    ],
)
