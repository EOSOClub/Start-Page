"""Integration providers: the backend polls external APIs and caches the result so the
frontend never has to deal with CORS, sockets or credentials.

Provider types and their `config`:
  docker      {"url": "unix:///var/run/docker.sock" | "http://host:2375", "show_all": true}
  uptime_kuma {"url": "http://kuma:3001", "slug": "status-page-slug"}
  json        {"url": "...", "headers": {...}, "method": "GET"}
  host        {} (no config needed — reads the local machine via psutil)
  rss         {"url": "https://example.com/feed.xml"} — RSS/Atom feed (feedparser, lazy import)
"""
import asyncio
import calendar
import logging
import os
import socket
import time
from typing import Any

import httpx

from . import models
from .database import SessionLocal

log = logging.getLogger("startpage.integrations")

_data: dict[str, dict] = {}
_next: dict[str, float] = {}
_task: asyncio.Task | None = None

TYPES = ["docker", "uptime_kuma", "json", "host", "rss"]


def get_all() -> dict[str, dict]:
    return _data


def get(integration_id: str) -> dict | None:
    return _data.get(integration_id)


# ---------------- Docker ----------------
def _docker_client(cfg: dict) -> tuple[httpx.AsyncClient, str]:
    url = (cfg.get("url") or "unix:///var/run/docker.sock").strip()
    if url.startswith("unix://"):
        transport = httpx.AsyncHTTPTransport(uds=url[len("unix://"):])
        return httpx.AsyncClient(transport=transport, timeout=8.0), "http://docker"
    return httpx.AsyncClient(timeout=8.0, verify=False), url.rstrip("/")


async def _fetch_docker(cfg: dict) -> Any:
    client, base = _docker_client(cfg)
    async with client:
        all_flag = "1" if cfg.get("show_all", True) else "0"
        containers = (await client.get(f"{base}/containers/json", params={"all": all_flag})).raise_for_status().json()
        info = (await client.get(f"{base}/info")).raise_for_status().json()
    rows = []
    for c in containers:
        rows.append({
            "id": c["Id"][:12],
            "name": (c.get("Names") or ["?"])[0].lstrip("/"),
            "image": c.get("Image"),
            "state": c.get("State"),
            "status": c.get("Status"),
            "ports": sorted({f"{p.get('PublicPort')}->{p.get('PrivatePort')}" for p in c.get("Ports", []) if p.get("PublicPort")}),
            "project": (c.get("Labels") or {}).get("com.docker.compose.project", ""),
        })
    rows.sort(key=lambda r: (r["project"], r["name"]))
    return {
        "host": info.get("Name"),
        "version": info.get("ServerVersion"),
        "containers": rows,
        "running": info.get("ContainersRunning"),
        "stopped": info.get("ContainersStopped"),
        "paused": info.get("ContainersPaused"),
        "total": info.get("Containers"),
        "cpus": info.get("NCPU"),
        "mem_total": info.get("MemTotal"),
    }


async def docker_action(cfg: dict, container_id: str, action: str) -> None:
    if action not in ("start", "stop", "restart"):
        raise ValueError("Unsupported action")
    client, base = _docker_client(cfg)
    async with client:
        (await client.post(f"{base}/containers/{container_id}/{action}", timeout=30.0)).raise_for_status()


# ---------------- Uptime Kuma ----------------
async def _fetch_uptime_kuma(cfg: dict) -> Any:
    base = (cfg.get("url") or "").rstrip("/")
    slug = cfg.get("slug") or "default"
    async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
        page = (await client.get(f"{base}/api/status-page/{slug}")).raise_for_status().json()
        hb = (await client.get(f"{base}/api/status-page/heartbeat/{slug}")).raise_for_status().json()
    heartbeats = hb.get("heartbeatList", {})
    uptime = hb.get("uptimeList", {})
    monitors = []
    for group in page.get("publicGroupList", []):
        for m in group.get("monitorList", []):
            beats = heartbeats.get(str(m["id"]), [])
            last = beats[-1] if beats else None
            # Kuma: 0 down, 1 up, 2 pending, 3 maintenance
            status = {0: "offline", 1: "online", 2: "degraded", 3: "maintenance"}.get(last["status"] if last else None, "unknown")
            monitors.append({
                "id": m["id"],
                "name": m.get("name"),
                "group": group.get("name"),
                "status": status,
                "ping": last.get("ping") if last else None,
                "uptime_24h": uptime.get(f"{m['id']}_24"),
                "history": [b["status"] for b in beats[-30:]],
            })
    return {
        "title": (page.get("config") or {}).get("title"),
        "monitors": monitors,
        "up": sum(1 for m in monitors if m["status"] == "online"),
        "total": len(monitors),
    }


# ---------------- Generic JSON ----------------
async def _fetch_json(cfg: dict) -> Any:
    async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
        resp = await client.request(
            cfg.get("method", "GET"),
            cfg["url"],
            headers=cfg.get("headers") or {},
            follow_redirects=True,
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"text": resp.text}


# ---------------- System stats (host) ----------------
def _read_system(psutil):
    """Sync helper — runs in a thread via asyncio.to_thread."""
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(os.path.abspath(os.sep))
    return {
        "hostname": socket.gethostname(),
        "cpu_percent": psutil.cpu_percent(None),
        "mem_percent": vm.percent, "mem_used": vm.used, "mem_total": vm.total,
        "disk_percent": du.percent, "disk_used": du.used, "disk_total": du.total,
    }


async def _fetch_system(cfg):
    import psutil  # lazy: missing dep = error tile, not backend outage
    return await asyncio.to_thread(_read_system, psutil)


# ---------------- RSS / Atom ----------------
RSS_MAX_BYTES = 5_000_000


async def _read_limited(resp: httpx.Response, limit: int) -> bytes:
    """Stream up to `limit` bytes, truncating (never rejecting) an oversized body.
    Mirrors routers/favicons._read_limited; buffering bound ≈ limit + one chunk."""
    chunks, total = [], 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            break
    return b"".join(chunks)[:limit]


async def _fetch_rss(cfg: dict) -> Any:
    async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
        async with client.stream("GET", cfg["url"], follow_redirects=True) as resp:
            resp.raise_for_status()
            body = await _read_limited(resp, RSS_MAX_BYTES)
    import feedparser  # lazy: missing dep = error tile, not backend outage
    # Parsing is CPU-bound and unbounded in time, so it must not run on the event loop
    # (ADR 006). The 5 MB cap keeps a huge or entity-amplified feed proportionate — it
    # caps the download too (streamed, truncated — never rejected).
    parsed = await asyncio.to_thread(feedparser.parse, body)
    entries = []
    for e in parsed.entries[:30]:
        when = e.get("published_parsed")
        entries.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            # feedparser parses dates as UTC; epoch seconds keep it JSON-safe (struct_time is not).
            "published_parsed": calendar.timegm(when) if when else None,
            "summary": (e.get("summary") or "")[:500],
        })
    return {"title": parsed.feed.get("title", ""), "entries": entries}


FETCHERS = {"docker": _fetch_docker, "uptime_kuma": _fetch_uptime_kuma, "json": _fetch_json, "host": _fetch_system, "rss": _fetch_rss}


async def fetch(integration: models.Integration) -> dict:
    fetcher = FETCHERS.get(integration.type)
    if not fetcher:
        return {"integration_id": integration.id, "ok": False, "error": f"Unknown type {integration.type}", "fetched_at": time.time()}
    try:
        data = await fetcher(integration.config or {})
        return {"integration_id": integration.id, "ok": True, "data": data, "fetched_at": time.time(), "error": None}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or type(exc).__name__
        return {"integration_id": integration.id, "ok": False, "error": msg[:300], "fetched_at": time.time(),
                "data": (_data.get(integration.id) or {}).get("data")}


async def refresh(integration_id: str) -> dict | None:
    with SessionLocal() as db:
        integ = db.get(models.Integration, integration_id)
        if not integ:
            return None
        interval = integ.interval or 30
        result = await fetch(integ)
    _data[integration_id] = result
    _next[integration_id] = time.time() + interval
    return result


async def _loop():
    while True:
        try:
            now = time.time()
            due: list[models.Integration] = []
            live: set[str] = set()
            with SessionLocal() as db:
                for i in db.query(models.Integration).all():
                    live.add(i.id)
                    if not i.enabled:
                        _data.pop(i.id, None)
                        continue
                    if _next.get(i.id, 0) <= now:
                        db.expunge(i)
                        due.append(i)
            for stale in set(_data) - live:
                _data.pop(stale, None)
                _next.pop(stale, None)
            if due:
                results = await asyncio.gather(*(fetch(i) for i in due))
                for integ, result in zip(due, results):
                    _data[integ.id] = result
                    _next[integ.id] = time.time() + (integ.interval or 30)
        except Exception:  # noqa: BLE001
            log.exception("integration loop error")
        await asyncio.sleep(2)


def start():
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())


def stop():
    global _task
    if _task:
        _task.cancel()
        _task = None
