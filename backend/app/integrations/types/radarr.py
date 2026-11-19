"""Radarr (API v3, header X-Api-Key): queue, missing, movie total and upcoming releases, plus a list.

Requests: GET /api/v3/queue (includeMovie), /api/v3/wanted/missing (pageSize=1, only totalRecords is
read), /api/v3/calendar (today .. today+7 days) and /api/v3/movie (counted).
The `list` option picks what `items` shows: upcoming releases (default) or the download queue.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..base import Context, Spec, cached, stat

MAX_ITEMS = 25
UPCOMING_DAYS = 7
_RELEASES = (("digitalRelease", "digital"), ("physicalRelease", "physical"), ("inCinemas", "cinema"))


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


def _release(movie: dict, start: str, end: str) -> tuple[Optional[str], Optional[str]]:
    """(date, kind) of the release inside the window, else the earliest known one."""
    known = sorted((str(movie[k])[:10], kind) for k, kind in _RELEASES if movie.get(k))
    inside = [r for r in known if start <= r[0] <= end]
    pick = (inside or known or [(None, None)])[0]
    return pick[0], pick[1]


def _title(movie: dict) -> str:
    title = movie.get("title") or "Unknown movie"
    return f"{title} ({movie['year']})" if movie.get("year") else title


def _upcoming_items(calendar: list, start: str, end: str) -> list[dict]:
    rows = []
    for m in (x for x in calendar if isinstance(x, dict)):
        date, kind = _release(m, start, end)
        rows.append((date or "9999", m, date, kind))
    items = []
    for _, m, date, kind in sorted(rows, key=lambda r: r[0]):
        item: dict = {"title": _title(m), "status": "online" if m.get("hasFile") else "unknown"}
        if kind:
            item["subtitle"] = f"{kind.capitalize()} release"
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
        status = str(r.get("status") or "").lower()
        item: dict = {
            "title": _title(r["movie"]) if isinstance(r.get("movie"), dict) else (r.get("title") or "Unknown"),
            "status": "online" if status == "downloading" else "degraded" if status in ("warning", "failed") else "unknown",
        }
        pct = _pct(r.get("size"), r.get("sizeleft"))
        if pct is not None:
            item["progress"] = pct
        if r.get("timeleft"):
            item["meta"] = str(r["timeleft"])
        items.append(item)
    return items[:MAX_ITEMS]


async def fetch(ctx: Context) -> dict:
    today = datetime.now(timezone.utc).date()
    start, end = today.isoformat(), (today + timedelta(days=UPCOMING_DAYS)).isoformat()
    queue = await _get(ctx, "/api/v3/queue", {"page": 1, "pageSize": MAX_ITEMS, "includeMovie": "true"})
    missing = await _get(ctx, "/api/v3/wanted/missing", {"page": 1, "pageSize": 1})
    calendar = await _get(ctx, "/api/v3/calendar", {"start": start, "end": end})
    movie_count = await cached(ctx, "movie_count", LIBRARY_TTL, lambda: _count(ctx, "/api/v3/movie"))
    records = (queue or {}).get("records") or []
    calendar = calendar if isinstance(calendar, list) else []
    queue_total = (queue or {}).get("totalRecords", len(records))
    missing_total = (missing or {}).get("totalRecords", 0)
    items = _queue_items(records) if ctx.cfg.get("list") == "queue" else _upcoming_items(calendar, start, end)
    return {
        "stats": [
            stat("queue", "Queue", queue_total),
            stat("missing", "Missing", missing_total, tone="warn" if missing_total else None),
            stat("movies", "Movies", movie_count),
            stat("upcoming", f"Next {UPCOMING_DAYS} days", len(calendar)),
        ],
        "items": items,
    }


SPEC = Spec(
    type="radarr",
    label="Radarr",
    description="Movie library: queue, missing and upcoming releases from Radarr (API v3).",
    fetch=fetch,
    default_interval=60,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "Radarr URL", "type": "url", "required": True, "placeholder": "http://192.168.1.10:7878"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True, "help": "Radarr -> Settings -> General -> API Key."},
        {"key": "list", "label": "List movies", "type": "select", "default": "upcoming",
         "options": [{"value": "upcoming", "label": "Upcoming releases (7 days)"}, {"value": "queue", "label": "Download queue"}]},
    ],
)
