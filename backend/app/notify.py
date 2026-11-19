"""Outgoing notifications (ntfy / Discord / generic webhook) for service state changes.

Health alerts go through one worker (a bounded queue plus a single task, started and stopped
with the health loop) that sends one message at a time over one shared httpx client, retrying
transient failures with backoff and honouring `Retry-After`. The caller learns the outcome
through an async `on_done(ok)` callback, so it only marks an alert as delivered once it was.
"""
import asyncio
import logging
import re
from collections import deque
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("startpage.notify")

TIMEOUT = 8.0
ATTEMPTS = 3
BACKOFF = 1.0  # seconds; doubled after every failed attempt
MAX_RETRY_AFTER = 30.0  # never wait longer than this for a Retry-After
QUEUE_MAX = 50
DISCORD_LIMIT = 2000
_ICON = {"down": "🔴", "recovered": "🟢", "test": "🔔", "mixed": "🔔"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class NotifyError(Exception):
    """A send failed; the message is safe to show (never contains the URL)."""

    def __init__(self, message: str, retryable: bool = True, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


def configured(cfg: dict) -> bool:
    return cfg.get("notify_type", "none") != "none" and bool(cfg.get("notify_url"))


def make_event(event: str, service: dict, status: str | None, previous: str | None, error: str | None) -> dict:
    return {"event": event, "service": service, "status": status, "previous": previous, "error": error}


def _text(event: str, service: dict, error: str | None) -> tuple[str, str]:
    """(title, body) for an event: 'down', 'recovered' or 'test'."""
    if event == "test":
        return "Start Page test", "Notifications are working."
    name = service["name"]
    if event == "down":
        return f"{name} is down", f"{name} is not responding" + (f" ({error})" if error else "") + "."
    return f"{name} recovered", f"{name} is back online."


def _batch_text(events: list[dict]) -> tuple[str, str, str]:
    """(kind, title, body) for several events of one tick, e.g. '3 services down: A, B, C'."""
    down = [e for e in events if e["event"] == "down"]
    rec = [e for e in events if e["event"] != "down"]

    def names(items):
        return ", ".join(e["service"]["name"] for e in items)

    def count(items, word):
        return f"{len(items)} service{'s' if len(items) != 1 else ''} {word}"

    lines = []
    for e in events:
        if e["event"] == "down":
            lines.append(f"- {e['service']['name']} is down" + (f" ({e['error']})" if e.get("error") else ""))
        else:
            lines.append(f"- {e['service']['name']} recovered")
    if down and not rec:
        title, kind = count(down, "down"), "down"
        summary = f"{title}: {names(down)}"
    elif rec and not down:
        title, kind = count(rec, "recovered"), "recovered"
        summary = f"{title}: {names(rec)}"
    else:
        title, kind = f"{len(events)} service status changes", "mixed"
        summary = f"{count(down, 'down')}: {names(down)}; {count(rec, 'recovered')}: {names(rec)}"
    return kind, title, summary + "\n" + "\n".join(lines)


def _webhook_item(e: dict) -> dict:
    s = e["service"]
    return {
        "event": e["event"],
        "service": {"id": s.get("id"), "name": s.get("name"), "url": s.get("url")},
        "status": e.get("status"),
        "previous": e.get("previous"),
        "error": e.get("error"),
        "at": datetime.now(timezone.utc).isoformat(),
    }


def _ntfy_title(title: str) -> str:
    """Header values must be single-line ASCII; the full title is also in the body."""
    flat = " ".join(_CONTROL.sub(" ", title).split())
    return flat.encode("ascii", "replace").decode()


def build_batch(cfg: dict, events: list[dict]) -> dict:
    """kwargs for `_post` (headers plus either `content` or `json`) for one or more events.

    One event keeps the single-event shape; several become one message, and the generic
    webhook gets `{"event": "batch", "events": [...], "at": ...}`."""
    kind = cfg["notify_type"]
    if len(events) == 1:
        e = events[0]
        ev, title, body = e["event"], *_text(e["event"], e["service"], e.get("error"))
    else:
        ev, title, body = _batch_text(events)
    if kind == "ntfy":
        return {
            "headers": {
                "Title": _ntfy_title(title),
                "Priority": "high" if ev in ("down", "mixed") else "default",
                "Tags": {"down": "rotating_light", "recovered": "white_check_mark"}.get(ev, "bell"),
            },
            "content": body,
        }
    if kind == "discord":
        content = f"{_ICON[ev]} **{title}**\n{body}"
        if len(content) > DISCORD_LIMIT:
            content = content[: DISCORD_LIMIT - 1] + "…"
        return {"json": {"content": content, "allowed_mentions": {"parse": []}}}
    if len(events) == 1:
        return {"json": _webhook_item(events[0])}
    return {
        "json": {
            "event": "batch",
            "events": [_webhook_item(e) for e in events],
            "at": datetime.now(timezone.utc).isoformat(),
        }
    }


def build_request(cfg: dict, event: str, service: dict, status: str, previous: str | None, error: str | None) -> dict:
    return build_batch(cfg, [make_event(event, service, status, previous, error)])


# ---------- transport ----------
_client: httpx.AsyncClient | None = None  # shared by the worker; None outside a running app


async def _post(url: str, **kwargs) -> None:
    try:
        if _client is not None:
            resp = await _client.post(url, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(url, **kwargs)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        retry_after = None
        if code == 429:
            try:
                retry_after = float(exc.response.headers.get("Retry-After", ""))
            except ValueError:
                retry_after = None
        raise NotifyError(
            f"HTTP {code}", retryable=code >= 500 or code in (408, 429), retry_after=retry_after
        ) from None
    except Exception as exc:  # noqa: BLE001  (httpx errors can embed the URL; keep only the type)
        raise NotifyError(type(exc).__name__) from None


async def deliver(cfg: dict, event: str, service: dict, status: str, previous: str | None, error: str | None) -> None:
    """Send one notification once; raises NotifyError on failure."""
    await _post(cfg["notify_url"], **build_request(cfg, event, service, status, previous, error))


async def send_test(cfg: dict) -> None:
    service = {"id": "test", "name": "Test service", "url": "http://localhost"}
    await deliver(cfg, "test", service, "online", "offline", None)


async def deliver_batch(cfg: dict, events: list[dict]) -> bool:
    """Send with up to ATTEMPTS tries: exponential backoff, Retry-After honoured (capped)."""
    last: NotifyError | None = None
    for attempt in range(ATTEMPTS):
        try:
            await _post(cfg["notify_url"], **build_batch(cfg, events))
            return True
        except NotifyError as exc:
            last = exc
            if not exc.retryable or attempt == ATTEMPTS - 1:
                break
            wait = BACKOFF * (2**attempt)
            if exc.retry_after is not None:
                wait = max(wait, exc.retry_after)
            await asyncio.sleep(min(wait, MAX_RETRY_AFTER))
    log.warning("notification for %d event(s) failed: %s", len(events), last)
    return False


# ---------- queue + worker ----------
DoneCallback = Callable[[bool], Awaitable[None]]
_queue: deque | None = None
_wake: asyncio.Event | None = None
_worker: asyncio.Task | None = None
_overflow_logged = False
_bg: set[asyncio.Task] = set()


def start() -> None:
    global _client, _queue, _wake, _worker
    if _worker is not None:
        return
    _client = httpx.AsyncClient(timeout=TIMEOUT)
    _queue = deque()
    _wake = asyncio.Event()
    _worker = asyncio.create_task(_run())


async def stop() -> None:
    global _client, _queue, _wake, _worker
    if _worker:
        _worker.cancel()
        try:
            await _worker
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _worker = None
    client, _client = _client, None
    _queue = _wake = None
    if client:
        await client.aclose()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg.add(task)
    task.add_done_callback(_bg.discard)


def submit(cfg: dict, events: list[dict], on_done: DoneCallback | None = None) -> None:
    """Queue one message for `events`. The oldest queued message is dropped on overflow
    (its `on_done(False)` still runs, so the caller keeps those alerts pending)."""
    global _overflow_logged
    if _queue is None or _wake is None:
        raise RuntimeError("notification worker is not running")
    if len(_queue) >= QUEUE_MAX:
        _c, _e, dropped_done = _queue.popleft()
        if not _overflow_logged:
            _overflow_logged = True
            log.warning("notification queue full (%d): dropping the oldest message", QUEUE_MAX)
        if dropped_done:
            _spawn(dropped_done(False))
    _queue.append((cfg, events, on_done))
    _wake.set()


async def _run() -> None:
    global _overflow_logged
    while True:
        while not _queue:
            _overflow_logged = False
            _wake.clear()
            await _wake.wait()
        cfg, events, on_done = _queue.popleft()
        ok = False
        try:
            ok = await deliver_batch(cfg, events)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("notification worker error")
        if on_done:
            try:
                await on_done(ok)
            except Exception:  # noqa: BLE001
                log.exception("notification callback error")
