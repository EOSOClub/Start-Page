"""Background health checker: polls each service (HTTP / TCP / ping) and caches the latest result."""
import asyncio
import logging
import platform
import time

import httpx

from . import models
from .database import SessionLocal

log = logging.getLogger("startpage.health")

_status: dict[str, dict] = {}  # service_id -> status dict
_next_check: dict[str, float] = {}
_task: asyncio.Task | None = None

_IS_WINDOWS = platform.system() == "Windows"


def get_all() -> dict[str, dict]:
    return _status


def _result(svc_id: str, status: str, **extra) -> dict:
    return {
        "service_id": svc_id,
        "status": status,
        "http_status": None,
        "latency_ms": None,
        "checked_at": time.time(),
        "error": None,
        **extra,
    }


async def _check_http(client: httpx.AsyncClient, svc_id: str, url: str) -> dict:
    started = time.perf_counter()
    try:
        resp = await client.get(url, follow_redirects=True, timeout=5.0)
        latency = (time.perf_counter() - started) * 1000
        status = "online" if resp.status_code < 500 else "degraded"
        return _result(svc_id, status, http_status=resp.status_code, latency_ms=round(latency, 1))
    except Exception as exc:  # noqa: BLE001
        return _result(svc_id, "offline", error=type(exc).__name__)


async def _check_tcp(svc_id: str, host: str, port: int | None) -> dict:
    if not port:
        return _result(svc_id, "offline", error="No port configured")
    started = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
        latency = (time.perf_counter() - started) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return _result(svc_id, "online", latency_ms=round(latency, 1))
    except Exception as exc:  # noqa: BLE001
        return _result(svc_id, "offline", error=type(exc).__name__)


async def _check_ping(svc_id: str, host: str) -> dict:
    args = ["ping", "-n", "1", "-w", "3000", host] if _IS_WINDOWS else ["ping", "-c", "1", "-W", "3", host]
    started = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        rc = await asyncio.wait_for(proc.wait(), timeout=6.0)
        latency = (time.perf_counter() - started) * 1000
        if rc == 0:
            return _result(svc_id, "online", latency_ms=round(latency, 1))
        return _result(svc_id, "offline", error="No reply")
    except Exception as exc:  # noqa: BLE001
        return _result(svc_id, "offline", error=type(exc).__name__)


async def check_service(client: httpx.AsyncClient, spec: dict) -> dict:
    kind = spec.get("type") or "http"
    if kind == "tcp":
        return await _check_tcp(spec["id"], spec["host"], spec["port"])
    if kind == "ping":
        return await _check_ping(spec["id"], spec["host"])
    return await _check_http(client, spec["id"], spec["url"])


def _spec(svc: models.Service) -> dict:
    return {
        "id": svc.id,
        "type": svc.health_type or "http",
        "url": svc.health_url or svc.url,
        "host": svc.host,
        "port": svc.port,
        "interval": svc.health_interval or 30,
    }


async def check_now(service_id: str) -> dict | None:
    with SessionLocal() as db:
        svc = db.get(models.Service, service_id)
        if not svc:
            return None
        if not svc.health_enabled:
            _status[svc.id] = {"service_id": svc.id, "status": "disabled"}
            return _status[svc.id]
        spec = _spec(svc)
    async with httpx.AsyncClient(verify=False) as client:
        result = await check_service(client, spec)
    _status[service_id] = result
    _next_check[service_id] = time.time() + spec["interval"]
    return result


async def _loop():
    async with httpx.AsyncClient(verify=False) as client:
        while True:
            try:
                now = time.time()
                due: list[dict] = []
                live_ids: set[str] = set()
                with SessionLocal() as db:
                    for s in db.query(models.Service).all():
                        live_ids.add(s.id)
                        if not s.health_enabled:
                            _status[s.id] = {"service_id": s.id, "status": "disabled"}
                            continue
                        if _next_check.get(s.id, 0) <= now:
                            due.append(_spec(s))
                for stale in set(_status) - live_ids:
                    _status.pop(stale, None)
                    _next_check.pop(stale, None)
                if due:
                    results = await asyncio.gather(*(check_service(client, spec) for spec in due))
                    for spec, result in zip(due, results):
                        _status[spec["id"]] = result
                        _next_check[spec["id"]] = time.time() + spec["interval"]
            except Exception:  # noqa: BLE001
                log.exception("health loop error")
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
