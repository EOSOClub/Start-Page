"""Searchable list of dashboard-icons names, fetched once and cached in memory."""
import logging
import time

import httpx
from fastapi import APIRouter, Query

log = logging.getLogger("startpage.icons")
router = APIRouter(prefix="/api/icons", tags=["icons"])

LIST_URL = "https://data.jsdelivr.com/v1/package/gh/walkxcode/dashboard-icons@main/flat"
_cache: dict = {"names": [], "fetched": 0.0}


async def _names() -> list[str]:
    if _cache["names"] and time.time() - _cache["fetched"] < 86400:
        return _cache["names"]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            data = (await client.get(LIST_URL)).raise_for_status().json()
        names = sorted(
            f["name"][len("/png/"):-len(".png")]
            for f in data.get("files", [])
            if f["name"].startswith("/png/") and f["name"].endswith(".png")
        )
        if names:
            _cache.update(names=names, fetched=time.time())
    except Exception as exc:  # noqa: BLE001
        log.warning("icon list unavailable: %s", exc)
    return _cache["names"]


@router.get("")
async def search_icons(q: str = Query(""), limit: int = Query(60, le=500)):
    names = await _names()
    q = q.lower().strip()
    if q:
        starts = [n for n in names if n.startswith(q)]
        contains = [n for n in names if q in n and not n.startswith(q)]
        names = starts + contains
    return {"total": len(names), "icons": names[:limit]}
