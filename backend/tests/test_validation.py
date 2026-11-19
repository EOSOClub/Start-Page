"""Shared service / widget URL validation on the plain routes, the whole-config import and backup restore.

Written values are refused (422, per field); stored or imported old data is repaired and reported as a
warning, and reading never fails because of what is stored.
"""
import pytest
from conftest import HEADERS

from app import models, validation

SVC = {"name": "Nas", "host": "10.0.0.5", "port": 8080}


def _dash(client, name="P"):
    return client.post("/api/dashboards", json={"name": name}, headers=HEADERS).json()["id"]


# ---------------------------------------------------------------- plain service routes
@pytest.mark.parametrize("bad", [
    {"protocol": "javascript"}, {"protocol": "ftp"}, {"protocol": ""}, {"host": "a b"}, {"host": "evil.com/x"},
    {"host": "u@h"}, {"port": 0}, {"port": 70000}, {"icon": "<script>"}, {"health_type": "udp"},
    {"health_url": "javascript:alert(1)"}, {"health_interval": 1},
])
def test_create_service_rejects_bad_fields_with_a_per_field_message(client, bad):
    res = client.post("/api/services", json={**SVC, **bad}, headers=HEADERS)
    assert res.status_code == 422, bad
    field = next(iter(bad))
    assert [e["loc"] for e in res.json()["detail"]] == [["body", field]]
    assert client.get("/api/services").json() == []


def test_create_service_accepts_http_https_and_normalises_the_path(client):
    for proto in ("http", "https"):
        res = client.post("/api/services", json={**SVC, "protocol": proto, "path": "a/b"}, headers=HEADERS)
        assert res.status_code == 201 and res.json()["path"] == "/a/b"
    assert client.post("/api/services", json={**SVC, "health_type": "tcp"}, headers=HEADERS).status_code == 201


def test_patch_service_checks_only_what_it_writes(client, session):
    old = models.Service(id="old", name="Old", protocol="gopher", host="h h", port=1, path="")
    session.add(old)
    session.commit()
    got = client.get("/api/services/old")  # reads never fail on stored data
    assert got.status_code == 200 and got.json()["protocol"] == "gopher"
    assert client.get("/api/services").status_code == 200
    assert client.patch("/api/services/old", json={"name": "Renamed"}, headers=HEADERS).status_code == 200
    res = client.patch("/api/services/old", json={"protocol": "javascript"}, headers=HEADERS)
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["body", "protocol"]
    assert client.patch("/api/services/old", json={"protocol": "https", "host": "ok.lan"}, headers=HEADERS).status_code == 200
    assert client.patch("/api/services/old", json={"port": None}, headers=HEADERS).status_code == 200


def test_a_javascript_path_is_plain_path_text(client):
    """A path is only ever appended after protocol://host, so this can never be a scheme."""
    res = client.post("/api/services", json={**SVC, "path": "javascript:alert(1)"}, headers=HEADERS)
    assert res.status_code == 201
    body = res.json()
    assert body["path"] == "/javascript:alert(1)"
    assert body["url"] == "http://10.0.0.5:8080/javascript:alert(1)"
    assert body["url"].startswith("http://")


# ---------------------------------------------------------------- widget configs on the plain routes
EVIL = ["javascript:alert(1)", " javascript:alert(1)", "java\tscript:alert(1)", "JaVaScRiPt:x", "vbscript:x",
        "data:text/html,<script>alert(1)</script>", "\x01javascript:alert(1)"]


@pytest.mark.parametrize("url", EVIL)
def test_widget_writes_refuse_dangerous_schemes(client, url):
    dash = _dash(client)
    res = client.post(f"/api/dashboards/{dash}/widgets", json={"type": "link", "config": {"url": url}}, headers=HEADERS)
    assert res.status_code == 422, url
    nested = {"type": "links", "config": {"links": [{"title": "x", "url": url}]}}
    assert client.post(f"/api/dashboards/{dash}/widgets", json=nested, headers=HEADERS).status_code == 422
    ok = client.post(f"/api/dashboards/{dash}/widgets", json={"type": "link", "config": {"url": "https://a.b"}}, headers=HEADERS).json()
    res = client.patch(f"/api/widgets/{ok['id']}", json={"config": {"href": url}}, headers=HEADERS)
    assert res.status_code == 422, url
    assert client.get(f"/api/dashboards/{dash}").json()["widgets"][0]["config"] == {"url": "https://a.b"}


@pytest.mark.parametrize("url", ["obsidian://open?vault=x", "steam://run/1", "ssh://host", "mailto:a@b.c", "/relative", "https://a.b/?q=1", ""])
def test_widget_writes_keep_app_schemes(client, url):
    dash = _dash(client)
    res = client.post(f"/api/dashboards/{dash}/widgets", json={"type": "link", "config": {"url": url}}, headers=HEADERS)
    assert res.status_code == 201, url


def test_widget_data_image_only_for_icons_and_image_widgets(client):
    dash = _dash(client)
    img = "data:image/png;base64,iVBORw0KGgo="
    post = lambda t, c: client.post(f"/api/dashboards/{dash}/widgets", json={"type": t, "config": c}, headers=HEADERS).status_code  # noqa: E731
    assert post("image", {"url": img}) == 201
    assert post("link", {"icon": img}) == 201
    assert post("link", {"url": img}) == 422


# ---------------------------------------------------------------- whole-config import and restore
def _old_bundle():
    return {
        "version": 2,
        "services": [
            {"id": "s1", "name": "Evil", "protocol": "javascript", "host": "alert(1)//", "port": 99999, "path": "/ok",
             "icon": "<img src=x>", "health_type": "ping", "health_url": "javascript:alert(1)", "url": "x"},
            {"id": "s2", "name": "Fine", "protocol": "https", "host": "fine.lan", "port": None, "path": "docs", "url": "x"},
        ],
        "dashboards": [{
            "id": "d1", "name": "Old", "position": 0, "columns": 24, "row_height": 40, "settings": {},
            "widgets": [
                {"id": "w1", "dashboard_id": "d1", "type": "link", "x": 0, "y": 0, "w": 4, "h": 3,
                 "config": {"url": "javascript:alert(1)", "label": "keep"}},
                {"id": "w2", "dashboard_id": "d1", "type": "links", "x": 4, "y": 0, "w": 4, "h": 3,
                 "config": {"links": [{"title": "a", "url": "obsidian://x"}, {"title": "b", "url": "vbscript:x"}]}},
            ],
        }],
        "integrations": [], "settings": {},
    }


def test_config_import_of_an_old_bundle_succeeds_and_neutralises(client):
    res = client.post("/api/config/import?replace=true", json=_old_bundle(), headers=HEADERS)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"counts", "warnings"} and body["counts"]["services"] == 2  # a summary, never the bundle
    body = {**client.get("/api/config/export").json(), "warnings": body["warnings"]}
    s1 = next(s for s in body["services"] if s["id"] == "s1")
    assert s1["protocol"] == "http" and s1["host"] == "" and s1["port"] is None and s1["icon"] == ""
    assert s1["health_url"] == "" and s1["health_type"] == "ping" and s1["path"] == "/ok"
    s2 = next(s for s in body["services"] if s["id"] == "s2")
    assert (s2["protocol"], s2["host"], s2["path"]) == ("https", "fine.lan", "/docs")
    cfgs = {w["id"]: w["config"] for w in body["dashboards"][0]["widgets"]}
    assert cfgs["w1"] == {"url": "", "label": "keep"}
    assert [l["url"] for l in cfgs["w2"]["links"]] == ["obsidian://x", ""]
    warnings = " | ".join(body["warnings"])
    for needle in ("protocol was reset", "host was reset", "port was reset", "icon was reset", "health_url was reset",
                   "config.url was emptied", "config.links[1].url was emptied"):
        assert needle in warnings, needle
    assert "javascript" not in warnings.replace("the javascript: scheme", "")  # never echoes the value
    assert client.get("/api/services").status_code == 200


def test_clean_bundle_has_no_warnings_and_export_has_no_warnings_key(client):
    client.post("/api/services", json=SVC, headers=HEADERS)
    exported = client.get("/api/config/export").json()
    assert "warnings" not in exported
    res = client.post("/api/config/import?replace=true", json=exported, headers=HEADERS)
    assert res.status_code == 200 and res.json()["warnings"] == []


def test_backup_restore_of_an_old_backup_is_not_blocked(client, monkeypatch):
    from app import backups
    monkeypatch.setattr(backups, "read_backup", lambda name: _old_bundle())
    monkeypatch.setattr(backups, "create_backup", lambda label: {"name": label})
    res = client.post("/api/backups/old.json/restore", headers=HEADERS)
    assert res.status_code == 200, res.text
    assert res.json()["warnings"]
    assert client.get("/api/services/s1").json()["protocol"] == "http"


# ---------------------------------------------------------------- the shared module
def test_repair_always_yields_a_valid_service():
    fields = {"name": "", "protocol": "x", "host": "a b", "port": 0, "path": "/a b", "icon": "<", "category": "c" * 99,
              "description": "d" * 999, "color": "<", "health_type": "?", "health_url": "ftp://x", "health_interval": 0,
              "depends_on": ["ok", "", "x" * 99]}
    notes = validation.repair_service(fields)
    assert len(notes) == 12 and not validation.field_errors({k: v for k, v in fields.items() if k != "host"})
    assert fields["depends_on"] == ["ok"] and fields["name"] == "Service"
