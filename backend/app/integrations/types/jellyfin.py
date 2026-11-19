"""Jellyfin (and, by the same API shape, Emby): active streams, library counts, now playing.

Auth: header X-Emby-Token with an API key (Dashboard -> API Keys). Jellyfin and Emby both accept it
(Jellyfin also accepts `Authorization: MediaBrowser Token=`, Emby does not), so one header serves both.
Requests: GET /Sessions?ActiveWithinSeconds=960 (sessions that have NowPlayingItem are streams) and
GET /Items/Counts (MovieCount, SeriesCount, EpisodeCount). Progress is PositionTicks / RunTimeTicks.
"""
from typing import Any, Optional

from ..base import Context, Spec, stat

MAX_ITEMS = 25


async def _get(ctx: Context, path: str, params: Optional[dict] = None) -> Any:
    resp = await ctx.client.get(ctx.url(path), params=params, headers={"X-Emby-Token": str(ctx.cfg.get("api_key") or "")})
    resp.raise_for_status()
    return resp.json()


def _title(item: dict) -> str:
    name = item.get("Name") or "Unknown"
    if item.get("Type") == "Episode" or item.get("SeriesName"):
        season, number = item.get("ParentIndexNumber"), item.get("IndexNumber")
        code = f"S{season:02d}E{number:02d}" if isinstance(season, int) and isinstance(number, int) else ""
        return " - ".join(p for p in (item.get("SeriesName"), code, name) if p)
    artists = item.get("Artists")
    if item.get("Type") == "Audio" and isinstance(artists, list) and artists:
        return f"{artists[0]} - {name}"
    return name


def _progress(session: dict, item: dict) -> Optional[float]:
    runtime = item.get("RunTimeTicks")
    position = (session.get("PlayState") or {}).get("PositionTicks")
    if not isinstance(runtime, (int, float)) or runtime <= 0 or not isinstance(position, (int, float)):
        return None
    return round(min(100.0, max(0.0, position / runtime * 100)), 1)


async def fetch(ctx: Context) -> dict:
    sessions = await _get(ctx, "/Sessions", {"ActiveWithinSeconds": 960})
    counts = await _get(ctx, "/Items/Counts")
    playing = [s for s in (sessions if isinstance(sessions, list) else []) if isinstance(s, dict) and isinstance(s.get("NowPlayingItem"), dict)]
    counts = counts if isinstance(counts, dict) else {}
    transcodes = sum(1 for s in playing if str((s.get("PlayState") or {}).get("PlayMethod") or "").lower() == "transcode")
    items = []
    for s in playing[:MAX_ITEMS]:
        state = s.get("PlayState") or {}
        method = state.get("PlayMethod")
        device = s.get("DeviceName") or s.get("Client")
        meta = " | ".join(str(p) for p in (device, method, "paused" if state.get("IsPaused") else None) if p)
        item: dict = {
            "title": _title(s["NowPlayingItem"]),
            "status": "maintenance" if state.get("IsPaused") else "online",
        }
        if s.get("UserName"):
            item["subtitle"] = str(s["UserName"])
        pct = _progress(s, s["NowPlayingItem"])
        if pct is not None:
            item["progress"] = pct
        if meta:
            item["meta"] = meta
        items.append(item)
    return {
        "stats": [
            stat("streams", "Streams", len(playing), tone="info" if playing else None),
            stat("transcodes", "Transcoding", transcodes, tone="warn" if transcodes else None),
            stat("movies", "Movies", counts.get("MovieCount")),
            stat("series", "Series", counts.get("SeriesCount")),
            stat("episodes", "Episodes", counts.get("EpisodeCount")),
        ],
        "items": items,
    }


SPEC = Spec(
    type="jellyfin",
    label="Jellyfin / Emby",
    description="Active streams, now playing and library counts from Jellyfin (Emby is expected to work too).",
    fetch=fetch,
    default_interval=15,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "Server URL", "type": "url", "required": True, "placeholder": "http://192.168.1.10:8096"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True, "help": "Dashboard -> API Keys -> add a key."},
    ],
)
