"""Sonarr (API v3, header X-Api-Key): queue, missing, upcoming and series counts, plus a list.

Requests: GET /api/v3/queue (includeSeries/includeEpisode), /api/v3/wanted/missing (pageSize=1, only
totalRecords is read), /api/v3/calendar (today .. today+7 days, includeSeries) and /api/v3/series (counted).
The `list` option picks what `items` shows: upcoming episodes (default) or the download queue.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..base import Context, Spec, cached, stat

MAX_ITEMS = 25
UPCOMING_DAYS = 7


async def _get(ctx: Context, path: str, params: Optional[dict] = None) -> Any:
    resp = await ctx.client.get(ctx.url(path), params=params, headers={"X-Api-Key": str(ctx.cfg.get("api_key") or "")})
    resp.raise_for_status()
    return resp.json()


def _pct(size: Any, left: Any) -> Optional[float]:
    try:
        size, left = float(size), float(left)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    return round(min(100.0, max(0.0, (size - left) / size * 100)), 1)


def _episode_code(season: Any, number: Any) -> str:
    if isinstance(season, int) and isinstance(number, int):
        return f"S{season:02d}E{number:02d}"
    return ""


def _upcoming_items(calendar: list) -> list[dict]:
    items = []
    for ep in sorted((e for e in calendar if isinstance(e, dict)), key=lambda e: str(e.get("airDateUtc") or e.get("airDate") or "")):
        series = (ep.get("series") or {}).get("title") or "Unknown series"
        code = _episode_code(ep.get("seasonNumber"), ep.get("episodeNumber"))
        subtitle = " ".join(p for p in (code, ep.get("title") or "") if p) or None
        date = str(ep.get("airDateUtc") or ep.get("airDate") or "")[:10] or None
        item: dict = {"title": series, "status": "online" if ep.get("hasFile") else "unknown"}
        if subtitle:
            item["subtitle"] = subtitle
        if date:
            item["meta"] = date
        items.append(item)
    return items[:MAX_ITEMS]


LIBRARY_TTL = 900.0  # the full library is downloaded only to be counted: at most every 15 minutes


async def _count(ctx: Context, path: str) -> Optional[int]:
    rows = await _get(ctx, path)
    return len(rows) if isinstance(rows, list) else None


def _queue_items(records: list) -> list[dict]:
    items = []
    for r in (x for x in records if isinstance(x, dict)):
        ep = r.get("episode") or {}
        series = (r.get("series") or {}).get("title") or r.get("title") or "Unknown"
        code = _episode_code(ep.get("seasonNumber"), ep.get("episodeNumber"))
        subtitle = " ".join(p for p in (code, ep.get("title") or "") if p) or None
        status = str(r.get("status") or "").lower()
        item: dict = {"title": series, "status": "online" if status == "downloading" else "degraded" if status in ("warning", "failed") else "unknown"}
        if subtitle:
            item["subtitle"] = subtitle
        pct = _pct(r.get("size"), r.get("sizeleft"))
        if pct is not None:
            item["progress"] = pct
        if r.get("timeleft"):
            item["meta"] = str(r["timeleft"])
        items.append(item)
    return items[:MAX_ITEMS]


async def fetch(ctx: Context) -> dict:
    today = datetime.now(timezone.utc).date()
    queue = await _get(ctx, "/api/v3/queue", {"page": 1, "pageSize": MAX_ITEMS, "includeSeries": "true", "includeEpisode": "true"})
    missing = await _get(ctx, "/api/v3/wanted/missing", {"page": 1, "pageSize": 1})
    calendar = await _get(ctx, "/api/v3/calendar", {
        "start": today.isoformat(), "end": (today + timedelta(days=UPCOMING_DAYS)).isoformat(), "includeSeries": "true",
    })
    series_count = await cached(ctx, "series_count", LIBRARY_TTL, lambda: _count(ctx, "/api/v3/series"))
    records = (queue or {}).get("records") or []
    calendar = calendar if isinstance(calendar, list) else []
    queue_total = (queue or {}).get("totalRecords", len(records))
    missing_total = (missing or {}).get("totalRecords", 0)
    items = _queue_items(records) if ctx.cfg.get("list") == "queue" else _upcoming_items(calendar)
    return {
        "stats": [
            stat("queue", "Queue", queue_total),
            stat("missing", "Missing", missing_total, tone="warn" if missing_total else None),
            stat("upcoming", f"Next {UPCOMING_DAYS} days", len(calendar)),
            stat("series", "Series", series_count),
        ],
        "items": items,
    }


SPEC = Spec(
    type="sonarr",
    label="Sonarr",
    description="TV library: queue, missing and upcoming episodes from Sonarr (API v3).",
    fetch=fetch,
    default_interval=60,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "Sonarr URL", "type": "url", "required": True, "placeholder": "http://192.168.1.10:8989"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True, "help": "Sonarr -> Settings -> General -> API Key."},
        {"key": "list", "label": "List shows", "type": "select", "default": "upcoming",
         "options": [{"value": "upcoming", "label": "Upcoming episodes (7 days)"}, {"value": "queue", "label": "Download queue"}]},
    ],
)
