"""Health history, incident log, retention and notifications (no real network, no loops)."""

import asyncio
import contextlib
import json
import logging
import time

import httpx
import pytest

from app import health, models, notify
from conftest import HEADERS

NOW = 1_800_000_000.0  # fixed clock for bucket maths


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    for d in (
        health._failures,
        health._status,
        health._pending_since,
        health._in_flight,
        health._last_attempt,
        health._persisted,
        health._next_check,
        health._locks,
        health._last_manual,
    ):
        d.clear()
    health._down_sent.clear()
    monkeypatch.setattr(notify, "BACKOFF", 0.0)  # retries without waiting
    yield


def _svc(session, sid="a", name="Alpha"):
    session.add(models.Service(id=sid, name=name, host="h"))
    session.commit()
    return sid


def _res(sid, status, ts, latency=None, error=None):
    return {"service_id": sid, "status": status, "checked_at": ts, "latency_ms": latency, "error": error}


def _record(session, *results):
    out = [health.record_result(session, r) for r in results]
    session.commit()
    return out


# ---------- recording ----------
def test_record_transitions_and_first_observation(session):
    _svc(session)
    out = _record(
        session,
        _res("a", "online", NOW, 10),
        _res("a", "online", NOW + 1, 12),
        _res("a", "offline", NOW + 2, error="ConnectError"),
        _res("a", "online", NOW + 3, 11),
    )
    assert out == [None, None, ("online", "offline"), ("offline", "online")]
    assert session.query(models.HealthCheck).count() == 4
    events = session.query(models.HealthEvent).order_by(models.HealthEvent.id).all()
    assert [(e.from_status, e.to_status, e.error) for e in events] == [
        ("online", "offline", "ConnectError"),
        ("offline", "online", None),
    ]


def test_record_skips_disabled_unknown_and_missing_service(session):
    _svc(session)
    out = _record(
        session, _res("a", "disabled", NOW), _res("a", "unknown", NOW), _res("ghost", "online", NOW)
    )
    assert out == [None, None, None]
    assert session.query(models.HealthCheck).count() == 0


# ---------- history ----------
def test_history_buckets_gaps_and_uptime(session):
    _svc(session)
    # 4 h window, 4 buckets of 1 h: [NOW-4h, NOW-3h) ... [NOW-1h, NOW]
    start = NOW - 4 * 3600
    _record(
        session,
        _res("a", "online", start + 10, 10),
        _res("a", "degraded", start + 20, 30),
        _res("a", "offline", start + 3600 + 5),
        # bucket 2 left empty
        _res("a", "online", start + 3 * 3600 + 5, 20),
    )
    (h,) = health.history(session, hours=4, buckets=4, now=NOW)
    assert h["service_id"] == "a"
    assert h["checks"] == 4
    assert h["uptime_pct"] == 75.0
    assert h["avg_latency_ms"] == 20.0
    b = h["buckets"]
    assert [x["t"] for x in b] == [int(start + i * 3600) for i in range(4)]
    assert [(x["up"], x["total"]) for x in b] == [(2, 2), (0, 1), (0, 0), (1, 1)]
    assert b[0]["latency_ms"] == 20.0
    assert b[1]["latency_ms"] is None and b[2]["latency_ms"] is None


def test_history_excludes_old_rows_and_services_without_data(session):
    _svc(session, "a")
    _svc(session, "b", "Beta")
    _record(session, _res("a", "online", NOW - 10 * 3600, 5))
    assert health.history(session, hours=4, buckets=4, now=NOW) == []


def test_history_endpoint_clamps(client, session):
    _svc(session)
    _record(session, _res("a", "online", time.time() - 5, 9))
    (h,) = client.get("/api/health/history", params={"hours": 99999, "buckets": 99999}).json()
    assert len(h["buckets"]) == 200
    assert h["buckets"][-1]["total"] == 1
    (h,) = client.get("/api/health/history", params={"hours": 0, "buckets": 0}).json()
    assert len(h["buckets"]) == 1 and h["uptime_pct"] == 100.0
    assert set(h) == {"service_id", "uptime_pct", "avg_latency_ms", "checks", "buckets"}


# ---------- events ----------
def test_events_order_limit_filter(client, session):
    _svc(session, "a", "Alpha")
    _svc(session, "b", "Beta")
    _record(
        session,
        _res("a", "online", NOW),
        _res("a", "offline", NOW + 1, error="X"),
        _res("b", "online", NOW + 2),
        _res("b", "offline", NOW + 3),
        _res("a", "online", NOW + 4),
    )
    ev = client.get("/api/health/events").json()
    assert [(e["service_name"], e["to_status"]) for e in ev] == [
        ("Alpha", "online"),
        ("Beta", "offline"),
        ("Alpha", "offline"),
    ]
    assert ev[0]["at"] == NOW + 4 and ev[2]["error"] == "X"
    assert len(client.get("/api/health/events", params={"limit": 2}).json()) == 2
    only_a = client.get("/api/health/events", params={"service_id": "a"}).json()
    assert {e["service_id"] for e in only_a} == {"a"} and len(only_a) == 2


# ---------- retention / cascade ----------
def test_prune_respects_both_windows(session):
    _svc(session)
    day = 86400
    _record(
        session,
        _res("a", "online", NOW - 10 * day),
        _res("a", "offline", NOW - 8 * day),  # event 8 d old
        _res("a", "online", NOW - 1 * day),  # event 1 d old
    )
    assert health.prune(session, check_days=7, event_days=90, now=NOW) == (2, 0)
    assert session.query(models.HealthCheck).count() == 1
    assert health.prune(session, check_days=7, event_days=5, now=NOW) == (0, 1)
    assert session.query(models.HealthEvent).count() == 1


def test_cascade_on_service_delete_and_empty_replace_import(client, session):
    _svc(session)
    _record(session, _res("a", "online", NOW), _res("a", "offline", NOW + 1))
    assert client.delete("/api/services/a", headers=HEADERS).status_code == 204
    assert session.query(models.HealthCheck).count() == 0
    assert session.query(models.HealthEvent).count() == 0

    _svc(session, "b", "Beta")
    _record(session, _res("b", "online", NOW), _res("b", "offline", NOW + 1))
    bundle = {"version": 2, "services": [], "dashboards": [], "integrations": [], "settings": {}}
    res = client.post("/api/config/import?replace=true", json=bundle, headers=HEADERS)
    assert res.status_code == 200, res.text
    assert session.query(models.HealthCheck).count() == 0  # b is not in the bundle: gone with its history
    assert "health_checks" not in res.json() and "events" not in res.json()


def test_replace_import_keeps_history_of_surviving_services(client, session):
    _svc(session, "a", "Alpha")
    _svc(session, "b", "Beta")
    page = client.post("/api/dashboards", json={"name": "P"}, headers=HEADERS).json()
    w = client.post(
        f"/api/dashboards/{page['id']}/widgets", json={"type": "service", "service_id": "a"}, headers=HEADERS
    ).json()
    _record(
        session,
        _res("a", "online", NOW), _res("a", "offline", NOW + 1),
        _res("b", "online", NOW), _res("b", "offline", NOW + 1),
    )
    session.add(models.HealthAlertState(service_id="a", failures=3, down_notified=True))
    session.commit()

    bundle = client.get("/api/config/export").json()
    bundle["services"] = [s for s in bundle["services"] if s["id"] == "a"]
    res = client.post("/api/config/import?replace=true", json=bundle, headers=HEADERS)
    assert res.status_code == 200, res.text
    session.expire_all()
    assert session.query(models.HealthCheck).filter_by(service_id="a").count() == 2
    assert session.query(models.HealthEvent).filter_by(service_id="a").count() == 1
    assert session.get(models.HealthAlertState, "a").down_notified is True
    assert session.query(models.HealthCheck).filter_by(service_id="b").count() == 0  # absent from the bundle
    assert session.get(models.Service, "b") is None
    widget = session.get(models.Widget, w["id"])
    assert widget is not None and widget.service_id == "a"  # the FK survived (never SET NULL)


# ---------- shared helpers for the alert tests ----------
HOOK_URL = "https://hooks.example/api/webhooks/1234/SECRET-TOKEN"


def _cfg(**over):
    return {
        "notify_type": "webhook",
        "notify_url": HOOK_URL,
        "notify_on_recover": True,
        "notify_after_failures": 1,
        **over,
    }


CFG = _cfg(notify_type="ntfy", notify_url="http://x", notify_after_failures=2)


def _clear_memory():
    for d in (health._failures, health._pending_since, health._in_flight, health._last_attempt, health._persisted):
        d.clear()
    health._down_sent.clear()


@pytest.fixture(name="wired")
def wired_fixture(session, monkeypatch):
    """The health module reads/writes through the test session; returns the live settings dict."""
    cfg = _cfg()
    monkeypatch.setattr(health, "SessionLocal", lambda: contextlib.nullcontext(session))
    monkeypatch.setattr(health, "_settings", lambda: cfg)
    return cfg


class Hook:
    """A webhook receiver for httpx.MockTransport: records requests, answers from a script."""

    def __init__(self, statuses=()):
        self.statuses = list(statuses)  # each an int or (int, headers); then 200
        self.calls = []  # (monotonic time, parsed body, headers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((time.monotonic(), body, dict(request.headers)))
        item = self.statuses.pop(0) if self.statuses else 200
        status, headers = item if isinstance(item, tuple) else (item, {})
        return httpx.Response(status, headers=headers)

    @property
    def bodies(self):
        return [c[1] for c in self.calls]


def run_with_worker(handler, scenario):
    async def main():
        notify.start()
        await notify._client.aclose()
        notify._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await scenario()
        finally:
            await notify.stop()

    asyncio.run(main())


async def tick(cfg, *results, track=True):
    """What one loop tick does after the checks: record, decide, queue ONE message."""
    cfg_out, events = await asyncio.to_thread(health.process_results, list(results), track)
    health._dispatch(cfg_out, events)
    return events


async def settled(timeout=5.0):
    end = time.monotonic() + timeout
    while health._in_flight or notify._queue:
        assert time.monotonic() < end, "notifications did not settle"
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)  # the worker's on_done persists after it clears the flag


# ---------- notification thresholds (no delivery) ----------
def test_down_after_n_failures_exactly_once_then_recovery_once():
    out = [health._track("a", "offline", CFG, now=NOW) for _ in range(4)]
    assert out == [None, "down", None, None]  # a second one is not queued while the first is in flight
    health._confirm("down", "a", True)
    assert [health._track("a", s, CFG, now=NOW) for s in ("offline", "online", "online")] == [None, "recovered", None]
    health._confirm("recovered", "a", True)
    assert "a" not in health._down_sent and health._track("a", "online", CFG, now=NOW) is None


def test_blip_does_not_notify():
    assert [health._track("a", s, CFG, now=NOW) for s in ["offline", "online", "offline", "online"]] == [None] * 4


def test_recovery_needs_prior_confirmed_down_and_setting():
    assert health._track("a", "online", CFG, now=NOW) is None
    cfg = {**CFG, "notify_on_recover": False, "notify_after_failures": 1}
    assert health._track("a", "offline", cfg, now=NOW) == "down"
    health._confirm("down", "a", True)
    assert health._track("a", "online", cfg, now=NOW) is None
    assert "a" not in health._down_sent


def test_recovery_before_down_was_delivered_sends_nothing():
    assert health._track("a", "offline", CFG, now=NOW) is None
    assert health._track("a", "offline", CFG, now=NOW) == "down"
    health._confirm("down", "a", False)  # delivery failed: still pending
    assert "a" in health._pending_since and "a" not in health._down_sent
    assert health._track("a", "online", CFG, now=NOW + 5) is None  # the user was never told: nothing to undo
    assert "a" not in health._pending_since


def test_nothing_when_type_none():
    cfg = {**CFG, "notify_type": "none"}
    assert [health._track("a", "offline", cfg, now=NOW) for _ in range(5)] == [None] * 5
    assert health._track("a", "online", cfg, now=NOW) is None


def test_5xx_counts_as_failure_other_degraded_does_not():
    assert [health._track("a", "degraded", CFG, 503, now=NOW) for _ in range(2)] == [None, "down"]
    health._confirm("down", "a", True)
    # A non-5xx degraded result is neither a failure nor a recovery.
    assert health._track("a", "degraded", CFG, 200, now=NOW + 1) is None
    assert "a" in health._down_sent and health._failures == {}
    # ... and it resets the counter: offline, degraded(200), offline never reaches 2.
    _clear_memory()
    assert [
        health._track("a", s, CFG, h, now=NOW) for s, h in (("offline", None), ("degraded", 200), ("offline", None))
    ] == [None] * 3


def test_5xx_event_names_the_http_status(session, wired):
    _svc(session)
    wired["notify_after_failures"] = 1
    r = {**_res("a", "degraded", NOW), "http_status": 503}
    _, events = health.process_results([r])
    assert [(e["event"], e["status"], e["error"]) for e in events] == [("down", "degraded", "HTTP 503")]


def test_disabling_clears_failures_and_notified_state(session, wired):
    _svc(session)
    health._failures["a"] = 3
    health._down_sent.add("a")
    session.add(models.HealthAlertState(service_id="a", failures=3, down_notified=True))
    session.commit()
    health._persisted["a"] = (3, True, None)
    session.get(models.Service, "a").health_enabled = False
    session.commit()
    due, live = health._scan(time.time())
    assert due == [] and live == {"a"}
    assert not health._failures and not health._down_sent
    session.expire_all()
    assert session.get(models.HealthAlertState, "a") is None
    # Re-enabled: starts from a clean slate (needs the full threshold again).
    session.get(models.Service, "a").health_enabled = True
    session.commit()
    wired["notify_after_failures"] = 2
    assert health._track("a", "offline", {**wired, "notify_after_failures": 2}, now=NOW) is None


def test_deleted_service_is_neither_recorded_nor_notified(session, wired):
    cfg, events = health.process_results([_res("ghost", "offline", NOW)])
    assert events == [] and not health._failures and not health._in_flight and not health._pending_since
    assert session.query(models.HealthCheck).count() == 0


def test_settings_failure_leaves_alert_state_untouched(session, monkeypatch, wired):
    _svc(session)
    health._down_sent.add("a")

    def boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(health, "_settings", boom)
    cfg, events = health.process_results([_res("a", "online", NOW)])
    assert cfg is None and events == []
    assert "a" in health._down_sent  # not cleared without having sent the recovery
    assert session.query(models.HealthCheck).count() == 1  # the check itself is still recorded


def test_db_failure_does_not_raise(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(health, "SessionLocal", boom)
    monkeypatch.setattr(health, "_settings", lambda: CFG)
    assert health.process_results([_res("a", "offline", NOW)])[1] == []


def test_first_checks_are_staggered_deterministically():
    offs = [health._first_offset(f"svc{i}", 30) for i in range(20)]
    assert all(0 <= o <= health.MAX_FIRST_CHECK_SPREAD for o in offs)
    assert len(set(offs)) > 10  # not phase-locked
    assert offs == [health._first_offset(f"svc{i}", 30) for i in range(20)]
    assert health._first_offset("x", 3) <= 3


# ---------- delivery: queue, retries, batching ----------
def test_retry_then_success_marks_notified_and_persists(session, wired, caplog):
    _svc(session)
    hook = Hook([500, 502, 200])
    caplog.set_level(logging.INFO)

    async def scenario():
        await tick(wired, _res("a", "online", NOW))
        await tick(wired, _res("a", "offline", NOW + 1, error="Timeout"))
        await settled()

    run_with_worker(hook, scenario)
    assert len(hook.calls) == 3
    assert hook.bodies[-1]["event"] == "down" and hook.bodies[-1]["error"] == "Timeout"
    assert "a" in health._down_sent and "a" not in health._pending_since
    session.expire_all()
    assert session.get(models.HealthAlertState, "a").down_notified is True
    assert "SECRET-TOKEN" not in caplog.text and "hooks.example" not in caplog.text


def test_permanent_failure_stays_pending_and_is_retried_once_a_minute(session, wired):
    _svc(session)
    hook = Hook([500] * 3)  # all three attempts of the first message fail

    async def scenario():
        await tick(wired, _res("a", "online", NOW))
        assert [e["event"] for e in await tick(wired, _res("a", "offline", NOW + 1))] == ["down"]
        await settled()
        assert len(hook.calls) == notify.ATTEMPTS
        assert "a" not in health._down_sent and "a" in health._pending_since
        session.expire_all()
        row = session.get(models.HealthAlertState, "a")
        assert row.down_notified is False and row.pending_since is not None
        # Still down, but retried at most once a minute...
        assert await tick(wired, _res("a", "offline", NOW + 2)) == []
        health._last_attempt["a"] -= health.RETRY_MIN_INTERVAL + 1
        # ... and then queued again; this time the receiver answers.
        assert [e["event"] for e in await tick(wired, _res("a", "offline", NOW + 3))] == ["down"]
        await settled()

    run_with_worker(hook, scenario)
    assert len(hook.calls) == notify.ATTEMPTS + 1
    assert "a" in health._down_sent and "a" not in health._pending_since


@pytest.mark.parametrize("kind", ["webhook", "discord"])
def test_one_tick_is_one_message(session, wired, kind):
    for sid, name in (("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")):
        _svc(session, sid, name)
    wired["notify_type"] = kind
    hook = Hook()

    async def scenario():
        await tick(wired, *(_res(s, "online", NOW) for s in "abc"))
        assert len(await tick(wired, *(_res(s, "offline", NOW + 1) for s in "abc"))) == 3
        await settled()
        assert len(await tick(wired, *(_res(s, "online", NOW + 2) for s in "abc"))) == 3
        await settled()

    run_with_worker(hook, scenario)
    assert len(hook.calls) == 2  # not six
    down, rec = hook.bodies
    if kind == "webhook":
        assert down["event"] == "batch" and [e["event"] for e in down["events"]] == ["down"] * 3
        assert {e["service"]["name"] for e in down["events"]} == {"Alpha", "Beta", "Gamma"}
        assert [e["event"] for e in rec["events"]] == ["recovered"] * 3
    else:
        assert "3 services down: Alpha, Beta, Gamma" in down["content"]
        assert "3 services recovered: Alpha, Beta, Gamma" in rec["content"]
    assert not health._down_sent


def test_single_event_keeps_the_single_event_shape(session, wired):
    _svc(session)
    hook = Hook()

    async def scenario():
        await tick(wired, _res("a", "online", NOW))
        await tick(wired, _res("a", "offline", NOW + 1))
        await settled()

    run_with_worker(hook, scenario)
    (body,) = hook.bodies
    assert body["event"] == "down" and "events" not in body and body["service"]["name"] == "Alpha"


def test_429_retry_after_is_honoured_and_capped(session, wired, monkeypatch):
    _svc(session)
    monkeypatch.setattr(notify, "MAX_RETRY_AFTER", 0.3)  # the receiver asks for 120 s
    hook = Hook([(429, {"Retry-After": "120"}), 200])

    async def scenario():
        await tick(wired, _res("a", "online", NOW))
        await tick(wired, _res("a", "offline", NOW + 1))
        await settled(timeout=5)

    run_with_worker(hook, scenario)
    assert len(hook.calls) == 2
    gap = hook.calls[1][0] - hook.calls[0][0]
    assert 0.25 <= gap < 2.0
    assert "a" in health._down_sent


def test_client_error_is_not_retried(session, wired):
    _svc(session)
    hook = Hook([404])

    async def scenario():
        await tick(wired, _res("a", "online", NOW))
        await tick(wired, _res("a", "offline", NOW + 1))
        await settled()

    run_with_worker(hook, scenario)
    assert len(hook.calls) == 1 and "a" not in health._down_sent and "a" in health._pending_since


def test_queue_overflow_drops_oldest_and_logs_once(monkeypatch, caplog):
    monkeypatch.setattr(notify, "QUEUE_MAX", 2)
    gate = {}
    done = {}

    async def handler(request):
        await gate["open"].wait()
        return httpx.Response(200)

    def msg(n):
        async def on_done(ok):
            done[n] = ok

        notify.submit(_cfg(), [notify.make_event("down", {"id": str(n), "name": str(n), "url": ""}, "offline", "online", None)], on_done)

    async def scenario():
        gate["open"] = asyncio.Event()
        msg(1)
        await asyncio.sleep(0.05)  # the worker takes #1 and blocks on the gate
        for n in (2, 3, 4, 5):  # queue holds 2: #2 then #3 are dropped
            msg(n)
        await asyncio.sleep(0.05)
        assert done == {2: False, 3: False}
        gate["open"].set()
        for _ in range(100):
            if len(done) == 3 + 1 + 1:
                break
            await asyncio.sleep(0.02)

    caplog.set_level(logging.WARNING)
    run_with_worker(handler, scenario)
    assert done == {1: True, 2: False, 3: False, 4: True, 5: True}
    assert sum("queue full" in r.message for r in caplog.records) == 1


# ---------- restart ----------
def test_restart_during_notified_outage_does_not_resend_and_recovery_is_sent_once(session, wired):
    _svc(session)
    hook = Hook()

    async def scenario():
        await tick(wired, _res("a", "online", NOW))
        await tick(wired, _res("a", "offline", NOW + 1))
        await settled()
        _clear_memory()  # the process restarts
        health.load_state()
        assert "a" in health._down_sent
        assert await tick(wired, _res("a", "offline", NOW + 2)) == []  # no second "down"
        assert [e["event"] for e in await tick(wired, _res("a", "online", NOW + 3))] == ["recovered"]
        await settled()
        assert await tick(wired, _res("a", "online", NOW + 4)) == []

    run_with_worker(hook, scenario)
    assert [b["event"] for b in hook.bodies] == ["down", "recovered"]


def test_recovery_that_happens_while_the_app_is_down_is_still_sent(session, wired):
    _svc(session)
    session.add(models.HealthAlertState(service_id="a", failures=5, down_notified=True))
    session.commit()
    hook = Hook()

    async def scenario():
        health.load_state()  # startup after the outage ended
        assert [e["event"] for e in await tick(wired, _res("a", "online", NOW))] == ["recovered"]
        await settled()

    run_with_worker(hook, scenario)
    assert [b["event"] for b in hook.bodies] == ["recovered"]
    session.expire_all()
    assert session.get(models.HealthAlertState, "a") is None  # back to a clean state


def test_failure_counter_survives_a_restart(session, wired):
    _svc(session)
    wired["notify_after_failures"] = 3
    hook = Hook()

    async def scenario():
        await tick(wired, _res("a", "offline", NOW))
        await tick(wired, _res("a", "offline", NOW + 1))
        _clear_memory()
        health.load_state()
        assert health._failures["a"] == 2
        assert [e["event"] for e in await tick(wired, _res("a", "offline", NOW + 2))] == ["down"]
        await settled()

    run_with_worker(hook, scenario)
    assert len(hook.calls) == 1


# ---------- manual checks ----------
def _fake_checker(monkeypatch, status="offline"):
    calls = []

    async def fake(client, spec):
        calls.append(spec["id"])
        return _res(spec["id"], status, time.time(), error="Boom" if status == "offline" else None)

    monkeypatch.setattr(health, "check_service", fake)
    return calls


def test_manual_check_records_but_does_not_count_or_notify_and_is_rate_limited(session, wired, monkeypatch):
    _svc(session)
    calls = _fake_checker(monkeypatch)
    first = asyncio.run(health.check_now("a"))
    assert first["status"] == "offline"
    assert not health._failures and not health._in_flight and not health._pending_since
    assert session.query(models.HealthCheck).count() == 1
    again = asyncio.run(health.check_now("a"))  # inside the 5 s window: the cached status
    assert calls == ["a"] and again == first
    health._last_manual["a"] -= health.MANUAL_MIN_INTERVAL + 1
    asyncio.run(health.check_now("a"))
    assert len(calls) == 2 and session.query(models.HealthCheck).count() == 2
    assert not health._failures


def test_manual_check_endpoint(client, session, wired, monkeypatch):
    _svc(session)
    calls = _fake_checker(monkeypatch, "online")
    assert client.post("/api/health/a/check", headers=HEADERS).json()["status"] == "online"
    assert client.post("/api/health/a/check", headers=HEADERS).status_code == 200
    assert len(calls) == 1
    assert client.post("/api/health/ghost/check", headers=HEADERS).status_code == 404


def test_loop_tick_batches_and_does_not_check_unscheduled_services(session, wired, monkeypatch):
    _svc(session, "a", "Alpha")
    _svc(session, "b", "Beta")
    _svc(session, "c", "Gamma")
    calls = _fake_checker(monkeypatch)
    hook = Hook()
    health._next_check.update({"a": 0, "b": 0, "c": time.time() + 999})

    async def scenario():
        async with httpx.AsyncClient() as client:
            await health._tick(client)
        await settled()

    run_with_worker(hook, scenario)
    assert sorted(calls) == ["a", "b"]
    (body,) = hook.bodies
    assert body["event"] == "batch" and len(body["events"]) == 2
    assert health._status["a"]["status"] == "offline" and "c" not in health._status


# ---------- payloads ----------
SERVICE = {"id": "a", "name": "Alpha", "url": "http://alpha"}


def test_payload_ntfy():
    req = notify.build_request({"notify_type": "ntfy"}, "down", SERVICE, "offline", "online", "Timeout")
    assert req["headers"]["Priority"] == "high"
    assert req["headers"]["Tags"] == "rotating_light"
    assert "Alpha" in req["headers"]["Title"]
    assert isinstance(req["content"], str) and "Timeout" in req["content"]
    rec = notify.build_request({"notify_type": "ntfy"}, "recovered", SERVICE, "online", "offline", None)
    assert rec["headers"]["Priority"] == "default"


def test_payload_ntfy_header_is_ascii_for_unicode_names():
    svc = {**SERVICE, "name": "Café"}
    req = notify.build_request({"notify_type": "ntfy"}, "down", svc, "offline", "online", None)
    req["headers"]["Title"].encode("ascii")
    assert "Café" in req["content"]


def test_payload_ntfy_title_survives_whitespace_and_control_characters():
    svc = {**SERVICE, "name": "  \n My\r\n\tNAS\x07 \x00"}
    req = notify.build_request({"notify_type": "ntfy"}, "down", svc, "offline", "online", None)
    title = req["headers"]["Title"]
    assert title == "My NAS is down"
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in title)
    batch = notify.build_batch(
        {"notify_type": "ntfy"},
        [notify.make_event("down", svc, "offline", "online", None), notify.make_event("down", SERVICE, "offline", "online", None)],
    )
    assert batch["headers"]["Title"] == "2 services down" and "\n" in batch["content"]


def test_payload_discord():
    req = notify.build_request({"notify_type": "discord"}, "down", SERVICE, "offline", "online", None)
    assert set(req) == {"json"} and "Alpha" in req["json"]["content"]
    assert req["json"]["allowed_mentions"] == {"parse": []}


def test_payload_discord_is_truncated_to_2000_characters():
    events = [
        notify.make_event("down", {"id": str(i), "name": f"@everyone service-{i}" + "x" * 60, "url": ""}, "offline", "online", "Err")
        for i in range(60)
    ]
    req = notify.build_batch({"notify_type": "discord"}, events)
    assert len(req["json"]["content"]) <= 2000 and req["json"]["allowed_mentions"] == {"parse": []}


def test_payload_webhook():
    req = notify.build_request({"notify_type": "webhook"}, "down", SERVICE, "offline", "online", "Timeout")
    body = req["json"]
    assert set(body) == {"event", "service", "status", "previous", "error", "at"}
    assert body["event"] == "down" and body["service"] == SERVICE
    assert (body["status"], body["previous"], body["error"]) == ("offline", "online", "Timeout")


def test_payload_webhook_batch_shape():
    evs = [notify.make_event("down", SERVICE, "offline", "online", "X"), notify.make_event("recovered", {**SERVICE, "id": "b"}, "online", "offline", None)]
    body = notify.build_batch({"notify_type": "webhook"}, evs)["json"]
    assert set(body) == {"event", "events", "at"} and body["event"] == "batch"
    assert [e["event"] for e in body["events"]] == ["down", "recovered"]
    assert set(body["events"][0]) == {"event", "service", "status", "previous", "error", "at"}


def test_httpx_loggers_are_quiet():
    from app import main  # noqa: F401  (configures logging)

    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


# ---------- uptime rounding ----------
def test_uptime_is_floored_so_only_a_perfect_record_reads_100(session):
    _svc(session)
    rows = [
        {"service_id": "a", "checked_at": health._to_dt(NOW - 100 - i * 0.5), "status": "online" if i else "offline"}
        for i in range(25000)
    ]
    session.bulk_insert_mappings(models.HealthCheck, rows[:1] + rows[1:])
    session.commit()
    (h,) = health.history(session, hours=4, buckets=4, now=NOW)
    assert h["checks"] == 25000 and h["uptime_pct"] == 99.99  # 99.996 rounds to 100.0, floors to 99.99
    _svc(session, "b", "Beta")
    _record(session, _res("b", "online", NOW - 5), _res("b", "online", NOW - 4), _res("b", "offline", NOW - 3))
    by_id = {x["service_id"]: x for x in health.history(session, hours=4, buckets=4, now=NOW)}
    assert by_id["b"]["uptime_pct"] == 66.66
    _svc(session, "c", "Gamma")
    _record(session, _res("c", "online", NOW - 5), _res("c", "degraded", NOW - 4))
    assert {x["service_id"]: x for x in health.history(session, hours=4, buckets=4, now=NOW)}["c"]["uptime_pct"] == 100.0


# ---------- startup migration ----------
def test_index_is_added_to_a_pre_existing_database(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text

    from app import main

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE services (id VARCHAR PRIMARY KEY, name VARCHAR)"))
        c.execute(
            text(
                "CREATE TABLE health_checks (id INTEGER PRIMARY KEY, service_id VARCHAR, "
                "checked_at DATETIME, status VARCHAR, latency_ms FLOAT)"
            )
        )
    monkeypatch.setattr(main, "engine", eng)
    names = lambda: {i["name"] for i in inspect(eng).get_indexes("health_checks")}  # noqa: E731
    assert models.HEALTH_CHECK_INDEX not in names()
    main._migrate()
    assert models.HEALTH_CHECK_INDEX in names()
    main._migrate()  # idempotent
    (idx,) = [i for i in inspect(eng).get_indexes("health_checks") if i["name"] == models.HEALTH_CHECK_INDEX]
    assert idx["column_names"] == ["service_id", "checked_at"]


def test_index_exists_on_a_fresh_database(session):
    from sqlalchemy import inspect

    names = {i["name"] for i in inspect(session.get_bind()).get_indexes("health_checks")}
    assert models.HEALTH_CHECK_INDEX in names


# ---------- /api/notify/test + settings validation ----------
def test_notify_test_validation(client):
    assert client.post("/api/notify/test", headers=HEADERS).status_code == 400  # type none
    client.patch("/api/settings", json={"notify_type": "ntfy"}, headers=HEADERS)
    assert client.post("/api/notify/test", headers=HEADERS).status_code == 400  # no URL


def test_notify_test_ok_and_error(client, monkeypatch):
    client.patch("/api/settings", json={"notify_type": "webhook", "notify_url": "https://h/x"}, headers=HEADERS)
    calls = []

    async def ok(url, **kw):
        calls.append((url, kw))

    monkeypatch.setattr(notify, "_post", ok)
    assert client.post("/api/notify/test", headers=HEADERS).json() == {"ok": True, "error": None}
    assert calls[0][0] == "https://h/x" and calls[0][1]["json"]["event"] == "test"  # the real URL, not the mask

    async def bad(url, **kw):
        raise notify.NotifyError("HTTP 401")

    monkeypatch.setattr(notify, "_post", bad)
    assert client.post("/api/notify/test", headers=HEADERS).json() == {"ok": False, "error": "HTTP 401"}


def test_settings_validation_and_defaults(client):
    s = client.get("/api/settings").json()
    assert (s["notify_type"], s["notify_url"], s["notify_url_set"], s["notify_on_recover"], s["notify_after_failures"]) == (
        "none", "", False, True, 2,
    )
    assert (s["health_history_days"], s["health_events_days"]) == (7, 90)
    for bad in (
        {"notify_url": "ftp://x"},
        {"notify_type": "sms"},
        {"notify_after_failures": 0},
        {"notify_after_failures": 11},
        {"health_history_days": 0},
        {"health_events_days": 9999},
    ):
        assert client.patch("/api/settings", json=bad, headers=HEADERS).status_code == 422, bad
    ok = client.patch("/api/settings", json={"notify_url": " https://x/y "}, headers=HEADERS)
    assert ok.json()["notify_url"] == "********" and ok.json()["notify_url_set"] is True


# ---------- notify_url is write-only ----------
def _stored_url(session):
    from app.routers.settings import load

    session.expire_all()
    return load(session)["notify_url"]


def test_notify_url_mask_round_trip(client, session):
    url = "https://discord.example/api/webhooks/1/SECRET"
    patch = lambda body: client.patch("/api/settings", json=body, headers=HEADERS)  # noqa: E731
    r = patch({"notify_type": "webhook", "notify_url": url})
    assert r.json()["notify_url"] == "********" and r.json()["notify_url_set"] is True
    assert "SECRET" not in r.text and "SECRET" not in client.get("/api/settings").text
    assert client.get("/api/settings").json()["notify_url"] == "********"
    assert _stored_url(session) == url

    # The mask sent back, or the key omitted, keeps the stored URL; notify_url_set is read-only.
    assert patch({"notify_url": "********", "title": "T"}).status_code == 200
    assert _stored_url(session) == url
    assert patch({"title": "U", "notify_url_set": False}).json()["notify_url_set"] is True
    assert _stored_url(session) == url
    # A new URL replaces it; "" clears it.
    new = "https://ntfy.example/topic"
    assert patch({"notify_url": new}).status_code == 200 and _stored_url(session) == new
    cleared = patch({"notify_url": ""}).json()
    assert cleared["notify_url"] == "" and cleared["notify_url_set"] is False
    assert _stored_url(session) == ""
    assert client.get("/api/settings").json()["notify_url"] == ""


def test_exports_keep_the_url_and_import_never_stores_the_mask(client, session):
    url = "https://discord.example/api/webhooks/1/SECRET"
    client.patch("/api/settings", json={"notify_type": "webhook", "notify_url": url}, headers=HEADERS)
    bundle = client.get("/api/config/export").json()
    assert bundle["settings"]["notify_url"] == url  # exports and backups are the user's own copy

    masked = {**bundle, "settings": {**bundle["settings"], "notify_url": "********", "notify_url_set": True, "title": "X"}}
    assert client.post("/api/config/import", json=masked, headers=HEADERS).status_code == 200
    assert _stored_url(session) == url
    from app.routers.settings import load

    assert "notify_url_set" not in session.get(models.AppSetting, "app").value
    assert load(session)["title"] == "X"
    # A real round trip restores the URL itself.
    client.patch("/api/settings", json={"notify_url": ""}, headers=HEADERS)
    assert client.post("/api/config/import", json=bundle, headers=HEADERS).status_code == 200
    assert _stored_url(session) == url


def test_event_for_a_5xx_transition_stores_the_http_status_as_its_error(session):
    _svc(session)
    r = {**_res("a", "degraded", NOW + 1), "http_status": 503}
    _record(session, _res("a", "online", NOW), r)
    ev = session.query(models.HealthEvent).one()
    assert (ev.from_status, ev.to_status, ev.error) == ("online", "degraded", "HTTP 503")


def test_event_error_prefers_the_check_error_and_stays_empty_for_recovery(session):
    _svc(session)
    _record(session, _res("a", "online", NOW), _res("a", "offline", NOW + 1, error="ConnectError"), _res("a", "online", NOW + 2))
    errors = [e.error for e in session.query(models.HealthEvent).order_by(models.HealthEvent.id)]
    assert errors == ["ConnectError", None]
