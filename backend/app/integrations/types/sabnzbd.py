"""SABnzbd: GET /api?mode=queue&output=json&apikey=KEY (the key travels in the query string).

The key is only ever passed through httpx `params`; nothing here builds a message from the URL or
the response body, and the framework's safe_error / redact strip the query from every HTTP error.
SABnzbd answers a wrong key with HTTP 200 and {"status": false, "error": "API Key Incorrect"}: that is
turned into a fixed message. Speed: `kbpersec` (KB/s) is scaled to a readable number and unit; the exact
bytes per second are in the top-level `speed_bps`.
"""
from typing import Any

from ..base import Action, Context, Spec, stat
from ..errors import IntegrationError

MAX_ITEMS = 25
_STATUS = {
    "downloading": "online", "fetching": "online", "grabbing": "online", "extracting": "online", "verifying": "online",
    "repairing": "online", "moving": "online", "running": "online",
    "paused": "maintenance", "queued": "unknown", "propagating": "unknown", "checking": "maintenance",
    "failed": "offline",
}


async def _call(ctx: Context, **params: Any) -> dict:
    resp = await ctx.client.get(ctx.url("/api"), params={**params, "output": "json", "apikey": ctx.cfg.get("api_key") or ""})
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise IntegrationError("invalid response from SABnzbd")
    if body.get("status") is False or ("error" in body and "queue" not in body):
        raise IntegrationError("SABnzbd rejected the request (check the API key)")  # the server's text is not echoed
    return body


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rate(kb_per_s: float) -> tuple[float, str]:
    if kb_per_s < 1024:
        return round(kb_per_s, 1), "KB/s"
    if kb_per_s < 1024 * 1024:
        return round(kb_per_s / 1024, 1), "MB/s"
    return round(kb_per_s / 1024 / 1024, 1), "GB/s"


async def fetch(ctx: Context) -> dict:
    body = await _call(ctx, mode="queue", limit=MAX_ITEMS)
    q = body.get("queue")
    if not isinstance(q, dict):
        raise IntegrationError("invalid response from SABnzbd")
    kbps = _num(q.get("kbpersec")) or 0.0
    speed, unit = _rate(kbps)
    paused = bool(q.get("paused"))
    count = q.get("noofslots_total", q.get("noofslots"))
    slots = [s for s in (q.get("slots") or []) if isinstance(s, dict)]
    items = []
    for s in slots[:MAX_ITEMS]:
        status = str(s.get("status") or "").lower()
        pct = _num(s.get("percentage"))
        if pct is None and _num(s.get("mb")):
            pct = (_num(s.get("mb")) - (_num(s.get("mbleft")) or 0)) / _num(s.get("mb")) * 100
        item: dict = {"title": str(s.get("filename") or "Unnamed"), "status": _STATUS.get(status, "unknown")}
        sub = " | ".join(p for p in (s.get("cat") if s.get("cat") not in (None, "*") else "", s.get("size")) if p)
        if sub:
            item["subtitle"] = str(sub)
        if pct is not None:
            item["progress"] = round(min(100.0, max(0.0, pct)), 1)
        if s.get("timeleft") and status != "paused":
            item["meta"] = str(s["timeleft"])
        items.append(item)
    return {
        "speed_bps": int(kbps * 1024),
        "stats": [
            stat("speed", "Speed", speed, unit, "info" if kbps and not paused else None),
            stat("left", "Remaining", str(q.get("sizeleft") or "0 B")),
            stat("timeleft", "Time left", str(q.get("timeleft") or "0:00:00")),
            stat("queue", "Queue", int(count) if _num(count) is not None else len(slots)),
            stat("paused", "Paused", paused, tone="warn" if paused else None),
        ],
        "items": items,
    }


async def _mode(ctx: Context, mode: str) -> None:
    body = await _call(ctx, mode=mode)
    if body.get("status") is not True:
        raise IntegrationError("SABnzbd did not accept the command")


async def pause(ctx: Context, payload: dict) -> None:
    await _mode(ctx, "pause")


async def resume(ctx: Context, payload: dict) -> None:
    await _mode(ctx, "resume")


SPEC = Spec(
    type="sabnzbd",
    label="SABnzbd",
    description="Download speed, queue size and queued jobs from SABnzbd, with pause and resume.",
    fetch=fetch,
    default_interval=10,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "SABnzbd URL", "type": "url", "required": True, "placeholder": "http://192.168.1.10:8080/sabnzbd"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True,
         "help": "SABnzbd -> Config -> General -> API Key (not the NZB key)."},
    ],
    actions={
        "pause": Action(pause, label="Pause", description="Pause the whole queue."),
        "resume": Action(resume, label="Resume", description="Resume the queue."),
    },
)
