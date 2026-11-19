"""Background health checker: polls each service (HTTP / TCP / ping) and caches the latest result."""
import asyncio
import logging
import platform
import threading
import time
import zlib
from contextlib import AsyncExitStack
from datetime import datetime, timezone

import httpx
from sqlalchemy import Float, Integer, case, cast, func
from sqlalchemy.orm import Session

from . import models, notify
from .database import SessionLocal

log = logging.getLogger("startpage.health")

RECORDED = ("online", "degraded", "offline")  # statuses written to health_checks
MAX_HISTORY_HOURS = 24 * 30
MAX_BUCKETS = 200
PRUNE_EVERY = 3600  # seconds
MANUAL_MIN_INTERVAL = 5.0  # seconds between manual checks of one service
RETRY_MIN_INTERVAL = 60.0  # a failed alert for one service is retried at most this often
IN_FLIGHT_TTL = 600.0  # an alert queued this long ago and never answered is considered lost
MAX_FIRST_CHECK_SPREAD = 10.0  # first checks are spread over at most this many seconds

_status: dict[str, dict] = {}  # service_id -> status dict
_next_check: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}  # per service: a check and its recording never interleave
_last_manual: dict[str, float] = {}

# Alert state. Mutated from the event loop and from worker threads: guard with _state_lock.
_state_lock = threading.RLock()
_failures: dict[str, int] = {}  # service_id -> consecutive failed checks
_down_sent: set[str] = set()  # services whose "down" alert was delivered (recovery still owed)
_pending_since: dict[str, float] = {}  # threshold reached, "down" not delivered yet
_in_flight: dict[str, float] = {}  # service_id -> when its alert was queued
_last_attempt: dict[str, float] = {}  # service_id -> when its alert was last queued
_persisted: dict[str, tuple] = {}  # service_id -> alert state as last written to the database
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


def _to_dt(ts: float) -> datetime:
    """Unix seconds -> naive UTC datetime (how the history tables store time)."""
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _to_ts(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def record_result(db: Session, result: dict) -> tuple[str, str] | None:
    """Store one completed check and, when the state changed, an incident-log event.

    Returns (from_status, to_status) on a transition. The first observation of a service
    is not a transition. The caller commits. Disabled/unknown results are not recorded."""
    status = result.get("status")
    svc_id = result.get("service_id")
    if status not in RECORDED or not svc_id or not db.get(models.Service, svc_id):
        return None
    at = _to_dt(result.get("checked_at") or time.time())
    prev = (
        db.query(models.HealthCheck.status)
        .filter(models.HealthCheck.service_id == svc_id)
        .order_by(models.HealthCheck.checked_at.desc(), models.HealthCheck.id.desc())
        .limit(1)
        .scalar()
    )
    db.add(models.HealthCheck(service_id=svc_id, checked_at=at, status=status, latency_ms=result.get("latency_ms")))
    if prev and prev != status:
        db.add(
            models.HealthEvent(
                service_id=svc_id, at=at, from_status=prev, to_status=status, error=_error_text(result)  # "HTTP 503" for a 5xx
            )
        )
        return prev, status
    return None


def prune(db: Session, check_days: int, event_days: int, now: float | None = None) -> tuple[int, int]:
    """Delete history older than the retention windows; returns (checks, events) removed."""
    now = time.time() if now is None else now
    checks = (
        db.query(models.HealthCheck)
        .filter(models.HealthCheck.checked_at < _to_dt(now - check_days * 86400))
        .delete(synchronize_session=False)
    )
    events = (
        db.query(models.HealthEvent)
        .filter(models.HealthEvent.at < _to_dt(now - event_days * 86400))
        .delete(synchronize_session=False)
    )
    db.commit()
    return checks, events


def history(db: Session, hours: int = 24, buckets: int = 48, now: float | None = None) -> list[dict]:
    """Uptime, latency and fixed-width time buckets (oldest first) for every service with data."""
    hours = max(1, min(int(hours), MAX_HISTORY_HOURS))
    buckets = max(1, min(int(buckets), MAX_BUCKETS))
    now = time.time() if now is None else now
    span = hours * 3600
    start = now - span
    # One grouped query: bucket index computed in SQL from the stored UTC timestamp.
    epoch = cast(func.strftime("%s", models.HealthCheck.checked_at), Float)
    idx = cast((epoch - int(start)) * buckets / span, Integer)
    up = func.sum(case((models.HealthCheck.status.in_(("online", "degraded")), 1), else_=0))
    rows = (
        db.query(
            models.HealthCheck.service_id,
            idx.label("idx"),
            func.count().label("total"),
            up.label("up"),
            func.sum(models.HealthCheck.latency_ms).label("lat_sum"),
            func.count(models.HealthCheck.latency_ms).label("lat_n"),
        )
        .filter(models.HealthCheck.checked_at >= _to_dt(start))
        .group_by(models.HealthCheck.service_id, "idx")
        .all()
    )
    width = span / buckets
    per: dict[str, dict] = {}
    for svc_id, i, total, n_up, lat_sum, lat_n in rows:
        entry = per.setdefault(
            svc_id,
            {
                "service_id": svc_id,
                "up": 0,
                "checks": 0,
                "lat_sum": 0.0,
                "lat_n": 0,
                "buckets": [
                    {"t": int(start + b * width), "up": 0, "total": 0, "latency_ms": None} for b in range(buckets)
                ],
            },
        )
        bucket = entry["buckets"][max(0, min(int(i), buckets - 1))]
        bucket["up"] += n_up or 0
        bucket["total"] += total
        if lat_n:
            # Average across rows when two index values clamp into one bucket (the final edge).
            prev_n = bucket.get("_n", 0)
            prev_sum = (bucket["latency_ms"] or 0) * prev_n
            bucket["_n"] = prev_n + lat_n
            bucket["latency_ms"] = (prev_sum + lat_sum) / bucket["_n"]
        entry["up"] += n_up or 0
        entry["checks"] += total
        entry["lat_sum"] += lat_sum or 0
        entry["lat_n"] += lat_n
    out = []
    for svc_id in sorted(per):
        e = per[svc_id]
        for b in e["buckets"]:
            b.pop("_n", None)
            if b["latency_ms"] is not None:
                b["latency_ms"] = round(b["latency_ms"], 1)
        out.append(
            {
                "service_id": svc_id,
                # Floor (integer maths), so only a perfect record reads 100.
                "uptime_pct": (e["up"] * 10000 // e["checks"]) / 100,
                "avg_latency_ms": round(e["lat_sum"] / e["lat_n"], 1) if e["lat_n"] else None,
                "checks": e["checks"],
                "buckets": e["buckets"],
            }
        )
    return out


def _settings() -> dict:
    from .routers.settings import load  # local import to avoid cycle

    with SessionLocal() as db:
        return load(db)


# ---------- alert state ----------
def _snapshot(svc_id: str) -> tuple:
    return (
        _failures.get(svc_id, 0),
        svc_id in _down_sent,
        int(_pending_since[svc_id]) if svc_id in _pending_since else None,
    )


def _persist(db: Session, ids) -> None:
    """Write the alert state of `ids` (only the ones that changed). The caller commits."""
    for svc_id in ids:
        with _state_lock:
            snap = _snapshot(svc_id)
        if _persisted.get(svc_id, (0, False, None)) == snap:
            continue
        if not db.get(models.Service, svc_id):
            _persisted.pop(svc_id, None)
            continue
        failures, notified, pending = snap
        row = db.get(models.HealthAlertState, svc_id)
        if failures == 0 and not notified and pending is None:
            if row:
                db.delete(row)
        else:
            if not row:
                row = models.HealthAlertState(service_id=svc_id)
                db.add(row)
            row.failures = failures
            row.down_notified = notified
            row.pending_since = _to_dt(pending) if pending is not None else None
        _persisted[svc_id] = snap


def load_state() -> None:
    """Seed the alert state from the database (startup), so a restart keeps it."""
    with SessionLocal() as db:
        rows = db.query(models.HealthAlertState).all()
        with _state_lock:
            for r in rows:
                if r.failures:
                    _failures[r.service_id] = r.failures
                if r.down_notified:
                    _down_sent.add(r.service_id)
                if r.pending_since:
                    _pending_since[r.service_id] = _to_ts(r.pending_since)
                _persisted[r.service_id] = _snapshot(r.service_id)


def _forget(svc_id: str) -> bool:
    """Drop all alert state of a service; True when there was any."""
    with _state_lock:
        had = bool(_failures.pop(svc_id, 0)) or svc_id in _down_sent or svc_id in _pending_since
        _down_sent.discard(svc_id)
        _pending_since.pop(svc_id, None)
        _in_flight.pop(svc_id, None)
        _last_attempt.pop(svc_id, None)
    return had


def _is_failure(status: str | None, http_status: int | None) -> bool:
    """Offline, or an HTTP 5xx answer (degraded), counts towards the alert threshold."""
    return status == "offline" or (status == "degraded" and (http_status or 0) >= 500)


def _track(svc_id: str, status: str, cfg: dict, http_status: int | None = None, now: float | None = None) -> str | None:
    """Update the failure counter; return 'down' / 'recovered' when an alert is due.

    Nothing is marked as notified here: that only happens in `_confirm` after a successful
    send, so a failed send stays due and is retried (at most once per RETRY_MIN_INTERVAL,
    never while one is still queued). Returning an event marks it in flight."""
    now = time.time() if now is None else now
    with _state_lock:
        if _is_failure(status, http_status):
            _failures[svc_id] = _failures.get(svc_id, 0) + 1
            if (
                notify.configured(cfg)
                and svc_id not in _down_sent
                and _failures[svc_id] >= cfg.get("notify_after_failures", 2)
            ):
                _pending_since.setdefault(svc_id, now)
                if _can_queue(svc_id, now):
                    return _queued("down", svc_id, now)
            return None
        _failures.pop(svc_id, None)
        if status != "online":
            return None  # degraded below 5xx: no alert, and not a recovery either
        _pending_since.pop(svc_id, None)  # recovered before the user was ever told
        if svc_id not in _down_sent:
            return None
        if not (notify.configured(cfg) and cfg.get("notify_on_recover", True)):
            _down_sent.discard(svc_id)
            return None
        if _can_queue(svc_id, now):
            return _queued("recovered", svc_id, now)
        return None


def _can_queue(svc_id: str, now: float) -> bool:
    queued = _in_flight.get(svc_id)
    if queued is not None and now - queued < IN_FLIGHT_TTL:
        return False
    return now - _last_attempt.get(svc_id, 0.0) >= RETRY_MIN_INTERVAL


def _queued(event: str, svc_id: str, now: float) -> str:
    _in_flight[svc_id] = now
    _last_attempt[svc_id] = now
    return event


def _confirm(event: str, svc_id: str, ok: bool) -> None:
    """Outcome of a queued alert: success marks it delivered, failure leaves it pending."""
    with _state_lock:
        _in_flight.pop(svc_id, None)
        if not ok:
            return
        _last_attempt.pop(svc_id, None)
        if event == "down":
            _down_sent.add(svc_id)
            _pending_since.pop(svc_id, None)
        else:
            _down_sent.discard(svc_id)


def _error_text(result: dict) -> str | None:
    if result.get("error"):
        return result["error"]
    if result.get("http_status") and result["http_status"] >= 500:
        return f"HTTP {result['http_status']}"
    return None


def process_results(results: list[dict], track: bool = True) -> tuple[dict | None, list[dict]]:
    """Record a tick's results (one session, one commit) and decide which alerts are due.

    Runs in a worker thread, so it creates its own session. Services deleted meanwhile are
    skipped (nothing recorded, nothing notified). `track=False` (manual checks) records
    only: such a check neither counts towards the threshold nor notifies. Returns
    (cfg, events); a database failure is logged and never propagates."""
    cfg = None
    if track:
        try:
            cfg = _settings()
        except Exception:  # noqa: BLE001
            log.exception("health: could not load settings; alert state left unchanged")
    events: list[dict] = []
    try:
        with SessionLocal() as db:
            try:
                live: list[tuple[dict, dict]] = []
                for result in results:
                    svc = db.get(models.Service, result.get("service_id"))
                    if svc is None:
                        continue
                    record_result(db, result)
                    live.append((result, {"id": svc.id, "name": svc.name, "url": svc.url}))
                if cfg is not None:
                    for result, service in live:
                        ev = _track(service["id"], result.get("status"), cfg, result.get("http_status"))
                        if ev:
                            previous = "offline" if ev == "recovered" else "online"
                            events.append(
                                notify.make_event(ev, service, result.get("status"), previous, _error_text(result))
                            )
                    _persist(db, [s["id"] for _, s in live])
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("health: could not record results")
    except Exception:  # noqa: BLE001
        log.exception("health: database error")
    return cfg, events


def _dispatch(cfg: dict | None, events: list[dict]) -> None:
    """Hand all events of one tick to the notification worker as ONE message."""
    if not events or cfg is None:
        return

    async def done(ok: bool) -> None:
        for e in events:
            _confirm(e["event"], e["service"]["id"], ok)
        await asyncio.to_thread(_persist_ids, [e["service"]["id"] for e in events])

    try:
        notify.submit(cfg, events, done)
    except Exception:  # noqa: BLE001
        log.exception("health: could not queue notification")
        for e in events:
            _confirm(e["event"], e["service"]["id"], False)


def _persist_ids(ids: list[str]) -> None:
    try:
        with SessionLocal() as db:
            _persist(db, ids)
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("health: could not save alert state")


# ---------- scheduling ----------
def _lock_for(svc_id: str) -> asyncio.Lock:
    lock = _locks.get(svc_id)
    if lock is None:
        lock = _locks[svc_id] = asyncio.Lock()
    return lock


def _first_offset(svc_id: str, interval: int) -> float:
    """Deterministic 0..min(interval, MAX_FIRST_CHECK_SPREAD) s offset, so services with the
    same interval are not all checked in the same tick forever."""
    span = min(float(interval), MAX_FIRST_CHECK_SPREAD)
    return (zlib.crc32(svc_id.encode()) % 1000) / 1000 * span


def _scan(now: float) -> tuple[list[dict], set[str]]:
    """(specs due now, ids of all services). Disabled services lose their alert state."""
    due: list[dict] = []
    live_ids: set[str] = set()
    cleared: list[str] = []
    with SessionLocal() as db:
        for s in db.query(models.Service).all():
            live_ids.add(s.id)
            if not s.health_enabled:
                _status[s.id] = {"service_id": s.id, "status": "disabled"}
                if _forget(s.id):
                    cleared.append(s.id)
                continue
            spec = _spec(s)
            if s.id not in _next_check:
                _next_check[s.id] = now + _first_offset(s.id, spec["interval"])
            if _next_check[s.id] <= now:
                due.append(spec)
        if cleared:
            _persist(db, cleared)
            db.commit()
    return due, live_ids


def _load_spec(service_id: str) -> tuple[bool, dict | None]:
    """(exists, spec); the spec is None for a service with health checks disabled."""
    with SessionLocal() as db:
        svc = db.get(models.Service, service_id)
        if not svc:
            return False, None
        if not svc.health_enabled:
            cleared = _forget(svc.id)
            if cleared:
                _persist(db, [svc.id])
                db.commit()
            return True, None
        return True, _spec(svc)


async def check_now(service_id: str) -> dict | None:
    """Manual check: recorded in the history, but it neither counts towards the alert
    threshold nor notifies. Rate-limited per service; inside the window the cached status
    is returned."""
    exists, spec = await asyncio.to_thread(_load_spec, service_id)
    if not exists:
        return None
    if spec is None:
        _status[service_id] = {"service_id": service_id, "status": "disabled"}
        return _status[service_id]
    async with _lock_for(service_id):
        cached = _status.get(service_id)
        if cached and cached.get("status") in RECORDED and time.time() - _last_manual.get(service_id, 0) < MANUAL_MIN_INTERVAL:
            return cached
        async with httpx.AsyncClient(verify=False) as client:
            result = await check_service(client, spec)
        _last_manual[service_id] = time.time()
        _status[service_id] = result
        _next_check[service_id] = time.time() + spec["interval"]
        await asyncio.to_thread(process_results, [result], False)
    return result


async def _tick(client: httpx.AsyncClient) -> None:
    now = time.time()
    due, live_ids = await asyncio.to_thread(_scan, now)
    for stale in (set(_status) | set(_next_check) | set(_locks)) - live_ids:
        _status.pop(stale, None)
        _next_check.pop(stale, None)
        _locks.pop(stale, None)
        _last_manual.pop(stale, None)
        _forget(stale)
        _persisted.pop(stale, None)
    if not due:
        return
    async with AsyncExitStack() as stack:
        # Held until the results are recorded, so a manual check of the same service
        # cannot interleave. Sorted: always the same acquisition order.
        for spec in sorted(due, key=lambda s: s["id"]):
            await stack.enter_async_context(_lock_for(spec["id"]))
        results = await asyncio.gather(*(check_service(client, spec) for spec in due))
        for spec, result in zip(due, results):
            _status[spec["id"]] = result
            _next_check[spec["id"]] = time.time() + spec["interval"]
        cfg, events = await asyncio.to_thread(process_results, list(results))
    _dispatch(cfg, events)


def _prune_job() -> None:
    cfg = _settings()
    with SessionLocal() as db:
        prune(db, cfg["health_history_days"], cfg["health_events_days"])


async def _loop():
    last_prune = 0.0
    async with httpx.AsyncClient(verify=False) as client:
        while True:
            try:
                await _tick(client)
                if time.time() - last_prune >= PRUNE_EVERY:
                    last_prune = time.time()
                    await asyncio.to_thread(_prune_job)
            except Exception:  # noqa: BLE001
                log.exception("health loop error")
            await asyncio.sleep(2)


def start():
    global _task
    if _task is None:
        try:
            load_state()
        except Exception:  # noqa: BLE001
            log.exception("health: could not load alert state")
        notify.start()
        _task = asyncio.create_task(_loop())


def stop():
    """Cancel the loop. The caller then awaits `notify.stop()` to close the shared client."""
    global _task
    if _task:
        _task.cancel()
        _task = None
