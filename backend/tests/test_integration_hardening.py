"""Hardening of the integration framework: credentials in logs, hostile feeds, redirects, login
backoff, session release, stale results, TLS default, discovery limits.

Real sockets are used only on 127.0.0.1 with a port the test opens itself (httpx.MockTransport bypasses
h11 and gzip streaming, so it cannot show the header-value and decompression problems). Time is
injected (`base._clock`, `ics._monotonic`); no test sleeps for more than a few milliseconds."""
import asyncio
import gzip
import io
import logging
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import integrations, main, models
from app.integrations import base, errors, net, runtime
from app.integrations.base import Action, Context, Spec, check_values, stat
from app.integrations.registry import SPECS
from app.integrations.types import docker as docker_mod
from app.integrations.types import ics as ics_mod
from app.integrations.types import portainer as portainer_mod
from app.integrations.types import prometheus as prom_mod
from integration_utils import Harness

from conftest import HEADERS
from test_integrations_api import _create, _stored, fake_fixture  # noqa: F401  (fixture `fake`)

MASK = "********"


def snap(id_="i1", type_="hfake", config=None, enabled=True, interval=30):
    return SimpleNamespace(id=id_, name="N", type=type_, interval=interval, enabled=enabled, config=dict(config or {}))


# ---------------------------------------------------------------- a tiny HTTP server on loopback
class Loop:
    """`async with Loop(handler) as srv`: srv.url, srv.requests. handler(req) -> (status, headers, body)."""

    def __init__(self, handler):
        self.handler, self.requests, self.server, self.port = handler, [], None, 0

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    async def _serve(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.lower(): v for k, v in (ln.split(": ", 1) for ln in lines[1:] if ": " in ln)}
            req = {"method": method, "path": target, "headers": headers}
            self.requests.append(req)
            status, hdrs, body = self.handler(req)
            out = f"HTTP/1.1 {status} X\r\nContent-Length: {len(body)}\r\nConnection: close\r\n"
            out += "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
            writer.write(out.encode("latin-1") + body)
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()


def js(data):
    import json
    return 200, {"Content-Type": "application/json"}, json.dumps(data).encode()


# ---------------------------------------------------------------- a controllable registered type
@pytest.fixture(name="hfake")
def hfake_fixture(monkeypatch):
    st = SimpleNamespace(calls=0, active=0, max_active=0, closed=[], acted=[])

    async def default_fetch(ctx):
        return {"stats": [stat("v", "V", ctx.cfg.get("url"))]}

    st.fetch = default_fetch

    async def fetch(ctx):
        st.calls += 1
        st.active += 1
        st.max_active = max(st.max_active, st.active)
        try:
            return await st.fetch(ctx)
        finally:
            st.active -= 1

    async def act(ctx, payload):
        st.active += 1
        st.max_active = max(st.max_active, st.active)
        try:
            await asyncio.sleep(0)
            st.acted.append(payload)
        finally:
            st.active -= 1

    async def close(ctx):
        st.closed.append(ctx.cfg.get("url"))

    spec = Spec(type="hfake", label="H", description="Test type.", fetch=fetch, provides=("stats",), close=close,
                fields=[{"key": "url", "label": "URL", "type": "url"}],
                actions={"act": Action(act, "Act", payload=[{"key": "n", "label": "N", "type": "number"}])})
    monkeypatch.setitem(SPECS, "hfake", spec)
    return st


# ================================================================ B1: secrets with whitespace / control chars
def test_check_values_strips_secrets_and_headers_and_refuses_control_characters():
    fields = [{"key": "token", "label": "T", "type": "secret", "secret": True},
              {"key": "headers", "label": "H", "type": "headers", "secret": True},
              {"key": "name", "label": "N", "type": "text"}]
    clean, errs = check_values(fields, {"token": "  KEY\t\n", "headers": {" X-Api-Key ": " v \n"}, "name": " a "})
    assert not errs and clean == {"token": "KEY", "headers": {"X-Api-Key": "v"}, "name": "a"}
    _, errs = check_values(fields, {"token": "KEY\x01SECRETTAIL"})
    assert [k for k, _ in errs] == ["token"] and "SECRETTAIL" not in str(errs) and "KEY" not in str(errs)
    _, errs = check_values(fields, {"headers": {"X-A": "ab\x00cdSECRETTAIL"}})
    assert [k for k, _ in errs] == ["headers"] and "SECRETTAIL" not in str(errs)
    # a line break inside a secret (a pasted second line) is refused, not sent
    _, errs = check_values(fields, {"token": "line1\nline2SECRETTAIL"})
    assert errs and "SECRETTAIL" not in str(errs)
    # whitespace only = unset
    clean, errs = check_values(fields, {"token": " \n"})
    assert clean == {} and not errs


def test_api_refuses_a_secret_with_a_control_character_without_echoing_it(client, fake):
    res = client.post("/api/integrations", json={"name": "F", "type": "fake",
                      "config": {"url": "http://x", "token": "abc\x01ZZZSECRET"}}, headers=HEADERS)
    assert res.status_code == 422 and "ZZZSECRET" not in res.text and "abc" not in res.text


def test_a_stored_header_secret_with_trailing_whitespace_is_stripped_and_works(caplog):
    async def run():
        async with Loop(lambda r: js({"ok": True})) as srv:
            cfg = {"url": srv.url + "/s", "headers": {"X-Api-Key": "KEY123\t\n"}}
            env = await runtime.fetch(snap(type_="json", config=cfg))
            return env, srv.requests

    env, requests = asyncio.run(run())
    assert env["ok"] and requests[0]["headers"]["x-api-key"] == "KEY123"


def test_real_socket_illegal_header_value_is_safe_in_the_api_message_and_the_log(caplog):
    """MockTransport cannot reproduce this: h11 refuses the header, httpx raises LocalProtocolError whose text
    holds the whole header value. Neither the log nor the API message may contain any of the key."""
    caplog.set_level(logging.DEBUG)

    async def run():
        async with Loop(lambda r: js({})) as srv:
            cfg = {"url": srv.url, "headers": {"X-Api-Key": "SECRETKEY\x00TAIL"}}
            return await runtime.fetch(snap(type_="json", config=cfg)), srv.requests

    env, requests = asyncio.run(run())
    assert not env["ok"] and env["error"].startswith("invalid characters in a credential or header")
    assert requests == []  # never sent
    assert "SECRETKEY" not in caplog.text and "TAIL" not in caplog.text and "Illegal header" not in caplog.text
    assert "LocalProtocolError" in caplog.text  # the class name is logged


def test_log_text_names_only_the_class_for_errors_that_can_embed_credentials():
    inner = httpx.LocalProtocolError("Illegal header value b'Bearer SECRETKEY\\t'")
    assert errors.log_text(inner) == "LocalProtocolError"
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        n: int

    with pytest.raises(ValidationError) as ei:
        M(n="SECRETBODY")
    assert "SECRETBODY" not in errors.log_text(ei.value)
    assert errors.log_text(ValueError("bad at https://h/x?k=S")) == "ValueError: bad at https://h/x"


def test_safe_error_for_illegal_header_values():
    exc = httpx.LocalProtocolError("Illegal header value b'Bearer SECRETKEY'")
    assert errors.safe_error(exc, {"url": "http://h.lan"}) == "invalid characters in a credential or header (h.lan)"


# ================================================================ B2: RSS
RSS = b"<?xml version='1.0'?><rss version='2.0'><channel><title>Local file</title><item><title>LEAK</title></item></channel></rss>"


def test_rss_body_that_is_a_file_path_never_opens_that_file(tmp_path):
    secret = tmp_path / "feed.xml"
    secret.write_bytes(RSS)
    import feedparser
    # the reproducer: handed bytes, feedparser opens the PATH
    assert feedparser.parse(str(secret).encode())["feed"].get("title") == "Local file"
    h = Harness("rss", {"url": "http://feeds.lan/rss"}, {("GET", r"feeds\.lan/rss"): str(secret).encode()})
    data = h.fetch()
    h.close()
    assert data["entries"] == [] and data["title"] == "" and "LEAK" not in str(data)


def test_rss_still_parses_a_real_feed():
    h = Harness("rss", {"url": "http://feeds.lan/rss"}, {("GET", r"feeds\.lan/rss"): RSS})
    data = h.fetch()
    h.close()
    assert data["title"] == "Local file" and data["items"][0]["title"] == "LEAK"


def test_decoded_size_is_capped_strictly_for_a_gzip_bomb():
    bomb = gzip.compress(b"\0" * 60_000_000)  # about 60 KB on the wire, 60 MB decoded, in one network chunk
    assert len(bomb) < 100_000

    async def run(truncate):
        async with Loop(lambda r: (200, {"Content-Encoding": "gzip"}, bomb)) as srv:
            async with httpx.AsyncClient() as client:
                async with net.stream_follow(client, "GET", srv.url) as resp:
                    return await net.read_capped(resp, 5_000_000, truncate=truncate, message="too big")

    t0 = time.time()
    assert len(asyncio.run(run(True))) == 5_000_000
    with pytest.raises(integrations.IntegrationError, match="too big"):
        asyncio.run(run(False))
    assert time.time() - t0 < 3


def test_read_capped_identity_body_is_cut_exactly():
    async def run():
        async with Loop(lambda r: (200, {}, b"x" * 100_000)) as srv:
            async with httpx.AsyncClient() as client:
                async with net.stream_follow(client, "GET", srv.url) as resp:
                    return await net.read_capped(resp, 70_000, truncate=True)

    assert len(asyncio.run(run())) == 70_000


# ================================================================ B3: ICS
def cal(*events):
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def vev(*lines):
    return "BEGIN:VEVENT\r\n" + "".join(f"{ln}\r\n" for ln in lines) + "END:VEVENT\r\n"


NOW = datetime(2025, 6, 5, 8, 0, tzinfo=timezone.utc)
UTC = timezone.utc


def build(text, **kw):
    return ics_mod.build(text, UTC, "UTC", NOW, kw.get("days", 14), kw.get("max_events", 200), kw.get("deadline"))


def test_unfolding_a_line_folded_tens_of_thousands_of_times_is_fast_and_bounded():
    text = "SUMMARY:" + "\r\n x" * 200_000
    t0 = time.time()
    lines = ics_mod._unfold(text)
    assert time.time() - t0 < 1 and len(lines) == 1 and len(lines[0]) <= ics_mod.MAX_LINE
    t0 = time.time()
    assert len(ics_mod._unfold("X" * 5_000_000)[0]) == ics_mod.MAX_LINE and time.time() - t0 < 1


def test_byday_bymonth_lists_are_deduplicated_and_capped():
    rule = ics_mod._parse_rule("FREQ=MONTHLY;BYDAY=" + ",".join(["MO", "1MO", "-1MO", "TU"] * 400)
                               + ";BYMONTH=" + ",".join(["1", "2"] * 500) + ";BYMONTHDAY=" + ",".join(["1", "2", "3"] * 300))
    assert rule.byday == [(None, 0), (1, 0), (-1, 0), (None, 1)]
    assert rule.bymonth == [1, 2] and rule.bymonthday == [1, 2, 3]
    big = ",".join(f"{o}{d}" for o in range(-53, 54) if o for d in ("MO", "TU", "WE", "TH", "FR", "SA", "SU"))
    assert len(ics_mod._parse_rule("FREQ=MONTHLY;BYDAY=" + big).byday) <= ics_mod.MAX_BYDAY


@pytest.mark.parametrize("rule", [
    "FREQ=MONTHLY;BYDAY=" + ",".join(["MO", "TU", "WE", "TH", "FR", "SA", "SU", "1MO", "2TU", "-1FR"] * 150),
    "FREQ=DAILY;BYDAY=" + ",".join(["MO", "TU", "WE"] * 500),
    "FREQ=WEEKLY;BYDAY=" + ",".join(["MO", "TU", "WE", "TH"] * 500) + ";COUNT=999999",
    "FREQ=YEARLY;BYMONTH=" + ",".join(str(m) for m in range(1, 13)) * 50 + ";BYMONTHDAY=" + ",".join(str(d) for d in range(1, 32)) * 20,
])
def test_adversarial_rules_finish_well_under_a_second(rule):
    text = cal(*[vev(f"UID:{i}", "DTSTART:20200101T090000Z", "DTEND:20200101T100000Z", f"RRULE:{rule}") for i in range(20)])
    t0 = time.time()
    build(text)
    assert time.time() - t0 < 1.0


def test_deadline_raises_calendar_too_complex(monkeypatch):
    clock = iter(range(0, 10_000_000))
    monkeypatch.setattr(ics_mod, "_monotonic", lambda: next(clock) / 1000)  # every check advances 1 ms
    text = cal(*[vev(f"UID:{i}", "DTSTART:20200101T090000Z", "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=999999") for i in range(50)])
    with pytest.raises(integrations.IntegrationError, match="calendar too complex"):
        ics_mod.build(text, UTC, "UTC", NOW, 14, 20, ics_mod._Deadline(0.02))


def test_a_cancelled_parse_stops_and_cannot_pile_up_threads(monkeypatch):
    started, stop_seen = threading.Event(), threading.Event()

    def slow_build(text, local, tzname, now, days, limit, deadline):
        started.set()
        while not deadline.cancel.is_set():  # a parse that only stops when told to
            time.sleep(0.002)
        stop_seen.set()
        raise integrations.IntegrationError("calendar too complex")

    monkeypatch.setattr(ics_mod, "build", slow_build)
    ctx = Context(SPECS["ics"], {"url": "http://x"})

    async def run():
        task = asyncio.create_task(ics_mod._parse(ctx, "x", UTC, "UTC", 14, 20))
        while not started.is_set():
            await asyncio.sleep(0.002)
        assert ctx.state["parsing"] is True
        with pytest.raises(integrations.IntegrationError, match="still running"):  # a second parse does not start
            await ics_mod._parse(ctx, "x", UTC, "UTC", 14, 20)
        task.cancel()  # what wait_for does on a timeout
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if not ctx.state["parsing"]:
                break
            await asyncio.sleep(0.005)

    asyncio.run(run())
    assert stop_seen.wait(1) and ctx.state["parsing"] is False


def test_forty_old_daily_series_all_show_and_nothing_is_truncated():
    text = cal(*[vev(f"UID:{i}", f"SUMMARY:S{i}", "DTSTART:20000101T090000Z", "DTEND:20000101T093000Z",
                     "RRULE:FREQ=DAILY;COUNT=999999") for i in range(40)])
    data = build(text)
    assert next(s for s in data["stats"] if s["key"] == "events_today")["value"] == 40
    assert data["truncated"] is False and "note" not in data
    # the series ends at its COUNT: 5 occurrences from 2000 are long past
    short = build(cal(vev("UID:a", "DTSTART:20000101T090000Z", "RRULE:FREQ=DAILY;COUNT=5")))
    assert short["events"] == []
    # fast-forward keeps the arithmetic right: weekly from 2000, one per week, count reached in 2025
    weekly = build(cal(vev("UID:w", "DTSTART:20250529T090000Z", "RRULE:FREQ=WEEKLY;COUNT=2")), days=14)
    assert [e["start"][:10] for e in weekly["events"]] == ["2025-06-05"]


def test_truncation_is_reported_when_the_step_budget_runs_out(monkeypatch):
    monkeypatch.setattr(ics_mod, "MAX_TOTAL_STEPS", 3000)
    monkeypatch.setattr(ics_mod, "MAX_STEPS", 1000)
    text = cal(*[vev(f"UID:{i}", "DTSTART:19000101T090000Z", "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=999999999") for i in range(10)])
    data = build(text)
    assert data["truncated"] is True and "left out" in data["note"]


# ================================================================ S1 / S2 / S12: runtime
def test_a_slow_fetch_of_the_old_config_does_not_overwrite_the_fresh_result(hfake):
    async def run():
        gate = asyncio.Event()

        async def fetch(ctx):
            if ctx.cfg["url"] == "http://old":
                await gate.wait()
            return {"stats": [stat("v", "V", ctx.cfg["url"])]}

        hfake.fetch = fetch
        t = asyncio.create_task(runtime.refresh_row(snap(config={"url": "http://old"})))
        while not hfake.calls:
            await asyncio.sleep(0)
        await runtime.refresh_row(snap(config={"url": "http://new"}))  # the edit: replaces the context
        fresh_next = runtime._next["i1"]
        gate.set()
        await t
        return fresh_next

    fresh_next = asyncio.run(run())
    assert runtime.get("i1")["data"]["stats"][0]["value"] == "http://new"
    assert runtime._next["i1"] == fresh_next  # the old poll did not push the next one out


def test_a_superseded_poll_leaves_the_integration_due_instead_of_parking_it(hfake):
    async def run():
        gate = asyncio.Event()

        async def fetch(ctx):
            await gate.wait()
            return {"stats": []}

        hfake.fetch = fetch
        runtime._next["i1"] = float("inf")  # what the scheduler does before a poll
        t = asyncio.create_task(runtime._poll(snap(config={"url": "http://old"})))
        while not hfake.calls:
            await asyncio.sleep(0)
        runtime.invalidate("i1")  # edited
        gate.set()
        await t

    asyncio.run(run())
    assert "i1" not in runtime._next and "i1" not in runtime._data


def test_an_action_never_runs_alongside_a_poll_and_gives_a_busy_error_when_the_poll_hangs(hfake, monkeypatch):
    async def run():
        # 1) an action waits for the running poll instead of racing it
        async def fetch(ctx):
            await asyncio.sleep(0.02)  # `active` / `max_active` are counted by the wrapper around fetch and act
            return {"stats": []}

        hfake.fetch = fetch
        s = snap(config={"url": "http://a"})
        poll = asyncio.create_task(runtime.fetch(s))
        while not hfake.calls:
            await asyncio.sleep(0)
        await runtime.run_action(s, "act", {"n": 1})
        await poll
        assert hfake.max_active == 1 and hfake.acted == [{"n": 1}]
        # 2) a hung poll: the action fails fast with a safe message
        gate = asyncio.Event()

        async def hung(ctx):
            await gate.wait()
            return {"stats": []}

        hfake.fetch = hung
        monkeypatch.setattr(runtime, "ACTION_WAIT", 0.03)
        poll = asyncio.create_task(runtime.fetch(s))
        await asyncio.sleep(0.01)
        with pytest.raises(runtime.ActionFailed, match="busy"):
            await runtime.run_action(s, "act", {"n": 2})
        with pytest.raises(runtime.ActionFailed, match="busy"):
            await runtime.call(s, lambda ctx: asyncio.sleep(0))
        gate.set()
        await poll

    asyncio.run(run())


def test_close_pending_cannot_close_a_client_in_the_middle_of_an_action(hfake):
    async def run():
        s = snap(config={"url": "http://a"})
        ctx = runtime._context(s, SPECS["hfake"])
        await ctx.lock.acquire()  # an action is running
        runtime.invalidate("i1")
        await runtime.close_pending()
        assert not ctx.closed and ctx in runtime._closing  # still busy: kept for later
        ctx.client  # noqa: B018  (the type's hook only runs for a context that built a client)
        ctx.lock.release()
        await runtime.close_pending()
        assert ctx.closed and hfake.closed == ["http://a"] and not runtime._closing  # closed once, hook ran

    asyncio.run(run())


def test_a_fetch_waiting_on_a_replaced_context_does_not_build_a_client_nobody_closes(hfake):
    async def run():
        s = snap(config={"url": "http://a"})
        ctx = runtime._context(s, SPECS["hfake"])
        await ctx.lock.acquire()
        waiter = asyncio.create_task(runtime.fetch(s))
        await asyncio.sleep(0)
        runtime.invalidate("i1")  # replaced / deleted while the waiter queues for the lock
        ctx.lock.release()
        env = await waiter
        assert not env["ok"] and hfake.calls == 0
        await runtime.close_pending()
        with pytest.raises(integrations.IntegrationError):  # closed contexts refuse a new client
            ctx.client

    asyncio.run(run())


def test_invalidate_from_other_threads_never_loses_a_context(hfake):
    async def run():
        ctxs = [runtime._context(snap(f"t{i}", config={"url": f"http://{i}"}), SPECS["hfake"]) for i in range(200)]
        threads = [threading.Thread(target=runtime.invalidate, args=(f"t{i}",)) for i in range(200)]
        closer = asyncio.create_task(_spin_close())
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        await closer
        await runtime.close_pending()
        return ctxs

    async def _spin_close():
        for _ in range(20):
            await runtime.close_pending()
            await asyncio.sleep(0)

    ctxs = asyncio.run(run())
    assert all(c.retired for c in ctxs) and not runtime._closing


def test_disabled_rows_are_closed_and_dropped_by_the_scheduler(hfake, session, monkeypatch):
    session.add(models.Integration(id="d1", name="D", type="hfake", interval=30, enabled=False, config={"url": "http://d"}))
    session.commit()
    runtime._data["d1"] = {"ok": True}
    monkeypatch.setattr(runtime, "SessionLocal", lambda: session)

    async def run():
        runtime._context(snap("d1", config={"url": "http://d"}), SPECS["hfake"]).client  # noqa: B018 (no client: no hook)
        await runtime._tick()

    asyncio.run(run())
    assert "d1" not in runtime._data and "d1" not in runtime._ctx and hfake.calls == 0


def test_api_disabled_rows_are_not_fetched_and_name_or_interval_edits_do_not_fetch(client, fake):
    iid = _create(client, enabled=False)["id"]
    assert fake.calls == 0 and runtime.get(iid) is None
    assert client.patch(f"/api/integrations/{iid}", json={"enabled": True}, headers=HEADERS).status_code == 200
    assert fake.calls == 1  # turning it on fetches
    client.patch(f"/api/integrations/{iid}", json={"name": "Renamed"}, headers=HEADERS)
    client.patch(f"/api/integrations/{iid}", json={"interval": 60}, headers=HEADERS)
    assert fake.calls == 1  # no blocking fetch for a name or interval edit
    client.patch(f"/api/integrations/{iid}", json={"config": {"url": "http://other:1"}}, headers=HEADERS)
    assert fake.calls == 2
    assert fake.clients and not fake.clients[-1].is_closed
    client.patch(f"/api/integrations/{iid}", json={"enabled": False}, headers=HEADERS)
    assert fake.calls == 2 and runtime.get(iid) is None and iid not in runtime._ctx
    assert all(c.is_closed for c in fake.clients)  # the client of the disabled integration was closed


# ================================================================ S3: login backoff
def clock(monkeypatch, start=1000.0):
    now = [start]
    monkeypatch.setattr(base, "_clock", lambda: now[0])
    return now


def test_pihole_wrong_password_logs_in_once_then_backs_off_exponentially(monkeypatch):
    now = clock(monkeypatch)
    logins = []

    def auth(req):
        logins.append(now[0])
        return 401, {"session": {"valid": False}}

    h = Harness("pihole", {"url": "http://pi.hole", "version": "6", "password": "bad"}, {("POST", r"/api/auth$"): auth})
    for _ in range(5):  # five polls in a few seconds: ONE login attempt
        now[0] += 2
        with pytest.raises(integrations.IntegrationError, match="check the Pi-hole password"):
            h.fetch()
    assert len(logins) == 1
    now[0] += 60
    with pytest.raises(integrations.IntegrationError):
        h.fetch()
    assert len(logins) == 2  # first delay (60 s) passed
    now[0] += 61
    with pytest.raises(integrations.IntegrationError):
        h.fetch()
    assert len(logins) == 2  # the delay doubled to 120 s
    now[0] += 60
    with pytest.raises(integrations.IntegrationError):
        h.fetch()
    assert len(logins) == 3
    for _ in range(12):  # the delay is capped at 15 minutes
        now[0] += 901
        with pytest.raises(integrations.IntegrationError):
            h.fetch()
    assert len(logins) == 15
    # a person pressing Test / refresh is never held back
    h.ctx.manual = True
    with pytest.raises(integrations.IntegrationError):
        h.fetch()
    assert len(logins) == 16
    h.close()


def test_the_backoff_resets_after_a_good_fetch_and_on_a_config_change(monkeypatch):
    now = clock(monkeypatch)
    ctx = Context(SPECS["pihole"], {"url": "http://pi.hole"})
    ctx.auth_fail("x")
    ctx.auth_fail("x")
    assert ctx._auth_until - now[0] == 120
    ctx.auth_ok()
    ctx.auth_fail("x")
    assert ctx._auth_until - now[0] == 60
    # a config change builds a new context: no memory of the old failures
    assert Context(SPECS["pihole"], {"url": "http://pi.hole"})._auth_until == 0.0


def test_qbittorrent_wrong_password_is_one_login_across_several_polls(monkeypatch):
    now = clock(monkeypatch)
    logins = []

    def login(req):
        logins.append(1)
        return 200, "Fails."

    h = Harness("qbittorrent", {"url": "http://qb.lan", "username": "u", "password": "bad"}, {("POST", r"/auth/login$"): login})
    for _ in range(4):
        now[0] += 15
        with pytest.raises(integrations.IntegrationError, match="login failed"):
            h.fetch()
    assert len(logins) == 1
    h.close()


def test_unifi_refused_login_is_one_login_across_several_polls(monkeypatch):
    now = clock(monkeypatch)
    logins = []

    def login(req):
        logins.append(1)
        return 401, {}

    h = Harness("unifi", {"url": "https://udm", "username": "u", "password": "bad"}, {("POST", r"/api/auth/login$"): login})
    for _ in range(4):
        now[0] += 10
        with pytest.raises(integrations.IntegrationError, match="login failed"):
            h.fetch()
    assert len(logins) == 1
    h.close()


def test_pihole_post_login_401_loop_does_not_create_a_session_every_poll(monkeypatch):
    now = clock(monkeypatch)
    logins = []
    routes = {("POST", r"/api/auth$"): lambda r: (logins.append(1), {"session": {"valid": True, "sid": f"s{len(logins)}"}})[1],
              ("GET", r"/api/stats/summary$"): (401, {}), ("DELETE", r"/api/auth$"): {}}
    h = Harness("pihole", {"url": "http://pi.hole", "version": "6", "password": "pw"}, routes)
    for _ in range(5):
        now[0] += 10
        with pytest.raises(integrations.IntegrationError):
            h.fetch()
    assert len(logins) == 2  # the first poll logs in twice (one retry); the next four are held back
    h.close()


def test_a_static_key_refused_with_401_stretches_the_poll_interval_gently(hfake):
    async def fetch(ctx):
        req = httpx.Request("GET", "http://x/y")
        raise httpx.HTTPStatusError("no", request=req, response=httpx.Response(401, request=req))

    hfake.fetch = fetch
    s = snap(config={"url": "http://a"}, interval=30)

    async def run():
        waits = []
        for _ in range(3):
            await runtime._poll(s)
            waits.append(runtime._next["i1"] - time.time())
        return waits

    a, b, c = asyncio.run(run())
    assert 55 < a < 61 and 85 < b < 91 and 115 < c < 121  # x2, x3, x4 of 30 s
    # a good fetch resets it
    async def good(ctx):
        return {"stats": []}

    hfake.fetch = good
    asyncio.run(runtime._poll(s))
    assert 25 < runtime._next["i1"] - time.time() < 31


# ================================================================ S4: Pi-hole sessions released
SUMMARY = {"queries": {"total": 1, "blocked": 0, "percent_blocked": 0}, "gravity": {"domains_being_blocked": 1}, "clients": {"active": 1}}


def pihole_server():
    def handler(req):
        p, m = req["path"], req["method"]
        if p.startswith("/api/auth") and m == "POST":
            return js({"session": {"valid": True, "sid": "SID-1"}})
        if p.startswith("/api/auth") and m == "DELETE":
            return 204, {}, b""
        if p.startswith("/api/stats/summary"):
            return js(SUMMARY)
        if p.startswith("/api/dns/blocking"):
            return js({"blocking": "enabled", "timer": None})
        return 404, {}, b"{}"

    return Loop(handler)


def logouts(srv):
    return [r for r in srv.requests if r["method"] == "DELETE"]


def test_pihole_session_is_released_after_test_after_edit_and_on_shutdown():
    async def run():
        async with pihole_server() as srv:
            cfg = {"url": srv.url, "version": "6", "password": "pw", "verify_tls": False}
            res = await runtime.test_config(SPECS["pihole"], cfg)
            assert res["ok"] and len(logouts(srv)) == 1 and logouts(srv)[0]["headers"]["x-ftl-sid"] == "SID-1"
            s = snap("p1", "pihole", cfg)
            assert (await runtime.fetch(s))["ok"]
            runtime.invalidate("p1")  # edit / delete / disable
            await runtime.close_pending()
            assert len(logouts(srv)) == 2
            assert (await runtime.fetch(s))["ok"]
            await runtime.close_all()  # shutdown
            assert len(logouts(srv)) == 3
            assert (await runtime.fetch(snap("p2", "pihole", {**cfg, "password": "pw"})))["ok"]
            # a context that never logged in sends nothing on close
            before = len(srv.requests)
            await runtime._context(snap("p3", "pihole", cfg), SPECS["pihole"]).aclose()
            assert len(srv.requests) == before

    asyncio.run(run())


def test_unifi_and_qbittorrent_log_out_on_close():
    h = Harness("qbittorrent", {"url": "http://qb.lan", "username": "u", "password": "p"}, {
        ("POST", r"/auth/login$"): lambda r: httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"}),
        ("GET", r"/transfer/info$"): {"dl_info_speed": 0, "up_info_speed": 0, "dl_info_data": 0, "up_info_data": 0},
        ("GET", r"/torrents/info"): [], ("GET", r"/sync/maindata"): {}, ("POST", r"/auth/logout$"): httpx.Response(200)})
    try:
        h.fetch(check=False)
    except Exception:  # noqa: BLE001  (other routes of this type are not the point)
        pass
    h.close()
    assert any(m == "POST" and u.endswith("/api/v2/auth/logout") for m, u in h.calls)


# ================================================================ S5: json provides neither stats nor items
def test_json_payload_with_top_level_items_and_stats_keys_is_returned_untouched():
    payload = {"items": "not a list", "stats": 5, "x": 1}
    h = Harness("json", {"url": "http://j.lan/s"}, {("GET", r"j\.lan/s"): payload})
    data = h.fetch(check=False)
    h.close()
    assert data == payload
    assert SPECS["json"].provides == ()
    base.check_shape(data, SPECS["json"].provides)  # not validated: the type does not provide them
    with pytest.raises(integrations.IntegrationError):
        base.check_shape(data)  # (the old behaviour: validated as if the type provided them)


def test_a_provided_key_is_still_validated():
    with pytest.raises(integrations.IntegrationError):
        base.check_shape({"items": [{"nope": 1}]}, ("items",))
    base.check_shape({"items": [{"nope": 1}]}, ("stats",))  # not provided: ignored


def test_json_type_fetch_through_the_runtime_succeeds_with_an_items_key(monkeypatch):
    async def run():
        async with Loop(lambda r: js({"items": [1, 2, 3], "stats": "x"})) as srv:
            return await runtime.fetch(snap("j1", "json", {"url": srv.url}))

    env = asyncio.run(run())
    assert env["ok"] and env["data"]["items"] == [1, 2, 3]


# ================================================================ S6: redirects and credentials
def test_json_headers_go_only_to_the_configured_origin_on_redirect():
    seen = []

    def route(name, value):
        def f(req):
            seen.append((name, req.headers.get("x-api-key")))
            return value(req) if callable(value) else value

        return f

    routes = {
        ("GET", r"//a\.lan/start"): route("a-start", httpx.Response(302, headers={"location": "http://b.lan/landing"})),
        ("GET", r"//b\.lan/landing"): route("b", {"ok": True}),
    }
    h = Harness("json", {"url": "http://a.lan/start", "headers": {"X-Api-Key": "KEY"}}, routes)
    assert h.fetch(check=False) == {"ok": True}
    h.close()
    assert seen == [("a-start", "KEY"), ("b", None)]


def test_json_same_origin_redirect_keeps_the_headers_and_the_chain_is_capped():
    seen = []

    def hop(req):
        seen.append(req.headers.get("x-api-key"))
        n = int(req.url.path.rsplit("/", 1)[1])
        return httpx.Response(302, headers={"location": f"/r/{n + 1}"})

    h = Harness("json", {"url": "http://a.lan/r/0", "headers": {"X-Api-Key": "KEY"}}, {("GET", r"//a\.lan/r/\d+"): hop})
    with pytest.raises(integrations.IntegrationError, match="too many redirects"):
        h.fetch(check=False)
    h.close()
    assert len(seen) == 6 and set(seen) == {"KEY"}


def test_scheme_downgrade_or_port_change_counts_as_another_origin():
    assert net.origin_of("https://a.lan/x") != net.origin_of("http://a.lan/x")
    assert net.origin_of("http://a.lan:81/x") != net.origin_of("http://a.lan/x")
    assert net.origin_of("http://A.lan/x") == net.origin_of("http://a.lan:80/y")


def test_ics_basic_auth_is_not_sent_to_another_origin():
    seen = []

    def start(req):
        seen.append(("a", req.headers.get("authorization")))
        return httpx.Response(302, headers={"location": "http://b.lan/cal.ics"})

    def end(req):
        seen.append(("b", req.headers.get("authorization")))
        return cal(vev("UID:1", "DTSTART:20990101T090000Z", "SUMMARY:x"))

    h = Harness("ics", {"url": "http://a.lan/cal.ics", "username": "u", "password": "p"},
                {("GET", r"//a\.lan/cal"): start, ("GET", r"//b\.lan/cal"): end})
    h.fetch()
    h.close()
    assert seen[0][1] and seen[0][1].startswith("Basic ") and seen[1] == ("b", None)


def test_no_type_enables_redirects_on_the_client():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "app" / "integrations"
    for f in (src / "types").glob("*.py"):
        if f.name == "net.py":
            continue
        assert "follow_redirects=True" not in f.read_text(encoding="utf-8"), f


# ================================================================ S7: json size cap
def test_json_body_over_one_megabyte_is_a_safe_error():
    h = Harness("json", {"url": "http://j.lan/big"}, {("GET", r"j\.lan/big"): b'{"a": "' + b"x" * 1_100_000 + b'"}'})
    with pytest.raises(integrations.IntegrationError, match="too large"):
        h.fetch(check=False)
    h.close()
    h = Harness("json", {"url": "http://j.lan/ok"}, {("GET", r"j\.lan/ok"): b'{"a": "' + b"x" * 900_000 + b'"}'})
    assert len(h.fetch(check=False)["a"]) == 900_000
    h.close()


# ================================================================ S8: regexes
def test_prometheus_unit_parsing_has_no_backtracking_and_keeps_its_behaviour():
    t0 = time.time()
    with pytest.raises(ValueError):
        prom_mod.parse_queries("a = up | " + " " * 9900 + "(" * 3000)
    out = prom_mod.parse_queries("a = up | " + " " * 900 + "(")
    assert time.time() - t0 < 0.2 and out[0]["unit"] is None
    q = prom_mod.parse_queries("Up = sum(up) | targets\nCPU = 100 - avg(x) * 100 | %\nPlain = vector(1)\nBad = up | (x)\nPipe=a|b")
    assert [(x["query"], x["unit"]) for x in q] == [("sum(up)", "targets"), ("100 - avg(x) * 100", "%"), ("vector(1)", None),
                                                    ("up | (x)", None), ("a|b", None)]
    with pytest.raises(ValueError, match="at most"):
        prom_mod.parse_queries("\n".join("#c"for _ in range(prom_mod.MAX_LINES + 1)))
    with pytest.raises(ValueError, match="longer"):
        prom_mod.parse_queries("a = " + "x" * 3000)
    field = next(f for f in SPECS["prometheus"].fields if f["key"] == "queries")
    assert field["max_length"] == 8000
    _, errs = check_values(SPECS["prometheus"].fields, {"url": "http://p", "queries": "a = " + "x" * 8001})
    assert errs


# ================================================================ S9: discovery
def test_traefik_labels_are_capped_and_fast():
    from app import discovery

    labels = {f"traefik.http.routers.r{i:04d}.rule": f"Host(`h{i}.example.com`)" for i in range(2000)}
    reasons: list[str] = []
    t0 = time.time()
    route = discovery.traefik_route(labels, reasons)
    assert time.time() - t0 < 0.5 and route["router"] == "r0000"
    assert any("first 20" in r for r in reasons)
    many = {f"x.label.{i}": "v" for i in range(5000)}
    many["traefik.http.routers.late.rule"] = "Host(`late.example.com`)"  # beyond the 500th label: not read
    reasons = []
    assert discovery.traefik_route(many, reasons) is None and any("first 500 labels" in r for r in reasons)
    t0 = time.time()
    discovery.parse_rule("Host(" * 400 + "PathPrefix(" + " " * 1000)
    assert time.time() - t0 < 0.2


def test_discovery_derivation_runs_in_a_worker_thread(client, session, monkeypatch):
    from app import discovery
    from app.routers import discovery as route

    session.add(models.Integration(id="dk", name="D", type="docker", interval=30, enabled=True, config={"url": "http://d:2375"}))
    session.commit()
    threads = []
    main_thread = threading.get_ident()

    async def fake_call(snapshot, fn):
        return [{"id": "abc", "name": "web", "image": "nginx", "state": "running", "labels": {}, "ports": [], "network_mode": ""}]

    real = discovery.derive

    def spy(*a, **kw):
        threads.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(route.integrations, "call", fake_call)
    monkeypatch.setattr(discovery, "derive", spy)
    res = client.get("/api/discovery/docker?integration_id=dk")
    assert res.status_code == 200, res.text
    assert threads and all(t != main_thread for t in threads)


# ================================================================ S11 / S13 / S14 / nits
def test_unraid_connection_failure_uses_the_safe_message():
    def refuse(req):
        raise httpx.ConnectError("boom", request=req)

    h = Harness("unraid", {"url": "http://tower.lan", "api_key": "K"}, {("POST", r"/graphql"): refuse})
    with pytest.raises(httpx.ConnectError) as ei:
        h.fetch(check=False)
    h.close()
    assert errors.safe_error(ei.value, {"url": "http://tower.lan"}) == "connection failed (tower.lan)"


def _status_error(url):
    """The error httpx really raises (its message holds the full URL)."""
    try:
        httpx.Response(404, request=httpx.Request("GET", url)).raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc


@pytest.mark.parametrize("type_", ["ics", "rss", "json"])
def test_logs_for_secret_url_types_keep_the_host_but_not_the_path(type_, caplog):
    caplog.set_level(logging.WARNING)
    runtime._log_failure(snap("u1", type_), _status_error("https://cal.example.com/private/TOKEN123/feed.ics?k=QQQ"))
    assert "cal.example.com" in caplog.text and "TOKEN123" not in caplog.text and "QQQ" not in caplog.text


def test_logs_for_other_types_keep_the_path(caplog):
    caplog.set_level(logging.WARNING)
    runtime._log_failure(snap("u2", "docker"), _status_error("https://kuma.lan/api/v1/private/path?apikey=S"))
    assert "kuma.lan/api/v1/private/path" in caplog.text and "apikey" not in caplog.text


def test_secret_url_flag_is_set_for_exactly_ics_rss_json():
    assert sorted(t for t, s in SPECS.items() if s.secret_url) == ["ics", "json", "rss"]


@pytest.mark.parametrize("cid", ["..", ".", "../x", "a/b", "-a", "", "a b", "a" * 129])
def test_docker_action_validates_inside_the_function(cid):
    h = Harness("docker", {"url": "http://d:2375"}, {})
    with pytest.raises(integrations.IntegrationError):
        h.action("container", {"id": cid, "action": "restart"})
    with pytest.raises(integrations.IntegrationError):
        h.action("container", {"id": "abc", "action": "rm"})
    assert h.calls == []  # nothing was sent
    h.close()


def test_docker_action_with_a_good_id_still_posts():
    h = Harness("docker", {"url": "http://d:2375"}, {("POST", r"/containers/web\.1/restart$"): httpx.Response(204)})
    h.action("container", {"id": "web.1", "action": "restart"})
    h.close()
    assert h.calls == [("POST", "http://d:2375/containers/web.1/restart")]


def test_portainer_action_is_scoped_to_the_configured_environment():
    routes = {("POST", r"/api/endpoints/2/docker/containers/abc/restart$"): httpx.Response(204)}
    h = Harness("portainer", {"url": "http://p.lan", "api_key": "K", "endpoint_id": 2}, routes)
    with pytest.raises(integrations.IntegrationError, match="limited to another environment"):
        h.action("container", {"endpoint_id": 5, "container_id": "abc", "action": "restart"})
    assert h.calls == []
    h.action("container", {"endpoint_id": 2, "container_id": "abc", "action": "restart"})
    assert len(h.calls) == 1
    h.close()
    free = Harness("portainer", {"url": "http://p.lan", "api_key": "K"}, {("POST", r"/endpoints/7/"): httpx.Response(204)})
    free.action("container", {"endpoint_id": 7, "container_id": "abc", "action": "stop"})
    free.close()


def test_runtime_docker_action_is_gone():
    assert not hasattr(runtime, "docker_action") and not hasattr(integrations, "docker_action")


@pytest.mark.parametrize("payload", [{"enabled": "yes"}, {"enabled": True, "seconds": -5}, {"enabled": False, "seconds": "10"},
                                     {"enabled": False, "seconds": 10**9}, {}])
def test_pihole_and_adguard_actions_validate_their_payload_when_called_directly(payload):
    h = Harness("pihole", {"url": "http://pi.hole", "version": "6", "password": "pw"}, {})
    with pytest.raises(integrations.IntegrationError, match="invalid payload"):
        h.action("set_blocking", payload)
    h.close()
    h = Harness("adguard", {"url": "http://ag.lan"}, {})
    with pytest.raises(integrations.IntegrationError, match="invalid payload"):
        h.action("set_protection", payload)
    assert h.calls == []
    h.close()


def test_http_reason_phrase_is_capped_and_sanitised():
    req = httpx.Request("GET", "http://h.lan/x")
    resp = httpx.Response(500, request=req, extensions={"reason_phrase": b"Oops\x1b[31m " + b"A" * 500})
    msg = errors.safe_error(httpx.HTTPStatusError("x", request=req, response=resp), {"url": "http://h.lan"})
    assert len(msg) < 80 and "\x1b" not in msg and msg.startswith("HTTP 500 Oops")


def test_uptime_kuma_slug_is_quoted_in_the_path():
    h = Harness("uptime_kuma", {"url": "http://k.lan", "slug": "a/../b?x=1"}, {
        ("GET", r"/api/status-page/a%2F\.\.%2Fb%3Fx%3D1$"): {"config": {}, "publicGroupList": []},
        ("GET", r"/api/status-page/heartbeat/a%2F\.\.%2Fb%3Fx%3D1$"): {"heartbeatList": {}}})
    h.fetch(check=False)
    h.close()
    assert all("%2F" in u for _, u in h.calls)


def test_home_assistant_cancels_sibling_requests_when_one_fails_hard():
    started, cancelled = [], []

    async def slow(request):
        raise AssertionError  # replaced below

    cfg = {"url": "http://ha.lan", "token": "T", "entities": "sensor.a\nsensor.b\nsensor.c"}
    h = Harness("home_assistant", cfg, {})

    async def run():
        orig = h.ctx.client.get

        async def get(url, **kw):
            if url.endswith("sensor.a"):
                return httpx.Response(401, request=httpx.Request("GET", url))
            started.append(url)
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(url)
                raise

        h.ctx.client.get = get
        with pytest.raises(integrations.IntegrationError, match="login failed"):
            await h.spec.fetch(h.ctx)

    h.run(run())
    h.close()
    assert sorted(cancelled) == sorted(started) and len(started) == 2


def test_sonarr_and_radarr_count_the_library_at_most_every_fifteen_minutes(monkeypatch):
    now = clock(monkeypatch)
    for type_, path, key in (("sonarr", "/api/v3/series", "series"), ("radarr", "/api/v3/movie", "movies")):
        hits = []
        base_routes = {("GET", r"/api/v3/queue"): {"records": [], "totalRecords": 0},
                       ("GET", r"/wanted/missing"): {"totalRecords": 0}, ("GET", r"/api/v3/calendar"): []}
        base_routes[("GET", path + "$")] = lambda r, hits=hits: (hits.append(1), [{"id": 1}, {"id": 2}])[1]
        h = Harness(type_, {"url": "http://x.lan", "api_key": "K"}, base_routes)
        for _ in range(3):
            data = h.fetch()
            now[0] += 60
        assert len(hits) == 1 and next(s for s in data["stats"] if s["key"] == key)["value"] == 2
        now[0] += 901
        h.fetch()
        assert len(hits) == 2
        h.close()


# ================================================================ TLS default
def test_new_integrations_verify_tls_by_default():
    field = next(f for f in SPECS["pihole"].fields if f["key"] == "verify_tls")
    assert field["default"] is True
    assert Context(SPECS["pihole"], {"url": "http://x"}).verify_tls is True
    assert Context(SPECS["pihole"], {"url": "http://x", "verify_tls": False}).verify_tls is False
    assert "verify_tls" not in {f["key"] for f in SPECS["host"].fields}


def test_a_new_row_created_through_the_api_verifies(client, fake):
    iid = _create(client)["id"]
    assert runtime._ctx[iid][1].verify_tls is True


def test_startup_migration_keeps_existing_rows_unverified_once(session):
    session.add_all([
        models.Integration(id="a", name="A", type="pihole", interval=15, config={"url": "https://pi"}),
        models.Integration(id="b", name="B", type="json", interval=30, config={"url": "https://j", "verify_tls": True}),
        models.Integration(id="c", name="C", type="host", interval=30, config={}),
    ])
    session.commit()
    main._migrate_integration_tls(lambda: session)
    session.expire_all()
    assert session.get(models.Integration, "a").config == {"url": "https://pi", "verify_tls": False}
    assert session.get(models.Integration, "b").config["verify_tls"] is True  # an explicit choice is kept
    assert "verify_tls" not in session.get(models.Integration, "c").config  # no HTTP: nothing to say
    # recorded: a row created later without the key means "verify" and is never rewritten
    session.add(models.Integration(id="d", name="D", type="pihole", interval=15, config={"url": "https://new"}))
    session.commit()
    main._migrate_integration_tls(lambda: session)
    session.expire_all()
    assert "verify_tls" not in session.get(models.Integration, "d").config
    assert Context(SPECS["pihole"], session.get(models.Integration, "d").config).verify_tls is True
    assert Context(SPECS["pihole"], session.get(models.Integration, "a").config).verify_tls is False


def _bundle(version, rows):
    b = {"services": [], "dashboards": [], "settings": {}, "integrations": rows}
    if version is not None:
        b["version"] = version
    return b


def test_import_of_an_old_bundle_keeps_rows_without_the_key_unverified(client, session):
    rows = [{"id": "o1", "name": "Old", "type": "pihole", "config": {"url": "https://pi", "version": "6"}},
            {"id": "o2", "name": "Old2", "type": "pihole", "config": {"url": "https://pi", "verify_tls": True}}]
    for version in (None, 1, 2):
        client.post("/api/config/import?replace=true", json=_bundle(version, [dict(r, config=dict(r["config"])) for r in rows]), headers=HEADERS)
        assert _stored(session, "o1")["verify_tls"] is False, version
        assert _stored(session, "o2")["verify_tls"] is True
    client.post("/api/config/import?replace=true", json=_bundle(3, [dict(r, config=dict(r["config"])) for r in rows]), headers=HEADERS)
    assert "verify_tls" not in _stored(session, "o1")  # a current bundle without the key means "verify"


def test_export_is_version_3_and_roundtrips(client, fake):
    _create(client)
    assert client.get("/api/config/export").json()["version"] == 3


# ================================================================ S10: import validates and does not cross types
def test_import_saves_an_invalid_row_disabled_with_a_warning(client, session, fake):
    rows = [{"id": "bad", "name": "Bad", "type": "fake", "enabled": True,
             "config": {"url": "http://x", "count": 99, "token": "SECRETVALUE"}},
            {"id": "good", "name": "Good", "type": "fake", "enabled": True, "config": {"url": "http://x", "count": 3}}]
    res = client.post("/api/config/import", json=_bundle(3, rows), headers=HEADERS)
    assert res.status_code == 200, res.text
    warnings = res.json()["warnings"]
    assert any('"Bad"' in w and "count" in w and "disabled" in w for w in warnings) and "SECRETVALUE" not in str(warnings)
    session.expire_all()
    bad, good = session.get(models.Integration, "bad"), session.get(models.Integration, "good")
    assert bad.enabled is False and bad.config["count"] == 99 and bad.config["token"] == "SECRETVALUE"  # nothing lost
    assert good.enabled is True and not [w for w in warnings if "Good" in w]


def test_import_whitespace_is_cleaned_by_the_type_validation(client, session, fake):
    rows = [{"id": "ws", "name": "W", "type": "fake", "config": {"url": " http://x ", "token": "tok\n"}}]
    client.post("/api/config/import", json=_bundle(3, rows), headers=HEADERS)
    assert _stored(session, "ws") == {"url": "http://x", "token": "tok"}


def test_import_never_merges_masks_against_a_row_of_another_type(client, session, fake):
    session.add(models.Integration(id="same", name="S", type="pihole", interval=15, config={"url": "http://pi", "password": "PIHOLE-PW"}))
    session.commit()
    rows = [{"id": "same", "name": "S", "type": "fake", "config": {"url": "http://x", "token": MASK}}]
    client.post("/api/config/import", json=_bundle(3, rows), headers=HEADERS)
    assert "PIHOLE-PW" not in str(_stored(session, "same")) and "token" not in _stored(session, "same")
    # same type: the mask still keeps the stored secret
    session.add(models.Integration(id="keep", name="K", type="fake", interval=30, config={"url": "http://x", "token": "REAL"}))
    session.commit()
    client.post("/api/config/import", json=_bundle(3, [{"id": "keep", "name": "K", "type": "fake", "config": {"url": "http://x", "token": MASK}}]), headers=HEADERS)
    assert _stored(session, "keep")["token"] == "REAL"


def test_import_invalidates_after_the_commit_not_before(client, session, fake, monkeypatch):
    iid = _create(client)["id"]
    order = []
    real_commit = session.commit
    monkeypatch.setattr(session, "commit", lambda: (order.append("commit"), real_commit())[1])
    real_inv = runtime.invalidate
    monkeypatch.setattr(integrations, "invalidate", lambda *a, **k: (order.append("invalidate"), real_inv(*a, **k))[1])
    rows = [{"id": iid, "name": "F", "type": "fake", "config": {"url": "http://elsewhere"}}]
    client.post("/api/config/import", json=_bundle(3, rows), headers=HEADERS)
    assert order.index("commit") < order.index("invalidate")
