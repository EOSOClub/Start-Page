"""Uptime Kuma: monitors from a public status page (no API key)."""
from urllib.parse import quote

from ..base import Context, Spec, stat

# Kuma: 0 down, 1 up, 2 pending, 3 maintenance
_STATUS = {0: "offline", 1: "online", 2: "degraded", 3: "maintenance"}


async def fetch(ctx: Context) -> dict:
    base = ctx.base_url()
    slug = quote(str(ctx.cfg.get("slug") or "default"), safe="")
    page = (await ctx.client.get(f"{base}/api/status-page/{slug}")).raise_for_status().json()
    hb = (await ctx.client.get(f"{base}/api/status-page/heartbeat/{slug}")).raise_for_status().json()
    heartbeats = hb.get("heartbeatList", {})
    uptime = hb.get("uptimeList", {})
    monitors = []
    for group in page.get("publicGroupList", []):
        for m in group.get("monitorList", []):
            beats = heartbeats.get(str(m["id"]), [])
            last = beats[-1] if beats else None
            status = _STATUS.get(last["status"] if last else None, "unknown")
            monitors.append({
                "id": m["id"],
                "name": m.get("name"),
                "group": group.get("name"),
                "status": status,
                "ping": last.get("ping") if last else None,
                "uptime_24h": uptime.get(f"{m['id']}_24"),
                "history": [b["status"] for b in beats[-30:]],
            })
    up = sum(1 for m in monitors if m["status"] == "online")
    down = sum(1 for m in monitors if m["status"] == "offline")
    return {
        "title": (page.get("config") or {}).get("title"),
        "monitors": monitors,
        "up": up,
        "total": len(monitors),
        "stats": [
            stat("up", "Up", up, tone="ok"),
            stat("down", "Down", down, tone="bad" if down else None),
            stat("total", "Monitors", len(monitors)),
        ],
        "items": [
            {
                "title": m["name"] or "?",
                "subtitle": m["group"] or None,
                "status": m["status"],
                **({"meta": f"{m['ping']} ms"} if m["ping"] is not None else {}),
            }
            for m in monitors
        ],
    }


SPEC = Spec(
    type="uptime_kuma",
    label="Uptime Kuma",
    description="Monitor status from an Uptime Kuma status page; no API key needed.",
    fetch=fetch,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "Uptime Kuma URL", "type": "url", "required": True, "placeholder": "http://192.168.1.20:3001"},
        {"key": "slug", "label": "Status page slug", "type": "text", "default": "default", "placeholder": "default",
         "help": "Create a status page in Kuma and add the monitors you want to show; no API key needed."},
    ],
)
