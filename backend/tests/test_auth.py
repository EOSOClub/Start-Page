"""Optional authentication: password, sessions, guest mode, API tokens, proxy header, recovery."""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app import auth, backups, main as main_mod, models
from app.main import app
from app.routers import auth as auth_router
from app.routers import favicons, weather

from conftest import HEADERS

PW = "correct horse battery"
EMPTY_BUNDLE = {"services": [], "dashboards": [], "integrations": [], "settings": {}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in ("STARTPAGE_TRUSTED_PROXIES", "STARTPAGE_AUTH_HEADER", "STARTPAGE_AUTH_DISABLE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(favicons, "CACHE_DIR", tmp_path / "favicons")  # never touch backend/data
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path / "backups")  # nor its backups, nor its database
    monkeypatch.setattr(backups, "_export_bundle", lambda: dict(EMPTY_BUNDLE))
    monkeypatch.setattr(weather, "_cache", OrderedDict())
    monkeypatch.setattr(weather, "_geo_cache", OrderedDict())
    auth.reset_throttle()
    yield
    auth.reset_throttle()


def anon() -> TestClient:
    return TestClient(app)


@pytest.fixture(name="authed")
def authed_fixture(client):
    """`client` with a password set; it holds the session cookie."""
    r = client.post("/api/auth/password", json={"new": PW}, headers=HEADERS)
    assert r.status_code == 200
    return client


def login(password=PW) -> tuple[TestClient, object]:
    c = anon()
    return c, c.post("/api/auth/login", json={"password": password}, headers=HEADERS)


def status(c) -> dict:
    return c.get("/api/auth/status").json()


def set_guest(c, mode):
    assert c.patch("/api/auth/settings", json={"guest_access": mode}, headers=HEADERS).status_code == 200


# ---------- off by default ----------
def test_auth_off_changes_nothing(client):
    assert status(client) == {
        "enabled": False, "has_password": False, "authenticated": False, "guest_access": "none",
        "can_write": True, "user": None, "method": None,
    }
    assert client.get("/api/dashboards").status_code == 200
    assert client.post("/api/dashboards", json={"name": "A"}, headers=HEADERS).status_code == 201
    assert client.get("/api/config/export").status_code == 200


# ---------- password ----------
def test_set_first_password_logs_caller_in(client):
    assert client.post("/api/auth/password", json={"new": "short"}, headers=HEADERS).status_code == 422
    r = client.post("/api/auth/password", json={"new": PW}, headers=HEADERS)
    assert r.status_code == 200 and auth.COOKIE in r.headers["set-cookie"]
    s = status(client)
    assert s["enabled"] and s["authenticated"] and s["method"] == "session" and s["can_write"]
    assert client.get("/api/dashboards").status_code == 200
    assert anon().get("/api/dashboards").status_code == 401


def test_password_is_hashed_with_scrypt(authed, session):
    stored = auth.get_config(session)["password_hash"]
    assert stored.startswith("scrypt$") and PW not in stored
    assert auth.verify_password(PW, stored) and not auth.verify_password("nope", stored)
    assert not auth.verify_password(PW, "")  # no hash never verifies


def test_login_success_and_failure(authed):
    bad = anon().post("/api/auth/login", json={"password": "wrong"}, headers=HEADERS)
    assert bad.status_code == 401 and bad.json() == {"detail": "Invalid password"}
    c, ok = login()
    assert ok.status_code == 200
    assert status(c)["authenticated"] and c.get("/api/dashboards").status_code == 200


def test_login_with_no_password_set_looks_like_wrong_password(client):
    r = client.post("/api/auth/login", json={"password": "anything"}, headers=HEADERS)
    assert r.status_code == 401 and r.json() == {"detail": "Invalid password"}


def test_login_needs_csrf_header(authed):
    assert anon().post("/api/auth/login", json={"password": PW}).status_code == 403


def test_rate_limit_backoff_and_reset(authed, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth, "_clock", lambda: now[0])
    c = anon()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"password": "x" * 9}, headers=HEADERS).status_code == 401
    blocked = c.post("/api/auth/login", json={"password": PW}, headers=HEADERS)  # even the right one
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
    now[0] += 3
    assert c.post("/api/auth/login", json={"password": "x" * 9}, headers=HEADERS).status_code == 401  # 6th: 4 s
    assert int(c.post("/api/auth/login", json={"password": PW}, headers=HEADERS).headers["Retry-After"]) > 2
    now[0] += 10
    assert c.post("/api/auth/login", json={"password": PW}, headers=HEADERS).status_code == 200
    # a successful login clears the counter: five more failures are needed to be blocked again
    for _ in range(4):
        assert c.post("/api/auth/login", json={"password": "x" * 9}, headers=HEADERS).status_code == 401
    assert c.post("/api/auth/login", json={"password": PW}, headers=HEADERS).status_code == 200


def test_session_cookie_flags(authed):
    _, r = login()
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "secure" not in cookie


def test_cookie_secure_only_behind_trusted_proxy(authed, monkeypatch):
    monkeypatch.setenv("STARTPAGE_TRUSTED_PROXIES", "10.0.0.0/8")
    fwd = {**HEADERS, "x-forwarded-proto": "https"}
    trusted = TestClient(app, client=("10.1.2.3", 1))
    assert "secure" in trusted.post("/api/auth/login", json={"password": PW}, headers=fwd).headers["set-cookie"].lower()
    other = TestClient(app, client=("203.0.113.9", 1))
    assert "secure" not in other.post("/api/auth/login", json={"password": PW}, headers=fwd).headers["set-cookie"].lower()
    https = TestClient(app, base_url="https://testserver")
    assert "secure" in https.post("/api/auth/login", json={"password": PW}, headers=HEADERS).headers["set-cookie"].lower()


def test_session_stores_only_a_hash(authed, session):
    token = authed.cookies.get(auth.COOKIE)
    row = session.query(models.AuthSession).one()
    assert row.token_hash != token and row.token_hash == auth._sha(token)


def test_sliding_expiry_and_expired_session(authed, session):
    row = session.query(models.AuthSession).one()
    now = auth._now()
    row.last_seen, row.expires_at = now - timedelta(hours=2), now + timedelta(days=1)
    session.commit()
    assert authed.get("/api/dashboards").status_code == 200
    session.refresh(row)
    assert row.expires_at > now + timedelta(days=29)  # pushed out again
    row.expires_at = now - timedelta(seconds=1)
    session.commit()
    assert authed.get("/api/dashboards").status_code == 401
    assert not status(authed)["authenticated"]


def test_expired_sessions_are_pruned_on_next_login(authed, session):
    row = session.query(models.AuthSession).one()
    row.expires_at = auth._now() - timedelta(days=1)
    session.commit()
    login()
    assert session.query(models.AuthSession).count() == 1


def test_logout_deletes_session_and_clears_cookie(authed, session):
    r = authed.post("/api/auth/logout", headers=HEADERS)
    assert r.status_code == 200 and "max-age=0" in r.headers["set-cookie"].lower()
    assert session.query(models.AuthSession).count() == 0
    assert authed.get("/api/dashboards").status_code == 401


def test_change_password_needs_current_and_revokes_others(authed, session):
    other, _ = login()
    assert other.get("/api/dashboards").status_code == 200
    assert authed.post("/api/auth/password", json={"new": "another password"}, headers=HEADERS).status_code == 403
    assert authed.post("/api/auth/password", json={"current": "bad", "new": "another password"}, headers=HEADERS).status_code == 403
    assert anon().post("/api/auth/password", json={"current": PW, "new": "another password"}, headers=HEADERS).status_code == 401
    r = authed.post("/api/auth/password", json={"current": PW, "new": "another password"}, headers=HEADERS)
    assert r.status_code == 200
    assert other.get("/api/dashboards").status_code == 401  # revoked
    assert authed.get("/api/dashboards").status_code == 200  # the caller is logged in again
    assert session.query(models.AuthSession).count() == 1
    assert login(PW)[1].status_code == 401 and login("another password")[1].status_code == 200


def test_remove_password_wipes_sessions_tokens_and_guest_access(authed, session):
    authed.post("/api/auth/tokens", json={"name": "ci"}, headers=HEADERS)
    set_guest(authed, "read")
    req = lambda cur: authed.request("DELETE", "/api/auth/password", json={"current": cur}, headers=HEADERS)  # noqa: E731
    assert req("bad").status_code == 403
    assert session.query(models.ApiToken).count() == 1  # a refused removal changes nothing
    assert req(PW).status_code == 200
    assert not status(anon())["enabled"]
    assert anon().get("/api/dashboards").status_code == 200
    assert session.query(models.AuthSession).count() == 0
    assert session.query(models.ApiToken).count() == 0
    assert auth.get_config(session) == {"password_hash": "", "guest_access": "none"}


# ---------- guests ----------
def test_guest_none_blocks_reads_and_writes(authed):
    a = anon()
    assert a.get("/api/dashboards").status_code == 401
    assert a.get("/api/dashboards").json() == {"detail": "Authentication required"}
    assert a.post("/api/dashboards", json={"name": "x"}, headers=HEADERS).status_code == 401
    assert a.get("/api/revision").status_code == 401
    s = status(a)  # status stays open and never errors
    assert s["enabled"] and not s["authenticated"] and s["guest_access"] == "none" and not s["can_write"]


def test_spa_shell_and_assets_stay_open(authed):
    assert anon().get("/").status_code == 200
    assert anon().get("/api/nope").status_code == 401  # even unknown /api paths do not leak a 404


@pytest.fixture(name="guest")
def guest_fixture(authed, session):
    session.add(models.Integration(id="i1", name="Kuma", type="json", config={"url": "http://x", "headers": {"K": "secret"}}))
    session.commit()
    authed.patch("/api/settings", json={"notify_type": "webhook", "notify_url": "https://example.com/hook"}, headers=HEADERS)
    set_guest(authed, "read")
    return anon()


def test_guest_read_allows_page_reads(guest):
    s = status(guest)
    assert s["guest_access"] == "read" and not s["can_write"] and not s["authenticated"]
    for path in ("/api/dashboards", "/api/services", "/api/health", "/api/health/history", "/api/health/events",
                 "/api/integrations/data", "/api/integrations/types", "/api/revision", "/api/settings"):
        assert guest.get(path).status_code == 200, path


def test_guest_read_redacts_integration_config_and_notify_url(guest, authed):
    assert guest.get("/api/integrations").json()[0]["config"] == {}
    # notify_url is write-only for everyone now: a fixed mask, never the URL.
    assert guest.get("/api/settings").json()["notify_url"] == "********"
    # Integration secrets are write-only for everyone now: signed-in users see the mask, not the headers.
    assert authed.get("/api/integrations").json()[0]["config"]["headers"] == "********"
    assert authed.get("/api/settings").json()["notify_url"] == "********"


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/integrations/i1/docker/abc/restart"),
    ("POST", "/api/integrations/i1/refresh"),
    ("POST", "/api/health/svc/check"),
    ("POST", "/api/notify/test"),
    ("PUT", "/api/dashboards/d/layout"),
    ("PUT", "/api/dashboards/order"),
    ("POST", "/api/dashboards"),
    ("PATCH", "/api/dashboards/d"),
    ("DELETE", "/api/dashboards/d"),
    ("POST", "/api/dashboards/d/widgets"),
    ("PATCH", "/api/widgets/w"),
    ("POST", "/api/widgets/w/duplicate"),
    ("DELETE", "/api/widgets/w"),
    ("POST", "/api/services"),
    ("PATCH", "/api/services/s"),
    ("DELETE", "/api/services/s"),
    ("POST", "/api/integrations"),
    ("PATCH", "/api/integrations/i1"),
    ("DELETE", "/api/integrations/i1"),
    ("PATCH", "/api/settings"),
    ("POST", "/api/config/import"),
    ("POST", "/api/backups"),
    ("POST", "/api/backups/x/restore"),
    ("DELETE", "/api/backups/x"),
    ("POST", "/api/auth/password"),
    ("DELETE", "/api/auth/password"),
    ("PATCH", "/api/auth/settings"),
    ("POST", "/api/auth/tokens"),
    ("DELETE", "/api/auth/tokens/t"),
])
def test_guest_read_blocks_every_unsafe_method(guest, method, path):
    r = guest.request(method, path, json={}, headers=HEADERS)
    assert r.status_code == 401, (method, path, r.status_code)


@pytest.mark.parametrize("path", [
    "/api/config/export", "/api/backups", "/api/backups/x", "/api/auth/tokens", "/api/sitemeta?url=http://example.com",
])
def test_guest_read_blocks_sensitive_reads(guest, path):
    assert guest.get(path).status_code == 401


def test_guest_favicon_is_cache_only(guest, authed, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("guest must not trigger a server-side fetch")

    monkeypatch.setattr(favicons, "_fetch", boom)
    assert guest.get("/api/favicon?url=http://example.com").status_code == 404
    favicons.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = favicons.hashlib.sha1(b"http://example.com").hexdigest()
    (favicons.CACHE_DIR / f"{key}.img").write_bytes(b"\x89PNG....")
    (favicons.CACHE_DIR / f"{key}.json").write_text('{"origin":"http://example.com","type":"image/png","fetched":1}')
    assert guest.get("/api/favicon?url=http://example.com").status_code == 200  # stale cache is fine for guests


def test_guest_read_can_be_switched_back(authed):
    set_guest(authed, "read")
    assert anon().get("/api/dashboards").status_code == 200
    set_guest(authed, "none")
    assert anon().get("/api/dashboards").status_code == 401
    assert authed.patch("/api/auth/settings", json={"guest_access": "write"}, headers=HEADERS).status_code == 422


# ---------- API tokens ----------
def test_bearer_token_flow(authed):
    made = authed.post("/api/auth/tokens", json={"name": "mcp"}, headers=HEADERS)
    assert made.status_code == 201
    token = made.json()["token"]
    assert token.startswith("sp_")
    listed = authed.get("/api/auth/tokens").json()
    assert len(listed) == 1 and set(listed[0]) == {"id", "name", "created_at", "last_used"}
    assert token not in authed.get("/api/auth/tokens").text and "hash" not in authed.get("/api/auth/tokens").text

    bearer = {"Authorization": f"Bearer {token}"}
    a = anon()
    assert a.get("/api/dashboards", headers=bearer).status_code == 200
    s = a.get("/api/auth/status", headers=bearer).json()
    assert s["authenticated"] and s["method"] == "token" and s["user"] == "mcp"
    assert a.get("/api/auth/tokens", headers=bearer).status_code == 200
    assert a.get("/api/dashboards", headers={"Authorization": "Bearer sp_wrong"}).status_code == 401

    assert authed.delete(f"/api/auth/tokens/{made.json()['id']}", headers=HEADERS).status_code == 204
    assert a.get("/api/dashboards", headers=bearer).status_code == 401
    assert authed.delete("/api/auth/tokens/gone", headers=HEADERS).status_code == 404


def test_token_stored_hashed_and_last_used_set(authed, session):
    token = authed.post("/api/auth/tokens", json={"name": "t"}, headers=HEADERS).json()["token"]
    anon().get("/api/dashboards", headers={"Authorization": f"Bearer {token}"})
    row = session.query(models.ApiToken).one()
    assert row.token_hash == auth._sha(token) and row.last_used is not None


def test_bearer_still_needs_csrf_header(authed):
    token = authed.post("/api/auth/tokens", json={"name": "t"}, headers=HEADERS).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    assert anon().post("/api/dashboards", json={"name": "A"}, headers=bearer).status_code == 403
    assert anon().post("/api/dashboards", json={"name": "A"}, headers={**bearer, **HEADERS}).status_code == 201


def test_bearer_ignored_when_auth_off(client):
    assert client.get("/api/dashboards", headers={"Authorization": "Bearer junk"}).status_code == 200


# ---------- trusted proxy header ----------
def test_proxy_header_trusted_only_from_listed_peer(authed, monkeypatch):
    monkeypatch.setenv("STARTPAGE_TRUSTED_PROXIES", "10.0.0.0/8, 192.168.1.5")
    monkeypatch.setenv("STARTPAGE_AUTH_HEADER", "Remote-User")
    hdr = {"Remote-User": "alice"}
    proxy = TestClient(app, client=("10.9.9.9", 1))
    s = proxy.get("/api/auth/status", headers=hdr).json()
    assert s["authenticated"] and s["method"] == "proxy" and s["user"] == "alice"
    assert proxy.get("/api/dashboards", headers=hdr).status_code == 200
    assert TestClient(app, client=("192.168.1.5", 1)).get("/api/dashboards", headers=hdr).status_code == 200
    assert proxy.get("/api/dashboards").status_code == 401  # header absent
    for peer in ("203.0.113.7", "192.168.1.6", "testclient"):
        assert TestClient(app, client=(peer, 1)).get("/api/dashboards", headers=hdr).status_code == 401, peer
    # CSRF rule is unchanged
    assert proxy.post("/api/dashboards", json={"name": "A"}, headers=hdr).status_code == 403
    assert proxy.post("/api/dashboards", json={"name": "A"}, headers={**hdr, **HEADERS}).status_code == 201


def test_proxy_auth_alone_enables_auth(client, monkeypatch):
    monkeypatch.setenv("STARTPAGE_TRUSTED_PROXIES", "10.0.0.0/8")
    monkeypatch.setenv("STARTPAGE_AUTH_HEADER", "Remote-User")
    assert status(client)["enabled"] and client.get("/api/dashboards").status_code == 401
    assert client.post("/api/auth/password", json={"new": PW}, headers=HEADERS).status_code == 401
    proxy = TestClient(app, client=("10.0.0.1", 1))
    assert proxy.post("/api/auth/password", json={"new": PW}, headers={**HEADERS, "Remote-User": "bob"}).status_code == 200


def test_header_needs_both_env_vars(authed, monkeypatch):
    monkeypatch.setenv("STARTPAGE_AUTH_HEADER", "Remote-User")  # no trusted proxies listed
    assert TestClient(app, client=("10.0.0.1", 1)).get("/api/dashboards", headers={"Remote-User": "x"}).status_code == 401


def test_forwarded_for_only_honoured_from_trusted_peer(monkeypatch):
    monkeypatch.setenv("STARTPAGE_TRUSTED_PROXIES", "10.0.0.0/8")

    class Req:
        def __init__(self, peer, xff):
            self.client = type("C", (), {"host": peer})()
            self.headers = {"x-forwarded-for": xff}

    assert auth.client_ip(Req("10.0.0.2", "198.51.100.4, 10.0.0.9")) == "198.51.100.4"
    assert auth.client_ip(Req("203.0.113.1", "198.51.100.4")) == "203.0.113.1"


# ---------- kill switch, secrecy, recovery ----------
def test_auth_disable_env_turns_enforcement_off(authed, monkeypatch):
    assert anon().get("/api/dashboards").status_code == 401
    monkeypatch.setenv("STARTPAGE_AUTH_DISABLE", "1")
    a = anon()
    assert a.get("/api/dashboards").status_code == 200
    assert not status(a)["enabled"] and status(a)["can_write"]
    assert a.post("/api/auth/login", json={"password": PW}, headers=HEADERS).status_code == 401


def test_auth_blob_never_exposed(authed, session):
    set_guest(authed, "read")
    for path in ("/api/settings", "/api/config/export"):
        body = authed.get(path).text
        assert "password_hash" not in body and "scrypt" not in body and "guest_access" not in body, path
    assert "scrypt" not in authed.post("/api/backups", headers=HEADERS).text
    assert "scrypt" not in authed.get("/api/auth/status").text


def test_import_and_settings_patch_cannot_touch_auth(authed, session):
    authed.patch("/api/settings", json={"password_hash": "x", "guest_access": "read"}, headers=HEADERS)
    cfg = auth.get_config(session)
    assert cfg["password_hash"].startswith("scrypt$") and cfg["guest_access"] == "none"
    # ... nor can a replacing import or a backup restore: the auth row, sessions and tokens survive
    authed.post("/api/auth/tokens", json={"name": "t"}, headers=HEADERS)
    set_guest(authed, "read")
    other, _ = login()
    before = (auth.get_config(session), session.query(models.AuthSession).count(), session.query(models.ApiToken).count())
    assert before[1:] == (2, 1)
    hostile = {**EMPTY_BUNDLE, "settings": {"password_hash": "", "guest_access": "none"}}
    assert authed.post("/api/config/import?replace=true", json=hostile, headers=HEADERS).status_code == 200
    name = authed.post("/api/backups", headers=HEADERS).json()["name"]
    assert authed.post(f"/api/backups/{name}/restore", headers=HEADERS).status_code == 200
    after = (auth.get_config(session), session.query(models.AuthSession).count(), session.query(models.ApiToken).count())
    assert after == before
    assert other.get("/api/dashboards").status_code == 200  # still signed in


def test_cli_reset_removes_everything(authed, session):
    authed.post("/api/auth/tokens", json={"name": "t"}, headers=HEADERS)
    set_guest(authed, "read")
    out = auth.reset(session)
    assert auth.get_config(session)["guest_access"] == "none"
    assert out == {"password_removed": True, "sessions_removed": 1, "tokens_removed": 1}
    assert not status(anon())["enabled"]
    assert anon().get("/api/dashboards").status_code == 200
    assert auth.reset(session) == {"password_removed": False, "sessions_removed": 0, "tokens_removed": 0}


def test_cli_usage_error():
    assert auth.main([]) == 2


# ---------- B1: credentials made while auth is off must not survive into the "on" state ----------
def test_tokens_and_guest_access_refused_while_auth_is_off(client, session):
    for method, path, body in (
        ("POST", "/api/auth/tokens", {"name": "x"}),
        ("GET", "/api/auth/tokens", None),
        ("DELETE", "/api/auth/tokens", None),
        ("DELETE", "/api/auth/tokens/abc", None),
        ("PATCH", "/api/auth/settings", {"guest_access": "read"}),
    ):
        r = client.request(method, path, json=body, headers=HEADERS)
        assert r.status_code == 409, (method, path, r.status_code)
        assert "Set a password first" in r.json()["detail"]
    assert session.query(models.ApiToken).count() == 0
    assert auth.get_config(session)["guest_access"] == "none"


def test_tokens_and_guest_access_refused_under_auth_disable(authed, session, monkeypatch):
    authed.post("/api/auth/tokens", json={"name": "keep"}, headers=HEADERS)
    monkeypatch.setenv("STARTPAGE_AUTH_DISABLE", "1")
    a = anon()
    for method, path, body in (
        ("POST", "/api/auth/tokens", {"name": "x"}),
        ("GET", "/api/auth/tokens", None),
        ("DELETE", "/api/auth/tokens", None),
        ("PATCH", "/api/auth/settings", {"guest_access": "read"}),
    ):
        r = a.request(method, path, json=body, headers=HEADERS)
        assert r.status_code == 409 and "STARTPAGE_AUTH_DISABLE" in r.json()["detail"], (method, path)
    assert session.query(models.ApiToken).count() == 1
    assert auth.get_config(session)["guest_access"] == "none"


def test_first_password_wipes_credentials_from_a_pre_fix_database(client, session):
    now = auth._now()
    session.add(models.ApiToken(name="planted", token_hash=auth._sha("sp_planted"), created_at=now))
    session.add(models.AuthSession(token_hash=auth._sha("old-cookie"), created_at=now, expires_at=now + timedelta(days=5), last_seen=now))
    session.commit()
    auth.save_config(session, guest_access="read")  # as left behind by an unauthenticated PATCH
    r = client.post("/api/auth/password", json={"new": PW}, headers=HEADERS)
    assert r.status_code == 200
    assert session.query(models.ApiToken).count() == 0
    assert [s.token_hash for s in session.query(models.AuthSession)] == [auth._sha(client.cookies.get(auth.COOKIE))]
    assert auth.get_config(session)["guest_access"] == "none"
    assert anon().get("/api/dashboards", headers={"Authorization": "Bearer sp_planted"}).status_code == 401
    assert anon().get("/api/dashboards", cookies={auth.COOKIE: "old-cookie"}).status_code == 401
    assert anon().get("/api/dashboards").status_code == 401  # and nobody got guest access out of it


def test_password_change_keeps_tokens_and_revoke_all_drops_them(authed, session):
    token = authed.post("/api/auth/tokens", json={"name": "a"}, headers=HEADERS).json()["token"]
    authed.post("/api/auth/tokens", json={"name": "b"}, headers=HEADERS)
    set_guest(authed, "read")
    bearer = {"Authorization": f"Bearer {token}"}
    r = authed.post("/api/auth/password", json={"current": PW, "new": "another password"}, headers=HEADERS)
    assert r.status_code == 200
    assert session.query(models.ApiToken).count() == 2 and auth.get_config(session)["guest_access"] == "read"
    assert anon().get("/api/dashboards", headers=bearer).status_code == 200
    assert anon().delete("/api/auth/tokens", headers=HEADERS).status_code == 401
    r = authed.delete("/api/auth/tokens", headers=HEADERS)
    assert r.status_code == 200 and r.json() == {"revoked": 2}
    assert session.query(models.ApiToken).count() == 0
    assert anon().get("/api/auth/status", headers=bearer).json()["authenticated"] is False


def test_first_password_keeps_tokens_made_by_a_proxy_user(client, session, monkeypatch):
    """With proxy auth, auth is already on, so a token made by the signed-in proxy user is legitimate."""
    monkeypatch.setenv("STARTPAGE_TRUSTED_PROXIES", "10.0.0.0/8")
    monkeypatch.setenv("STARTPAGE_AUTH_HEADER", "Remote-User")
    proxy = TestClient(app, client=("10.0.0.1", 1))
    h = {**HEADERS, "Remote-User": "bob"}
    assert proxy.post("/api/auth/tokens", json={"name": "t"}, headers=h).status_code == 201
    assert proxy.post("/api/auth/password", json={"new": PW}, headers=h).status_code == 200
    assert session.query(models.ApiToken).count() == 1


def test_password_change_and_removal_under_auth_disable(authed, session, monkeypatch):
    authed.post("/api/auth/tokens", json={"name": "t"}, headers=HEADERS)
    monkeypatch.setenv("STARTPAGE_AUTH_DISABLE", "1")
    a = anon()
    new = "another password"
    assert a.post("/api/auth/password", json={"new": new}, headers=HEADERS).status_code == 403  # still needs the current one
    assert a.post("/api/auth/password", json={"current": "bad", "new": new}, headers=HEADERS).status_code == 403
    assert a.post("/api/auth/password", json={"current": PW, "new": new}, headers=HEADERS).status_code == 200
    assert session.query(models.ApiToken).count() == 1  # a change keeps tokens
    remove = lambda cur: a.request("DELETE", "/api/auth/password", json={"current": cur}, headers=HEADERS)  # noqa: E731
    assert remove(PW).status_code == 403  # the old password no longer works
    assert remove(new).status_code == 200
    assert session.query(models.ApiToken).count() == 0 and session.query(models.AuthSession).count() == 0
    assert not auth.get_config(session)["password_hash"]


# ---------- passwords and sessions ----------
@pytest.mark.parametrize("bad", [" " * 12, "\t\n" * 6, "short", ""])
def test_blank_or_short_passwords_rejected_on_every_path(authed, bad):
    assert authed.post("/api/auth/password", json={"current": PW, "new": bad}, headers=HEADERS).status_code == 422
    assert login(PW)[1].status_code == 200  # nothing changed


def test_blank_first_password_rejected(client, session):
    assert client.post("/api/auth/password", json={"new": " " * 10}, headers=HEADERS).status_code == 422
    assert not auth.get_config(session)["password_hash"]
    assert client.post("/api/auth/password", json={"new": "  padded pw  "}, headers=HEADERS).status_code == 200


def test_session_has_an_absolute_lifetime(authed, session):
    row = session.query(models.AuthSession).one()
    row_id = row.id
    now = auth._now()
    row.created_at, row.last_seen, row.expires_at = now - timedelta(days=89), now, now + timedelta(days=30)
    session.commit()
    assert authed.get("/api/dashboards").status_code == 200
    row.created_at = now - timedelta(days=91)  # still "valid" by the sliding expiry, but too old
    session.commit()
    assert authed.get("/api/dashboards").status_code == 401
    assert not status(authed)["authenticated"]
    login()  # the next sign-in prunes it
    assert session.query(models.AuthSession).filter_by(id=row_id).count() == 0


# ---------- login throttle ----------
@pytest.fixture(name="clock")
def clock_fixture(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth, "_clock", lambda: now[0])
    return now


def bad_login(c):
    return c.post("/api/auth/login", json={"password": "x" * 9}, headers=HEADERS)


def test_block_never_exceeds_60_seconds(authed, clock):
    c, waits = anon(), []
    for _ in range(14):
        r = bad_login(c)
        while r.status_code == 429:
            waits.append(int(r.headers["Retry-After"]))
            clock[0] += waits[-1]
            r = bad_login(c)
        assert r.status_code == 401
    assert waits[:5] == [2, 4, 8, 16, 32] and max(waits) == 60
    other = anon().post("/api/auth/login", json={"password": PW}, headers=HEADERS)  # a second client, same address
    assert other.status_code == 429 and 1 <= int(other.headers["Retry-After"]) <= 60


def test_attempts_while_blocked_do_not_extend_the_block(clock):
    for _ in range(auth.FREE_FAILURES):
        assert auth.throttle_reserve("203.0.113.5") == 0
    waits = {auth.throttle_reserve("203.0.113.5") for _ in range(20)}
    assert waits == {2}
    assert auth._fails["203.0.113.5"][0] == auth.FREE_FAILURES  # refused attempts are not counted
    clock[0] += 1.5
    assert auth.throttle_reserve("203.0.113.5") == 1  # the block did not move
    clock[0] += 0.5
    assert auth.throttle_reserve("203.0.113.5") == 0


def test_success_clears_the_record(clock):
    for _ in range(4):
        assert auth.throttle_reserve("203.0.113.6") == 0
    auth.throttle_release("203.0.113.6")
    assert "203.0.113.6" not in auth._fails
    for _ in range(4):
        assert auth.throttle_reserve("203.0.113.6") == 0


def test_throttle_keys_ipv6_by_64_and_ipv4_mapped_as_ipv4():
    assert auth.throttle_key("2001:db8:1:2::1") == auth.throttle_key("2001:db8:1:2:ffff:ffff:ffff:ffff")
    assert auth.throttle_key("2001:db8:1:2::1") != auth.throttle_key("2001:db8:1:3::1")
    assert auth.throttle_key("::ffff:198.51.100.7") == auth.throttle_key("198.51.100.7") == "198.51.100.7"
    assert auth.throttle_key("198.51.100.7") != auth.throttle_key("198.51.100.8")  # IPv4: full address
    assert auth.throttle_key("testclient") == "testclient"


def test_rotating_addresses_inside_one_ipv6_64_share_a_budget(clock):
    for i in range(auth.FREE_FAILURES):
        assert auth.throttle_reserve(f"2001:db8:aaaa:bbbb::{i + 1:x}") == 0
    assert auth.throttle_reserve("2001:db8:aaaa:bbbb:dead:beef::1") > 0
    assert auth.throttle_reserve("2001:db8:aaaa:cccc::1") == 0  # another /64 is unaffected


def test_throttle_table_is_capped(clock, monkeypatch):
    monkeypatch.setattr(auth, "MAX_ENTRIES", 50)
    monkeypatch.setattr(auth, "GLOBAL_LIMIT", 10**9)
    for i in range(500):
        clock[0] += 0.001
        auth.throttle_reserve(f"10.1.{i // 250}.{i % 250}")
    assert len(auth._fails) <= 50
    assert "10.1.0.0" not in auth._fails and "10.1.1.249" in auth._fails  # the oldest went, the newest stayed


def test_global_budget_slows_everyone_without_locking_out(clock):
    for i in range(auth.GLOBAL_LIMIT):
        assert auth.throttle_reserve(f"192.0.2.{i}") == 0
    assert auth.throttle_reserve("198.51.100.1") == auth.GLOBAL_DELAY  # a never-seen address
    clock[0] += 2
    assert auth.throttle_reserve("198.51.100.2") == 3
    clock[0] += 3
    assert auth.throttle_reserve("198.51.100.3") == 0  # one attempt per GLOBAL_DELAY gets through
    assert auth.throttle_reserve("198.51.100.4") == auth.GLOBAL_DELAY
    clock[0] += auth.GLOBAL_WINDOW + 1  # the burst is over: normal service
    assert auth.throttle_reserve("198.51.100.5") == 0 and auth.throttle_reserve("198.51.100.6") == 0


def test_global_budget_answers_429_with_retry_after_5(authed, clock):
    for i in range(auth.GLOBAL_LIMIT):
        auth.throttle_reserve(f"192.0.2.{i}")
    r = anon().post("/api/auth/login", json={"password": PW}, headers=HEADERS)
    assert r.status_code == 429 and r.headers["Retry-After"] == "5"


def test_parallel_burst_evaluates_at_most_the_allowed_attempts(monkeypatch):
    calls, lock = [], threading.Lock()

    def slow_verify(password, stored):
        with lock:
            calls.append(password)
        time.sleep(0.05)  # a long window for the other threads to race in
        return False

    monkeypatch.setattr(auth, "verify_password", slow_verify)
    monkeypatch.setattr(auth, "get_config", lambda db: {"password_hash": "x", "guest_access": "none"})
    req = type("Req", (), {"client": type("C", (), {"host": "203.0.113.50"})(), "headers": {}})()
    codes = []

    def attempt(_):
        try:
            auth_router.login(auth_router.LoginIn(password="guess"), req, Response(), db=None)
        except HTTPException as e:
            codes.append(e.status_code)

    with ThreadPoolExecutor(max_workers=40) as pool:
        list(pool.map(attempt, range(40)))
    assert len(calls) == auth.FREE_FAILURES  # the other 35 were refused before any hashing
    assert sorted(codes) == [401] * auth.FREE_FAILURES + [429] * 35


def test_throttle_warns_at_most_once_a_minute_and_names_the_proxy_setting(clock, caplog):
    caplog.set_level(logging.WARNING, logger="startpage.auth")
    for _ in range(auth.FREE_FAILURES + 20):
        auth.throttle_reserve("203.0.113.9")
    warnings = [r for r in caplog.records if "throttle engaged" in r.getMessage()]
    assert len(warnings) == 1 and "STARTPAGE_TRUSTED_PROXIES" in warnings[0].getMessage()
    clock[0] += 61
    for _ in range(auth.FREE_FAILURES + 2):
        auth.throttle_reserve("203.0.113.9")
    assert len([r for r in caplog.records if "throttle engaged" in r.getMessage()]) == 2


def test_spoofed_forwarded_for_from_an_untrusted_peer_does_not_change_the_key(authed, monkeypatch):
    monkeypatch.setenv("STARTPAGE_TRUSTED_PROXIES", "10.0.0.0/8")
    c = TestClient(app, client=("203.0.113.77", 1))
    codes = [c.post("/api/auth/login", json={"password": "x" * 9}, headers={**HEADERS, "X-Forwarded-For": f"198.51.100.{i}"}).status_code
             for i in range(6)]
    assert codes == [401] * 5 + [429]
    # behind a trusted proxy the forwarded client is the key: another client is not blocked
    proxy = TestClient(app, client=("10.0.0.2", 1))
    r = proxy.post("/api/auth/login", json={"password": PW}, headers={**HEADERS, "X-Forwarded-For": "198.51.100.200"})
    assert r.status_code == 200


# ---------- trusted-proxy parsing is cached ----------
def test_trusted_proxy_env_is_parsed_once_per_value(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="startpage.auth")
    monkeypatch.setenv("STARTPAGE_TRUSTED_PROXIES", "10.0.0.0/8, not-an-ip")
    for _ in range(5):
        assert auth.is_trusted("10.1.1.1") and not auth.is_trusted("11.1.1.1")
    assert len([r for r in caplog.records if "not-an-ip" in r.getMessage()]) == 1
    monkeypatch.setenv("STARTPAGE_TRUSTED_PROXIES", "11.0.0.0/8")  # a changed value is re-read
    assert auth.is_trusted("11.1.1.1") and not auth.is_trusted("10.1.1.1")
    monkeypatch.delenv("STARTPAGE_TRUSTED_PROXIES")
    assert not auth.is_trusted("11.1.1.1")


# ---------- docs live under /api and are guarded ----------
def test_docs_moved_under_api_and_guarded(authed):
    paths = ("/api/docs", "/api/openapi.json", "/api/docs/oauth2-redirect")
    for p in paths:
        assert anon().get(p).status_code == 401, p
    docs = authed.get("/api/docs")
    assert docs.status_code == 200 and "/api/openapi.json" in docs.text
    assert authed.get("/api/openapi.json").json()["info"]["title"] == "Start Page"
    set_guest(authed, "read")
    for p in paths:
        assert anon().get(p).status_code == 401, p  # guests never see them
    assert anon().get("/api/dashboards").status_code == 200  # while they still read the page


def test_old_docs_paths_are_not_docs(authed):
    for p in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        r = authed.get(p)
        assert r.status_code == 404 and "swagger" not in r.text.lower(), p
    assert authed.get("/api/redoc").status_code == 404


# ---------- the /api boundary uses the raw ASGI path, not the Host-derived URL ----------
@pytest.mark.parametrize("path", ["//api/dashboards", "///api/dashboards", "/api//dashboards", "/API/dashboards", "/Api/dashboards", "/api"])
def test_odd_api_spellings_do_not_bypass_the_guard(authed, path):
    url = "http://testserver" + path  # a bare "//api/..." would be read as a protocol-relative URL
    assert anon().get(url).status_code == 401, path
    r = authed.get(url)  # signed in: never a page of data either
    assert r.status_code == 404 and "Home" not in r.text, path


@pytest.mark.parametrize("host", ["x?", "x#", "evil.example/", "evil.example/api/auth/status?", "a b", "x/y?/api/"])
def test_odd_host_header_cannot_move_the_api_boundary(authed, host):
    assert anon().get("/api/dashboards", headers={"host": host}).status_code == 401, host
    assert anon().post("/api/dashboards", json={"name": "z"}, headers={"host": host}).status_code == 403  # csrf header missing
    assert anon().post("/api/dashboards", json={"name": "z"}, headers={"host": host, **HEADERS}).status_code == 401
    assert authed.get("/api/dashboards", headers={"host": host}).status_code == 200
    assert anon().get("/", headers={"host": host}).status_code == 200  # the SPA shell stays open


# ---------- middleware: DB errors and CORS ----------
def test_db_error_in_the_guard_is_503_and_fails_closed(authed, monkeypatch):
    def boom(db, request):
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(auth, "resolve", boom)
    r = anon().get("/api/dashboards")
    assert r.status_code == 503 and r.json() == main_mod.DB_BUSY == {"detail": "Database busy — please retry"}
    assert authed.get("/api/dashboards").status_code == 503  # nobody is let through blind
    assert anon().get("/").status_code == 200  # the shell does not touch the database


CORS_SCRIPT = """
import json
from fastapi.testclient import TestClient
from app import auth
from app.database import Base, SessionLocal, engine
from app.main import app

Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    auth.save_config(db, password_hash=auth.hash_password("correct horse battery"))
c = TestClient(app)
ok, bad = {"Origin": "https://ui.example"}, {"Origin": "https://evil.example"}
pick = lambda r: [r.status_code, r.headers.get("access-control-allow-origin")]
print("RESULT " + json.dumps({
    "unauth": pick(c.get("/api/dashboards", headers=ok)),
    "other_origin": pick(c.get("/api/dashboards", headers=bad)),
    "no_csrf_header": pick(c.post("/api/dashboards", json={"name": "x"}, headers=ok)),
    "cross_site": pick(c.post("/api/dashboards", json={"name": "x"}, headers={**ok, "sec-fetch-site": "cross-site"})),
    "evil_cross_site": pick(c.post("/api/dashboards", json={"name": "x"}, headers={**bad, "sec-fetch-site": "cross-site", "x-requested-by": "x"})),
    "preflight": pick(c.options("/api/dashboards", headers={**ok, "Access-Control-Request-Method": "POST"})),
}))
"""


def test_cors_headers_ride_on_401_and_403_when_an_origin_is_allowed(tmp_path):
    # CORS is wired at import time, so this needs a fresh interpreter (with its own throwaway data dir).
    env = {k: v for k, v in os.environ.items() if not k.startswith("STARTPAGE_")}
    env.update(STARTPAGE_CORS_ORIGINS="https://ui.example", STARTPAGE_DATA_DIR=str(tmp_path / "corsdata"))
    done = subprocess.run([sys.executable, "-c", CORS_SCRIPT], cwd=Path(__file__).resolve().parents[1], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, done.stderr[-800:]
    out = json.loads(line[len("RESULT "):])
    origin = "https://ui.example"
    assert out["unauth"] == [401, origin]  # an allowed origin can read the 401
    assert out["other_origin"] == [401, None]
    assert out["no_csrf_header"] == [403, origin]
    assert out["cross_site"] == [403, origin]  # the CSRF check is not weakened
    assert out["evil_cross_site"] == [403, None]
    assert out["preflight"] == [200, origin]


# ---------- weather: bounded caches, and guests are served from cache only ----------
class _FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return self

    def json(self):
        return self.data


class _FakeWeatherClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        if url == weather.GEOCODE_URL:
            return _FakeResp({"results": [{"name": params["name"], "latitude": 1.0, "longitude": 2.0, "country": "X"}]})
        return _FakeResp({"current": {"temperature_2m": 5}, "daily": {"time": []}})


def test_weather_caches_are_bounded(authed, monkeypatch):
    monkeypatch.setattr(weather, "MAX_CACHE", 3)
    monkeypatch.setattr(weather.httpx, "AsyncClient", _FakeWeatherClient)
    for i in range(8):
        assert authed.get(f"/api/weather?location=City{i}").status_code == 200
    assert len(weather._cache) == 3 and len(weather._geo_cache) == 3
    assert ("city7", "metric", 3) in weather._cache and ("city0", "metric", 3) not in weather._cache
    assert authed.get("/api/weather?location=" + "a" * 201).status_code == 422


def test_guest_weather_is_cache_only(guest, authed, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("guest must not trigger a server-side fetch")

    monkeypatch.setattr(weather.httpx, "AsyncClient", boom)
    r = guest.get("/api/weather?location=Paris")
    assert r.status_code == 404 and "not cached" in r.json()["detail"]
    assert guest.get("/api/weather?location=1.5,2.5").status_code == 404  # coordinates need the forecast call too
    assert len(weather._cache) == 0 and len(weather._geo_cache) == 0  # and a guest grows nothing
    stale = (time.time() - 10 * weather.TTL, {"location": "Paris", "units": "metric", "current": {}, "daily": []})
    weather._cache[("paris", "metric", 3)] = stale
    r = guest.get("/api/weather?location=Paris")
    assert r.status_code == 200 and r.json()["location"] == "Paris"  # a stale entry is fine for guests
    # a signed-in user still refreshes through the network
    monkeypatch.setattr(weather.httpx, "AsyncClient", _FakeWeatherClient)
    assert authed.get("/api/weather?location=Paris").status_code == 200
