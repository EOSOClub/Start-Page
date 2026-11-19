"""Polling runtime: contexts (client + state) per integration, the scheduler, fetch and actions.

- Each due integration is polled in its own task with an overall timeout, so a slow one never
  delays another, and an integration is never fetched by two tasks at once (`Context.lock`).
- Each integration has one Context (one shared httpx client plus a state dict). It is rebuilt
  when the integration's type or config changes, and closed when it is deleted or on shutdown.
- Errors are reduced to a safe message (see errors.py) before they reach `_data`.
"""
import asyncio
import json
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any, Optional

from .. import models
from ..database import SessionLocal
from ..schemas import MIN_INTERVAL
from .base import Context, Spec, check_shape, check_values, trim_sample
import httpx

from .errors import AuthFailed, IntegrationError, log_text, safe_error
from .registry import SPECS

log = logging.getLogger("startpage.integrations")

FETCH_TIMEOUT = 20.0  # seconds a single fetch may take, in total
ACTION_TIMEOUT = 30.0
ACTION_WAIT = 10.0  # how long an action waits for a running poll of the same integration
MAX_POLL_FACTOR = 10  # a static key refused with 401/403 stretches the poll interval up to this many times
TICK = 2.0  # how often the scheduler looks for due integrations

_data: dict[str, dict] = {}
_next: dict[str, float] = {}
_task: Optional[asyncio.Task] = None
_tasks: set[asyncio.Task] = set()  # in-flight polls
_ctx: dict[str, tuple[str, Context]] = {}  # integration id -> (config fingerprint, context)
_closing: list[Context] = []  # replaced / deleted contexts whose client still needs closing
_last_logged: dict[str, str] = {}
_lock = threading.Lock()  # guards _closing and _ctx pops: invalidate() runs in threadpool threads


class UnknownAction(LookupError):
    pass


class InvalidPayload(ValueError):
    def __init__(self, errors: list[tuple[str, str]]):
        super().__init__("; ".join(f"{k}: {m}" for k, m in errors))
        self.errors = errors


class ActionFailed(IntegrationError):
    """The upstream refused or failed an action; the message is safe to show."""


def get_all() -> dict[str, dict]:
    return _data


def get(integration_id: str) -> Optional[dict]:
    return _data.get(integration_id)


def snapshot(integ) -> SimpleNamespace:
    """A plain copy of an Integration row, safe to hand to another task or thread."""
    return SimpleNamespace(id=integ.id, name=integ.name, type=integ.type, interval=integ.interval,
                           enabled=integ.enabled, config=dict(integ.config or {}))


def interval_of(integ) -> int:
    """Seconds between polls: the row's interval, the type's default when it is unset or not
    positive (old rows), never below MIN_INTERVAL."""
    spec = SPECS.get(integ.type)
    v = integ.interval if isinstance(integ.interval, int) and integ.interval > 0 else (spec.default_interval if spec else 30)
    return max(MIN_INTERVAL, v)


# ---------------------------------------------------------------- contexts
def _fingerprint(integ) -> str:
    return json.dumps([integ.type, integ.config or {}], sort_keys=True, default=str)


def _context(integ, spec: Spec) -> Context:
    fp = _fingerprint(integ)
    cur = _ctx.get(integ.id)
    if cur and cur[0] == fp:
        return cur[1]
    if cur:  # edited: its client and state belong to the old config
        _retire(cur[1])
    ctx = Context(spec, integ.config or {}, integ.id)
    _ctx[integ.id] = (fp, ctx)
    return ctx


def _retire(ctx: Context) -> None:
    """Mark a replaced or deleted context (queued fetches refuse it) and queue its client for closing."""
    ctx.retired = True
    with _lock:
        _closing.append(ctx)


def _current(integration_id: str, ctx: Optional[Context]) -> bool:
    """True when `ctx` is still the integration's live context (it was not edited, disabled or
    deleted while a fetch ran on it). A result of a stale context must not be stored."""
    if ctx is None:
        return True
    cur = _ctx.get(integration_id)
    return cur is not None and cur[1] is ctx


def invalidate(integration_id: str, drop_data: bool = False) -> None:
    """Forget an integration's client and state (edited, imported over, deleted, disabled). Sync, so
    any thread may call it; the client is closed (after the type's close hook, e.g. a logout) by
    close_pending() soon after. It is polled again on the next scheduler tick."""
    cur = _ctx.pop(integration_id, None)
    if cur:
        _retire(cur[1])
    _next.pop(integration_id, None)
    _last_logged.pop(integration_id, None)
    if drop_data:
        _data.pop(integration_id, None)


async def close_pending() -> None:
    """Close the clients of replaced contexts (not while one of their fetches is still running)."""
    global _closing
    with _lock:
        busy = [c for c in _closing if c.lock.locked()]
        done = [c for c in _closing if not c.lock.locked()]
        _closing = busy
    for c in done:
        try:
            await c.aclose()
        except Exception:  # noqa: BLE001
            log.debug("closing an integration client failed", exc_info=True)


async def close_all() -> None:
    """Shutdown: close every client."""
    with _lock:
        _closing.extend(c for _, c in _ctx.values())
        _ctx.clear()
        pending = list(_closing)
        _closing.clear()
    for c in pending:
        c.retired = True
        try:
            await c.aclose()
        except Exception:  # noqa: BLE001
            log.debug("closing an integration client failed", exc_info=True)


# ---------------------------------------------------------------- fetch
def _log_failure(integ, exc: BaseException, spec: Optional[Spec] = None) -> None:
    spec = spec or SPECS.get(getattr(integ, "type", ""))
    # No traceback and no headers: see errors.log_text (class name only for errors that can embed
    # credentials; URLs without query and userinfo, and without the path for `secret_url` types).
    text = log_text(exc, host_only=bool(spec and spec.secret_url))
    if _last_logged.get(integ.id) == text:  # a dead integration must not log every poll
        return
    _last_logged[integ.id] = text
    log.warning("integration %r (%s) failed: %s", getattr(integ, "name", integ.id), integ.type, text)


def _envelope(integ, ok: bool, data: Any = None, error: Optional[str] = None) -> dict:
    return {"integration_id": integ.id, "ok": ok, "data": data, "fetched_at": time.time(), "error": error}


def _is_status_auth(exc: BaseException) -> bool:
    return isinstance(exc, AuthFailed) or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403))


async def _fetch(integration, manual: bool = False) -> tuple[dict, Optional[Context]]:
    """fetch() plus the context it ran on, so callers can tell whether it is still current."""
    spec = SPECS.get(integration.type)
    if spec is None:
        return _envelope(integration, False, error=f"Unknown type {integration.type}"), None
    ctx = _context(integration, spec)
    async with ctx.lock:  # never two fetches of one integration at once
        if ctx.retired:  # replaced or deleted while this fetch waited for the lock
            return _envelope(integration, False, error="changed while waiting"), ctx
        ctx.manual = manual
        try:
            data = await asyncio.wait_for(spec.fetch(ctx), FETCH_TIMEOUT)
            check_shape(data, spec.provides)
            _last_logged.pop(integration.id, None)
            ctx.status_auth_failures = 0
            ctx.auth_ok()  # a whole fetch worked: the credentials are good, forget the login backoff
            return _envelope(integration, True, data), ctx
        except Exception as exc:  # noqa: BLE001
            _log_failure(integration, exc, spec)
            if _is_status_auth(exc):
                ctx.status_auth_failures += 1
            return _envelope(integration, False, (_data.get(integration.id) or {}).get("data"), safe_error(exc, ctx.cfg)), ctx
        finally:
            ctx.manual = False


async def fetch(integration) -> dict:
    """One fetch -> the envelope {integration_id, ok, data, fetched_at, error}. On failure `data`
    keeps the last good result and `error` is a short safe message."""
    return (await _fetch(integration))[0]


async def refresh_row(integ) -> dict:
    """Fetch now for a row already in hand (router paths use this: no second DB session). A person
    asked for it, so the login backoff does not apply. The result is stored only when the context
    it ran on is still the current one (a slow fetch of an old config must not overwrite a newer result)."""
    snap = snapshot(integ)
    result, ctx = await _fetch(snap, manual=True)
    if _current(snap.id, ctx):
        _data[snap.id] = result
        _next[snap.id] = time.time() + interval_of(snap)
    return result


async def refresh(integration_id: str) -> Optional[dict]:
    with SessionLocal() as db:
        integ = db.get(models.Integration, integration_id)
        if not integ:
            return None
        snap = snapshot(integ)
    return await refresh_row(snap)


async def test_config(spec: Spec, cfg: dict) -> dict:
    """Run one fetch for an unsaved config: {ok, error, sample}. Nothing is stored."""
    ctx = Context(spec, cfg)
    ctx.manual = True
    try:
        data = await asyncio.wait_for(spec.fetch(ctx), FETCH_TIMEOUT)
        check_shape(data, spec.provides)
        return {"ok": True, "error": None, "sample": trim_sample(data)}
    except Exception as exc:  # noqa: BLE001
        _log_failure(SimpleNamespace(id="(test)", name="(test)", type=spec.type), exc, spec)
        return {"ok": False, "error": safe_error(exc, ctx.cfg), "sample": None}
    finally:
        await ctx.aclose()  # runs the type's close hook: a Pi-hole session from the test is logged out


# ---------------------------------------------------------------- actions
async def run_action(integ, name: str, payload: Optional[dict]) -> None:
    """Validate `payload` against the action's declared fields and run it on the integration's
    shared context. Raises UnknownAction, InvalidPayload, or ActionFailed (safe message)."""
    spec = SPECS.get(integ.type)
    action = spec.actions.get(name) if spec else None
    if action is None:
        raise UnknownAction(name)
    clean, errors = check_values(action.payload, payload or {})
    if errors:
        raise InvalidPayload(errors)
    ctx = _context(integ, spec)
    await _acquire(ctx)
    try:
        await asyncio.wait_for(action.fn(ctx, clean), ACTION_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        _log_failure(integ, exc, spec)
        raise ActionFailed(safe_error(exc, ctx.cfg)) from None
    finally:
        ctx.manual = False
        ctx.lock.release()


async def _acquire(ctx: Context) -> None:
    """Take the integration's lock for an action or one-off call (they share the client and state
    with the poll). Waits at most ACTION_WAIT behind a running poll, then fails with a safe message."""
    try:
        await asyncio.wait_for(ctx.lock.acquire(), ACTION_WAIT)
    except asyncio.TimeoutError:
        raise ActionFailed("busy: the integration is still being polled, try again in a moment") from None
    if ctx.retired:
        ctx.lock.release()
        raise ActionFailed("the integration was changed, try again")
    ctx.manual = True


async def call(integ, fn):
    """Run `await fn(ctx)` on the integration's shared context (its client and config) and
    return the result. For one-off reads that are not part of the polled payload (service
    discovery). Raises ActionFailed with a safe message."""
    spec = SPECS.get(integ.type)
    if spec is None:
        raise ActionFailed(f"Unknown type {integ.type}")
    ctx = _context(integ, spec)
    await _acquire(ctx)
    try:
        return await asyncio.wait_for(fn(ctx), FETCH_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        _log_failure(integ, exc, spec)
        raise ActionFailed(safe_error(exc, ctx.cfg)) from None
    finally:
        ctx.manual = False
        ctx.lock.release()


# ---------------------------------------------------------------- scheduler
async def _poll(integ) -> None:
    ctx: Optional[Context] = None
    try:
        result, ctx = await _fetch(integ)
        if _current(integ.id, ctx):
            _data[integ.id] = result
    except Exception:  # noqa: BLE001
        log.exception("integration poll error")
    finally:
        if _current(integ.id, ctx):
            # a static key refused with 401/403 is retried less and less often (gently, at most x10)
            factor = min(1 + (ctx.status_auth_failures if ctx else 0), MAX_POLL_FACTOR)
            _next[integ.id] = time.time() + interval_of(integ) * factor
        else:
            _next.pop(integ.id, None)  # superseded by an edit: poll again on the next tick


async def _tick() -> None:
    await close_pending()
    now = time.time()
    due = []
    live: set[str] = set()
    with SessionLocal() as db:
        for i in db.query(models.Integration).all():
            live.add(i.id)
            if not i.enabled:
                if i.id in _data or i.id in _ctx:
                    invalidate(i.id, drop_data=True)  # drop data, close the client (and log out)
                continue
            cur = _ctx.get(i.id)
            if _next.get(i.id, 0) <= now and not (cur and cur[1].lock.locked()):
                due.append(snapshot(i))
    for stale in set(_data) | set(_next) | set(_ctx):
        if stale not in live:
            invalidate(stale, drop_data=True)
    for integ in due:
        _next[integ.id] = float("inf")  # until its poll finishes; the next tick must not pick it up again
        task = asyncio.create_task(_poll(integ))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    await close_pending()  # clients of integrations dropped just above


async def _loop() -> None:
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001
            log.exception("integration loop error")
        await asyncio.sleep(TICK)


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())


def stop() -> None:
    """Cancel the scheduler and in-flight polls; `await close_all()` afterwards closes the clients."""
    global _task
    if _task:
        _task.cancel()
        _task = None
    for t in list(_tasks):
        t.cancel()
