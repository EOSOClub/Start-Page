"""Integration framework: registry, secrets, safe errors, scheduling, validation, /test, actions.

Types are exercised through a registered fake type (no network); the real types are covered by
test_integration_types.py."""
import asyncio
import json
import logging
import sys
import textwrap
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import integrations, models
from app.integrations import base, runtime
from app.integrations.base import Action, Spec, stat
from app.integrations.registry import SPECS, discover
from app.main import app

from conftest import HEADERS

MASK = "********"
PW = "correct horse battery"


# ---------------------------------------------------------------- fixtures
@pytest.fixture(name="fake")
def fake_fixture(monkeypatch):
    """A registered type "fake" whose fetch and actions the test controls. Adds `.calls`, `.fetch`, `.clients`."""
    st = SimpleNamespace(calls=0, active=0, max_active=0, clients=[])

    async def default_fetch(ctx):
        ctx.client  # a real fetch uses the shared client: create it
        return {"stats": [stat("token", "Token", ctx.cfg.get("token")), stat("note", "Note", ctx.cfg.get("note"))],
                "items": [{"title": "One", "status": "online", "progress": 50}]}

    st.fetch = default_fetch

    async def fetch(ctx):
        st.calls += 1
        st.active += 1
        st.max_active = max(st.max_active, st.active)
        try:
            return await st.fetch(ctx)
        finally:
            st.active -= 1

    async def echo(ctx, payload):
        st.echoed = payload

    async def boom(ctx, payload):
        raise RuntimeError("POST https://u:pw@up.stream:9000/secret/path?apikey=SECRET failed")

    async def http_fail(ctx, payload):
        request = httpx.Request("POST", "http://up.stream:9000/x?apikey=SECRET")
        raise httpx.HTTPStatusError("bad", request=request, response=httpx.Response(500, request=request))

    def factory(ctx):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        st.clients.append(client)
        return client

    spec = Spec(
        type="fake", label="Fake", description="A test type.", fetch=fetch, provides=("stats", "items"),
        client_factory=factory,
        fields=[
            {"key": "url", "label": "URL", "type": "url", "required": True},
            {"key": "token", "label": "Token", "type": "secret"},
            {"key": "note", "label": "Note", "type": "text"},
            {"key": "count", "label": "Count", "type": "number", "min": 1, "max": 10},
            {"key": "mode", "label": "Mode", "type": "select", "default": "a",
             "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
        ],
        actions={
            "echo": Action(echo, "Echo", payload=[{"key": "n", "label": "N", "type": "number", "required": True, "min": 0}]),
            "boom": Action(boom, "Boom"),
            "http_fail": Action(http_fail, "HTTP fail"),
        },
    )
    monkeypatch.setitem(SPECS, "fake", spec)
    return st


def _create(client, **over):
    body = {"name": "F", "type": "fake", "config": {"url": "http://up.stream:9000", "token": "tok-123"}, **over}
    res = client.post("/api/integrations", json=body, headers=HEADERS)
    assert res.status_code == 201, res.text
    return res.json()


def _stored(session, integration_id):
    session.expire_all()
    return session.get(models.Integration, integration_id).config


def _guest_client(client) -> TestClient:
    assert client.post("/api/auth/password", json={"new": PW}, headers=HEADERS).status_code == 200
    assert client.patch("/api/auth/settings", json={"guest_access": "read"}, headers=HEADERS).status_code == 200
    return TestClient(app)


# ---------------------------------------------------------------- registry
def test_every_type_module_is_discovered():
    import pkgutil
    from app.integrations import types as pkg

    modules = {m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_")}
    assert len(SPECS) == len(modules) >= 7
    assert {"docker", "uptime_kuma", "json", "host", "rss", "pihole", "prometheus"} <= set(SPECS)
    assert integrations.TYPES == sorted(SPECS)


def _package(tmp_path, monkeypatch, name, modules: dict):
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text("")
    for mod, body in modules.items():
        (root / f"{mod}.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        return __import__(name)
    finally:
        pass


_MODULE = """
    from app.integrations.base import Spec
    async def fetch(ctx): return {{}}
    SPEC = Spec(type="{type}", label="L", description="d", fetch=fetch)
"""


def test_duplicate_type_keys_fail_at_import(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "dup_pkg", {"a": _MODULE.format(type="same"), "b": _MODULE.format(type="same")})
    try:
        with pytest.raises(RuntimeError, match="defined twice"):
            discover(pkg)
    finally:
        for m in [m for m in sys.modules if m.startswith("dup_pkg")]:
            del sys.modules[m]


def test_a_module_without_a_spec_fails_loudly(tmp_path, monkeypatch):
    pkg = _package(tmp_path, monkeypatch, "nospec_pkg", {"a": "X = 1\n"})
    try:
        with pytest.raises(RuntimeError, match="must define SPEC"):
            discover(pkg)
    finally:
        for m in [m for m in sys.modules if m.startswith("nospec_pkg")]:
            del sys.modules[m]


@pytest.mark.parametrize("kwargs,match", [
    (dict(type="Bad-Key"), "must match"),
    (dict(fields=[{"key": "a", "label": "A", "type": "nope"}]), "unknown field type"),
    (dict(fields=[{"key": "a", "label": "A", "type": "text"}, {"key": "a", "label": "B", "type": "text"}]), "duplicated"),
    (dict(fields=[{"key": "a", "label": "A", "type": "number", "secret": True}]), "can be secret"),
    (dict(provides=("stats", "logs")), "provides"),
    (dict(default_interval=1), "default_interval"),
])
def test_a_bad_spec_fails_at_construction(kwargs, match):
    async def fetch(ctx):
        return {}

    args = dict(type="x", label="X", description="one line", fetch=fetch) | kwargs
    with pytest.raises(ValueError, match=match):
        Spec(**args)


def _no_callables(value):
    if callable(value):
        return False
    if isinstance(value, dict):
        return all(_no_callables(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_no_callables(v) for v in value)
    return True


def test_types_route_matches_the_registry_and_has_no_callables(client):
    res = client.get("/api/integrations/types")
    assert res.status_code == 200
    body = res.json()
    assert [t["type"] for t in body] == sorted(SPECS)
    assert body == json.loads(json.dumps([SPECS[t].public() for t in sorted(SPECS)]))
    assert _no_callables(body)
    pihole = next(t for t in body if t["type"] == "pihole")
    assert pihole["provides"] == ["stats"] and pihole["actions"][0]["name"] == "set_blocking"
    assert next(f for f in pihole["fields"] if f["key"] == "password") == {
        "key": "password", "label": "Password (v6)", "type": "secret", "secret": True, "mask": MASK,
        "help": "Pi-hole v6: the web interface password or an app password. Leave empty if none is set.",
    }
    assert any(f["key"] == "verify_tls" and f["default"] is True for f in pihole["fields"])  # verify by default
    assert not any(f["key"] == "verify_tls" for f in next(t for t in body if t["type"] == "host")["fields"])


# ---------------------------------------------------------------- secrets
def test_secret_round_trip(client, session, fake):
    created = _create(client)
    assert created["config"]["token"] == MASK and created["config"]["url"] == "http://up.stream:9000"
    assert _stored(session, created["id"])["token"] == "tok-123"  # stored for real
    assert client.get("/api/integrations").json()[0]["config"]["token"] == MASK

    iid = created["id"]
    # Mask or an omitted key keeps the stored value.
    for config in ({"url": "http://up.stream:9000", "token": MASK}, {"url": "http://up.stream:9000"}):
        res = client.patch(f"/api/integrations/{iid}", json={"config": config}, headers=HEADERS)
        assert res.status_code == 200 and res.json()["config"]["token"] == MASK
        assert _stored(session, iid)["token"] == "tok-123"
    # A new value replaces it; "" clears it (and the API then shows "").
    client.patch(f"/api/integrations/{iid}", json={"config": {"url": "http://up.stream:9000", "token": "new"}}, headers=HEADERS)
    assert _stored(session, iid)["token"] == "new"
    res = client.patch(f"/api/integrations/{iid}", json={"config": {"url": "http://up.stream:9000", "token": ""}}, headers=HEADERS)
    assert res.json()["config"]["token"] == "" and "token" not in _stored(session, iid)
    assert _create(client, name="G", config={"url": "http://x"})["config"]["token"] == ""  # never set


def test_the_fetch_sees_the_real_secret_not_the_mask(client, fake):
    iid = _create(client)["id"]
    client.patch(f"/api/integrations/{iid}", json={"config": {"url": "http://up.stream:9000", "token": MASK}}, headers=HEADERS)
    data = client.get(f"/api/integrations/{iid}/data").json()
    assert data["ok"] and data["data"]["stats"][0]["value"] == "tok-123"


def test_json_headers_are_secret_as_a_whole(client, session, monkeypatch):
    monkeypatch.setattr(SPECS["json"], "client_factory",
                        lambda ctx: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"h": r.headers.get("x-key")}))))
    created = client.post("/api/integrations", headers=HEADERS,
                          json={"name": "J", "type": "json", "config": {"url": "http://j/x", "headers": {"X-Key": "abc"}}}).json()
    assert created["config"]["headers"] == MASK
    assert client.get(f"/api/integrations/{created['id']}/data").json()["data"] == {"h": "abc"}
    assert _stored(session, created["id"])["headers"] == {"X-Key": "abc"}
    res = client.patch(f"/api/integrations/{created['id']}", json={"config": {"url": "http://j/x", "headers": MASK}}, headers=HEADERS)
    assert _stored(session, created["id"])["headers"] == {"X-Key": "abc"} and res.json()["config"]["headers"] == MASK
    res = client.patch(f"/api/integrations/{created['id']}", json={"config": {"url": "http://j/x", "headers": ""}}, headers=HEADERS)
    assert res.json()["config"]["headers"] == ""


def test_export_keeps_real_values_and_import_never_stores_the_mask(client, session, fake):
    iid = _create(client)["id"]
    exported = client.get("/api/config/export").json()
    assert exported["integrations"][0]["config"]["token"] == "tok-123"

    def bundle(config):
        return {"services": [], "dashboards": [], "settings": {},
                "integrations": [{"id": iid, "name": "F", "type": "fake", "config": config}]}

    # The mask in a bundle keeps the stored secret ...
    assert client.post("/api/config/import", json=bundle({"url": "http://up.stream:9000", "token": MASK}), headers=HEADERS).status_code == 200
    assert _stored(session, iid)["token"] == "tok-123"
    # ... a real value replaces it ...
    client.post("/api/config/import", json=bundle({"url": "http://up.stream:9000", "token": "swapped"}), headers=HEADERS)
    assert _stored(session, iid)["token"] == "swapped"
    # ... and for an integration that does not exist yet the mask is dropped, not stored.
    other = {"version": 3, "services": [], "dashboards": [], "settings": {},  # version 3: a missing verify_tls means "verify"
             "integrations": [{"id": "new1", "name": "N", "type": "fake", "config": {"url": "http://n", "token": MASK}}]}
    client.post("/api/config/import", json=other, headers=HEADERS)
    assert _stored(session, "new1") == {"url": "http://n"}
    assert MASK not in json.dumps(client.get("/api/config/export").json())


def test_import_of_an_edited_integration_resets_its_client_and_state(client, fake):
    iid = _create(client)["id"]
    first = runtime._ctx[iid][1]
    same = {"version": 3, "services": [], "dashboards": [], "settings": {},
            "integrations": [{"id": iid, "name": "F", "type": "fake", "config": {"url": "http://up.stream:9000", "token": "tok-123"}}]}
    client.post("/api/config/import", json=same, headers=HEADERS)
    assert runtime._ctx[iid][1] is first  # unchanged: kept
    same["integrations"][0]["config"]["url"] = "http://elsewhere"
    client.post("/api/config/import", json=same, headers=HEADERS)
    assert iid not in runtime._ctx and iid not in runtime._next  # edited: due again on the next tick


def test_guests_get_no_config_and_a_generic_error(client, fake):
    iid = _create(client)["id"]
    guest = _guest_client(client)
    assert guest.get("/api/integrations").json()[0]["config"] == {}
    assert client.get("/api/integrations").json()[0]["config"]["token"] == MASK

    async def failing(ctx):
        raise httpx.ConnectError("refused")

    fake.fetch = failing
    assert client.post(f"/api/integrations/{iid}/refresh", headers=HEADERS).json()["error"] == "connection failed (up.stream:9000)"
    assert client.get("/api/integrations/data").json()[iid]["error"] == "connection failed (up.stream:9000)"
    assert guest.get("/api/integrations/data").json()[iid]["error"] == "Unavailable"
    assert guest.get(f"/api/integrations/{iid}/data").json()["error"] == "Unavailable"


# ---------------------------------------------------------------- errors
def test_errors_never_expose_urls_credentials_or_paths(client, fake, caplog):
    caplog.set_level(logging.DEBUG)

    async def failing(ctx):
        raise RuntimeError("GET https://user:hunter2@kuma.lan:3001/api/v1/private/path?apikey=SECRET&x=1 failed")

    fake.fetch = failing
    iid = _create(client)["id"]
    guest = _guest_client(client)
    seen = [
        client.get("/api/integrations/data").text,
        client.get(f"/api/integrations/{iid}/data").text,
        client.post(f"/api/integrations/{iid}/refresh", headers=HEADERS).text,
        guest.get("/api/integrations/data").text,
    ]
    for text in seen:
        for secret in ("SECRET", "hunter2", "private/path", "apikey"):
            assert secret not in text, (secret, text)
    assert client.get(f"/api/integrations/{iid}/data").json()["error"] == "unexpected error (RuntimeError)"
    # The log has the exception with the query string and credentials removed (the path stays).
    assert "kuma.lan:3001/api/v1/private/path" in caplog.text
    for secret in ("SECRET", "hunter2", "apikey"):
        assert secret not in caplog.text


@pytest.mark.parametrize("exc,expected", [
    (httpx.ReadTimeout("t"), "timeout (h.lan:81)"),
    (TimeoutError(), "timeout (h.lan:81)"),
    (ValueError("bad json at https://h.lan/x?k=SECRET"), "invalid response (h.lan:81)"),
    (KeyError("Id"), "invalid response (h.lan:81)"),
    (ImportError("psutil"), "a required Python package is missing"),
    (base.IntegrationError("login failed"), "login failed"),
])
def test_safe_error_vocabulary(exc, expected):
    assert integrations.safe_error(exc, {"url": "https://user:pw@h.lan:81/p?k=SECRET"}) == expected


def test_safe_error_names_http_status_connection_and_tls_problems():
    request = httpx.Request("GET", "http://user:pw@h.lan:81/p/q?apikey=SECRET")
    status = httpx.HTTPStatusError("m", request=request, response=httpx.Response(401, request=request))
    assert integrations.safe_error(status) == "HTTP 401 Unauthorized (h.lan:81)"

    def raised(inner, outer=httpx.ConnectError("x", request=request)):
        outer.__cause__ = inner
        return outer

    assert integrations.safe_error(raised(ConnectionRefusedError())) == "connection refused (h.lan:81)"
    import ssl
    assert integrations.safe_error(raised(ssl.SSLCertVerificationError())) == (
        'TLS certificate not trusted (h.lan:81): paste its CA under "Trusted CA certificate" '
        'or turn off "Verify TLS certificate"')
    assert integrations.safe_error(raised(ssl.SSLError())) == "TLS error (h.lan:81)"
    import socket
    assert integrations.safe_error(raised(socket.gaierror())) == "DNS lookup failed (h.lan:81)"
    assert integrations.safe_error(httpx.ConnectError("x", request=request)) == "connection failed (h.lan:81)"


def test_redact_strips_query_and_userinfo():
    from app.integrations.errors import redact

    assert redact("failed GET https://u:p@h.lan:81/a/b?k=SECRET#frag now") == "failed GET https://h.lan:81/a/b now"


# ---------------------------------------------------------------- scheduling
class _Sessions:
    """Stands in for SessionLocal: hands out the test session without closing it."""

    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


def _row(session, iid, note="", interval=30):
    session.add(models.Integration(id=iid, name=iid, type="fake", interval=interval, config={"url": "http://x", "note": note}))
    session.commit()


def test_a_slow_integration_does_not_delay_another(session, fake, monkeypatch):
    monkeypatch.setattr(runtime, "SessionLocal", _Sessions(session))
    _row(session, "slow", "slow")
    _row(session, "fast", "fast")

    async def main():
        gate = asyncio.Event()

        async def behave(ctx):
            if ctx.cfg["note"] == "slow":
                await gate.wait()
            return {"stats": [stat("n", "N", ctx.cfg["note"])]}

        fake.fetch = behave
        await runtime._tick()  # returns at once: polls run as their own tasks
        assert "slow" not in runtime._data
        for _ in range(200):  # the fast one finishes while the slow one is still blocked
            if "fast" in runtime._data:
                break
            await asyncio.sleep(0.01)
        assert runtime._data["fast"]["ok"] and "slow" not in runtime._data
        await runtime._tick()  # the slow one is still running: not started a second time
        assert fake.calls == 2
        gate.set()
        await asyncio.gather(*runtime._tasks)
        assert runtime._data["slow"]["data"]["stats"][0]["value"] == "slow"
        assert fake.max_active == 2  # concurrent across integrations ...

    asyncio.run(main())


def test_one_integration_is_never_fetched_twice_at_once(fake):
    snap = SimpleNamespace(id="i1", name="i1", type="fake", interval=30, enabled=True, config={"url": "http://x"})

    async def slowish(ctx):
        await asyncio.sleep(0.02)
        return {"stats": [stat("n", "N", 1)]}

    fake.fetch = slowish

    async def main():
        return await asyncio.gather(runtime.fetch(snap), runtime.fetch(snap), runtime.refresh_row(snap))

    results = asyncio.run(main())
    assert all(r["ok"] for r in results) and fake.calls == 3
    assert fake.max_active == 1  # ... but serial within one integration


def test_a_fetch_that_times_out_is_a_safe_error_and_frees_the_integration(fake, monkeypatch):
    monkeypatch.setattr(runtime, "FETCH_TIMEOUT", 0.05)
    snap = SimpleNamespace(id="i1", name="i1", type="fake", interval=30, enabled=True, config={"url": "http://up.stream:9000"})

    async def hang(ctx):
        await asyncio.sleep(30)

    fake.fetch = hang

    async def main():
        first = await runtime.fetch(snap)

        async def fine(ctx):
            return {"stats": [stat("n", "N", 1)]}

        fake.fetch = fine
        return first, await runtime.fetch(snap)

    first, second = asyncio.run(main())
    assert first["ok"] is False and first["error"] == "timeout (up.stream:9000)"
    assert second["ok"] is True


def test_a_failed_fetch_keeps_the_last_good_data(fake):
    snap = SimpleNamespace(id="i1", name="i1", type="fake", interval=30, enabled=True, config={"url": "http://x"})

    async def main():
        good = await runtime.refresh_row(snap)

        async def fail(ctx):
            raise httpx.ConnectError("x")

        fake.fetch = fail
        return good, await runtime.refresh_row(snap)

    good, bad = asyncio.run(main())
    assert bad["ok"] is False and bad["data"] == good["data"]


def test_deleted_and_disabled_integrations_are_dropped_by_the_scheduler(session, fake, monkeypatch):
    monkeypatch.setattr(runtime, "SessionLocal", _Sessions(session))
    _row(session, "a")

    async def main():
        await runtime._tick()
        await asyncio.gather(*runtime._tasks)
        assert "a" in runtime._data and "a" in runtime._ctx
        client = runtime._ctx["a"][1].client
        session.query(models.Integration).delete()
        session.commit()
        await runtime._tick()
        assert "a" not in runtime._data and "a" not in runtime._ctx
        assert client.is_closed

    asyncio.run(main())


def test_bad_stored_intervals_are_tolerated_and_clamped():
    ns = lambda i, t="fake": SimpleNamespace(type=t, interval=i)  # noqa: E731
    assert runtime.interval_of(ns(0, "rss")) == 300  # unset / bad: the type's default
    assert runtime.interval_of(ns(-4, "rss")) == 300
    assert runtime.interval_of(ns(1, "rss")) == 5
    assert runtime.interval_of(ns(60, "rss")) == 60


def test_clients_are_shared_per_integration_and_closed_on_edit_and_delete(client, fake):
    iid = _create(client)["id"]
    client.post(f"/api/integrations/{iid}/refresh", headers=HEADERS)
    assert len(fake.clients) == 1  # one client for create + refresh
    client.patch(f"/api/integrations/{iid}", json={"name": "renamed"}, headers=HEADERS)
    assert len(fake.clients) == 1 and not fake.clients[0].is_closed  # a rename keeps it
    client.patch(f"/api/integrations/{iid}", json={"config": {"url": "http://other:1", "token": MASK}}, headers=HEADERS)
    assert fake.clients[0].is_closed and len(fake.clients) == 2
    assert client.delete(f"/api/integrations/{iid}", headers=HEADERS).status_code == 204
    assert fake.clients[1].is_closed and iid not in runtime._data


def test_state_survives_between_polls_but_not_a_config_change(fake):
    snap = SimpleNamespace(id="i1", name="i1", type="fake", interval=30, enabled=True, config={"url": "http://x"})
    seen = []

    async def counting(ctx):
        ctx.state["n"] = ctx.state.get("n", 0) + 1
        seen.append(ctx.state["n"])
        return {"stats": [stat("n", "N", ctx.state["n"])]}

    fake.fetch = counting

    async def main():
        await runtime.fetch(snap)
        await runtime.fetch(snap)
        snap.config = {"url": "http://y"}
        await runtime.fetch(snap)

    asyncio.run(main())
    assert seen == [1, 2, 1]


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("config,field", [
    ({}, "url"),  # required
    ({"url": "ftp://x"}, "url"),
    ({"url": "http://"}, "url"),
    ({"url": "http://x", "count": "three"}, "count"),
    ({"url": "http://x", "count": 99}, "count"),
    ({"url": "http://x", "mode": "z"}, "mode"),
    ({"url": "http://x", "verify_tls": "yes"}, "verify_tls"),
    ({"url": "http://x", "token": 5}, "token"),
    ({"url": "http://x", "bogus": 1}, "bogus"),
])
def test_create_validates_config_per_field(client, fake, config, field):
    res = client.post("/api/integrations", json={"name": "F", "type": "fake", "config": config}, headers=HEADERS)
    assert res.status_code == 422, res.text
    assert field in [d["loc"][-1] for d in res.json()["detail"]]
    assert client.get("/api/integrations").json() == []


def test_validation_accepts_schemeless_urls_and_docker_sockets(client, fake, monkeypatch):
    assert _create(client, config={"url": "up.stream:9000"})["config"]["url"] == "up.stream:9000"
    monkeypatch.setattr(SPECS["docker"], "client_factory", lambda ctx: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    res = client.post("/api/integrations", json={"name": "D", "type": "docker", "config": {"url": "unix:///var/run/docker.sock"}}, headers=HEADERS)
    assert res.status_code == 201
    bad = client.post("/api/integrations", json={"name": "D2", "type": "docker", "config": {"url": "unix://"}}, headers=HEADERS)
    assert bad.status_code == 422


def test_unknown_types_and_missing_integrations(client):
    assert client.post("/api/integrations", json={"name": "x", "type": "nope"}, headers=HEADERS).status_code == 400
    assert client.patch("/api/integrations/missing", json={"name": "x"}, headers=HEADERS).status_code == 404
    assert client.patch("/api/integrations/missing", json={"type": "nope"}, headers=HEADERS).status_code == 404


def test_prometheus_queries_are_validated_across_fields(client):
    res = client.post("/api/integrations", headers=HEADERS,
                      json={"name": "P", "type": "prometheus", "config": {"url": "http://p:9090", "queries": "no equals here"}})
    assert res.status_code == 422 and res.json()["detail"][0]["loc"][-1] == "queries"


def test_interval_rules(client, session, fake):
    for bad in (0, -5):
        assert client.post("/api/integrations", json={"name": "F", "type": "fake", "interval": bad, "config": {"url": "http://x"}}, headers=HEADERS).status_code == 422
    assert _create(client, interval=2)["interval"] == 5  # raised to the minimum
    assert _create(client, name="G")["interval"] == 30  # the type's default
    iid = _create(client, name="H", interval=60)["id"]
    assert client.patch(f"/api/integrations/{iid}", json={"interval": 0}, headers=HEADERS).status_code == 422
    assert client.patch(f"/api/integrations/{iid}", json={"interval": 3}, headers=HEADERS).json()["interval"] == 5
    # Existing rows with a bad value still load, list, import and export.
    session.add(models.Integration(id="old", name="Old", type="fake", interval=0, config={"url": "http://x"}))
    session.commit()
    assert any(i["id"] == "old" and i["interval"] == 0 for i in client.get("/api/integrations").json())
    bundle = {"services": [], "dashboards": [], "settings": {},
              "integrations": [{"id": "old", "name": "Old", "type": "fake", "interval": 0, "config": {"url": "http://x"}}]}
    assert client.post("/api/config/import", json=bundle, headers=HEADERS).status_code == 200


def test_changing_the_type_needs_a_valid_config_for_the_new_type(client, fake):
    iid = _create(client)["id"]
    res = client.patch(f"/api/integrations/{iid}", json={"type": "rss"}, headers=HEADERS)
    assert res.status_code == 422  # the old config is not carried over (its secrets neither)
    assert client.get("/api/integrations").json()[0]["type"] == "fake"


# ---------------------------------------------------------------- /test
def test_test_endpoint_runs_a_fetch_without_saving(client, fake):
    res = client.post("/api/integrations/test", json={"type": "fake", "config": {"url": "http://x", "token": "t0k"}}, headers=HEADERS)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["error"] is None
    assert body["sample"]["stats"][0] == {"key": "token", "label": "Token", "value": "t0k"}
    assert client.get("/api/integrations").json() == []
    assert fake.clients[0].is_closed  # a throwaway client, closed again


def test_test_endpoint_reports_a_failure_safely(client, fake):
    async def failing(ctx):
        raise RuntimeError("https://u:p@h/x?apikey=SECRET")

    fake.fetch = failing
    body = client.post("/api/integrations/test", json={"type": "fake", "config": {"url": "http://x"}}, headers=HEADERS).json()
    assert body == {"ok": False, "error": "unexpected error (RuntimeError)", "sample": None}


def test_test_endpoint_resolves_masked_secrets_from_the_stored_integration(client, fake):
    iid = _create(client)["id"]
    config = {"url": "http://x", "token": MASK}
    body = client.post("/api/integrations/test", json={"type": "fake", "id": iid, "config": config}, headers=HEADERS).json()
    assert body["sample"]["stats"][0]["value"] == "tok-123"
    # Without an id the mask is not a secret: it is dropped, the fetch sees none.
    body = client.post("/api/integrations/test", json={"type": "fake", "config": config}, headers=HEADERS).json()
    assert body["sample"]["stats"][0]["value"] is None
    assert client.post("/api/integrations/test", json={"type": "fake", "id": "nope", "config": config}, headers=HEADERS).status_code == 404


def test_test_endpoint_validates_and_needs_a_writer(client, fake):
    assert client.post("/api/integrations/test", json={"type": "nope", "config": {}}, headers=HEADERS).status_code == 400
    assert client.post("/api/integrations/test", json={"type": "fake", "config": {}}, headers=HEADERS).status_code == 422
    guest = _guest_client(client)
    assert guest.post("/api/integrations/test", json={"type": "fake", "config": {"url": "http://x"}}, headers=HEADERS).status_code == 401
    assert guest.post("/api/integrations/i1/action/echo", json={"n": 1}, headers=HEADERS).status_code == 401


# ---------------------------------------------------------------- actions
def test_action_runs_with_a_validated_payload_and_returns_fresh_data(client, fake):
    iid = _create(client)["id"]
    res = client.post(f"/api/integrations/{iid}/action/echo", json={"n": 3}, headers=HEADERS)
    assert res.status_code == 200 and res.json()["ok"] is True
    assert fake.echoed == {"n": 3}


def test_action_errors(client, fake):
    iid = _create(client)["id"]
    url = f"/api/integrations/{iid}/action"
    assert client.post(f"{url}/nope", json={}, headers=HEADERS).status_code == 404
    assert client.post("/api/integrations/missing/action/echo", json={"n": 1}, headers=HEADERS).status_code == 404
    for payload in ({}, {"n": "x"}, {"n": -1}, {"n": 1, "extra": 1}):
        res = client.post(f"{url}/echo", json=payload, headers=HEADERS)
        assert res.status_code == 422, payload
        assert res.json()["detail"][0]["loc"][0] == "body"
    assert not hasattr(fake, "echoed")
    # Upstream failures: 502 with a safe message, never the URL, path or query.
    for name, expected in (("boom", "unexpected error (RuntimeError)"), ("http_fail", "HTTP 500 Internal Server Error (up.stream:9000)")):
        res = client.post(f"{url}/{name}", json={}, headers=HEADERS)
        assert res.status_code == 502 and res.json()["detail"] == expected
        assert "SECRET" not in res.text and "secret/path" not in res.text


# ---------------------------------------------------------------- docker route
def test_docker_route_still_works(client, monkeypatch):
    posted = []

    def handler(request):
        if request.method == "POST":
            posted.append(request.url.path)
            return httpx.Response(500 if "broken" in request.url.path else 204)
        return httpx.Response(200, json=[] if request.url.path.endswith("json") else {"Name": "nas", "ContainersRunning": 0, "ContainersStopped": 0, "Containers": 0})

    monkeypatch.setattr(SPECS["docker"], "client_factory", lambda ctx: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    iid = client.post("/api/integrations", json={"name": "D", "type": "docker", "config": {"url": "http://dock:2375"}}, headers=HEADERS).json()["id"]
    res = client.post(f"/api/integrations/{iid}/docker/web/restart", headers=HEADERS)
    assert res.status_code == 200 and res.json()["ok"] is True and posted == ["/containers/web/restart"]
    assert client.post(f"/api/integrations/{iid}/docker/web/explode", headers=HEADERS).status_code == 400
    broken = client.post(f"/api/integrations/{iid}/docker/broken/stop", headers=HEADERS)
    assert broken.status_code == 502 and broken.json()["detail"] == "Docker error: HTTP 500 Internal Server Error (dock:2375)"
    # The generic route does the same.
    assert client.post(f"/api/integrations/{iid}/action/container", json={"id": "web", "action": "stop"}, headers=HEADERS).status_code == 200
    assert client.post(f"/api/integrations/{iid}/action/container", json={"id": "../x", "action": "stop"}, headers=HEADERS).status_code == 422
    # Not a docker integration.
    other = client.post("/api/integrations", json={"name": "H", "type": "host"}, headers=HEADERS).json()["id"]
    assert client.post(f"/api/integrations/{other}/docker/web/stop", headers=HEADERS).status_code == 400
