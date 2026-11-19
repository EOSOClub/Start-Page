"""Review fixes: layout validation (B1), service host/path/URL rules and lossless restore (B2),
widget URL denylist (S11), import/restore summaries (B3), page import shape rules, response headers."""
import contextlib
import json

import pytest

from app import models, schemas, validation
from conftest import HEADERS

JSON_HEADERS = {**HEADERS, "content-type": "application/json"}


def mkpage(client, **over):
    body = {"name": "P", "columns": 24, **over}
    r = client.post("/api/dashboards", json=body, headers=HEADERS)
    assert r.status_code == 201, r.text
    return r.json()


def mkwidget(client, page_id, **over):
    body = {"type": "clock", "x": 0, "y": 0, "w": 4, "h": 2, **over}
    return client.post(f"/api/dashboards/{page_id}/widgets", json=body, headers=HEADERS)


def all_reads_ok(client, page_id):
    assert client.get(f"/api/dashboards/{page_id}").status_code == 200
    assert client.get("/api/config/export").status_code == 200
    assert client.get(f"/api/dashboards/{page_id}/export").status_code == 200


# ---------------------------------------------------------------- B1: layout validation
@pytest.mark.parametrize("bp", ["lg", "sm"])
@pytest.mark.parametrize("bad", [
    {"y": 200000}, {"x": -1}, {"w": 0}, {"h": -2}, {"x": 5000}, {"w": 5000},
])
def test_layout_save_refuses_out_of_range_values_before_writing(client, bp, bad):
    page = mkpage(client)
    wid = mkwidget(client, page["id"]).json()["id"]
    item = {"id": wid, "x": 0, "y": 0, "w": 2, "h": 2, **bad}
    r = client.put(f"/api/dashboards/{page['id']}/layout?breakpoint={bp}", json=[item], headers=HEADERS)
    assert r.status_code == 422, r.text
    all_reads_ok(client, page["id"])
    stored = client.get(f"/api/dashboards/{page['id']}").json()["widgets"][0]
    assert stored["x"] == 0 and stored["y"] == 0 and stored["w"] == 4


@pytest.mark.parametrize("bp", ["lg", "sm"])
def test_layout_save_refuses_nan_and_infinity(client, bp):
    page = mkpage(client)
    wid = mkwidget(client, page["id"]).json()["id"]
    for token in ("NaN", "Infinity", "-Infinity"):
        raw = '[{"id": "%s", "x": %s, "y": 0, "w": 2, "h": 2}]' % (wid, token)
        r = client.put(f"/api/dashboards/{page['id']}/layout?breakpoint={bp}", content=raw, headers=JSON_HEADERS)
        assert r.status_code == 422, (token, r.text)
    all_reads_ok(client, page["id"])


def test_phone_layout_must_fit_the_phone_grid_on_create_and_patch(client):
    page = mkpage(client, settings={"columns_sm": 4})
    r = mkwidget(client, page["id"], layout_sm={"x": 900, "y": 0, "w": 50, "h": 2})
    assert r.status_code == 422, r.text
    wid = mkwidget(client, page["id"]).json()["id"]
    r = client.patch(f"/api/widgets/{wid}", json={"layout_sm": {"x": 3, "y": 0, "w": 2, "h": 2}}, headers=HEADERS)
    assert r.status_code == 422 and "phone grid" in r.text
    ok = client.patch(f"/api/widgets/{wid}", json={"layout_sm": {"x": 2, "y": 0, "w": 2, "h": 2}}, headers=HEADERS)
    assert ok.status_code == 200 and ok.json()["layout_sm"]["x"] == 2
    all_reads_ok(client, page["id"])


def test_widget_writes_refuse_bad_numbers_and_overflow(client):
    page = mkpage(client)
    assert mkwidget(client, page["id"], y=10**9).status_code == 422
    assert mkwidget(client, page["id"], w=0).status_code == 422
    assert mkwidget(client, page["id"], x=22, w=6).status_code == 422  # past the 24 columns
    wid = mkwidget(client, page["id"]).json()["id"]
    for body in ({"x": -1}, {"w": 1e9}, {"h": 0}, {"x": 22, "w": 6}):
        assert client.patch(f"/api/widgets/{wid}", json=body, headers=HEADERS).status_code == 422, body
    all_reads_ok(client, page["id"])


def test_page_file_with_a_bad_phone_layout_is_refused_and_writes_nothing(client, session):
    doc = {"format": "startpage-page", "version": 1, "name": "X", "widgets": [
        {"type": "clock", "x": 0, "y": 0, "w": 4, "h": 2, "config": {}, "layout_sm": {"x": "abc", "junk": [1, 2]}}]}
    for _ in range(2):  # a retry must not create a second (broken) page
        r = client.post("/api/dashboards/import", json={"page": doc}, headers=HEADERS)
        assert r.status_code == 422, r.text
    assert session.query(models.Dashboard).count() == 0
    assert client.get("/api/config/export").status_code == 200


def test_config_import_refuses_bad_layouts_before_writing(client, session):
    bundle = {"services": [], "integrations": [], "settings": {}, "dashboards": [
        {"id": "d1", "name": "D", "widgets": [{"id": "w1", "type": "clock", "x": 0, "y": 10**7, "w": 2, "h": 2}]}]}
    r = client.post("/api/config/import", json=bundle, headers=HEADERS)
    assert r.status_code == 422, r.text
    assert session.query(models.Dashboard).count() == 0


def test_config_import_drops_a_phone_layout_that_does_not_fit(client):
    bundle = {"services": [], "integrations": [], "settings": {}, "dashboards": [
        {"id": "d1", "name": "D", "settings": {"columns_sm": 2}, "widgets": [
            {"id": "w1", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 2, "layout_sm": {"x": 1, "y": 0, "w": 2, "h": 2}}]}]}
    r = client.post("/api/config/import", json=bundle, headers=HEADERS)
    assert r.status_code == 200 and any("phone position" in w for w in r.json()["warnings"])
    assert client.get("/api/dashboards/d1").json()["widgets"][0]["layout_sm"] is None


def test_reading_a_pre_broken_row_never_500s_and_startup_repairs_it(client, session, monkeypatch):
    from app import main
    page = mkpage(client)
    session.get(models.Dashboard, page["id"]).settings = {"columns_sm": 99, "phone_layout": "weird"}
    good = mkwidget(client, page["id"]).json()["id"]
    session.add_all([
        models.Widget(id="bad1", dashboard_id=page["id"], type="clock", x=1e12, y=-5, w=-3, h=1e12,
                      layout_sm={"x": 900, "y": 1e9, "w": 50, "h": 2}),
        models.Widget(id="bad2", dashboard_id=page["id"], type="clock", x=0, y=0, w=4, h=3,
                      layout_sm={"x": "abc", "junk": [1, 2]}),
        models.Widget(id="bad3", dashboard_id=page["id"], type="clock", x=0, y=0, w=4, h=3, layout_sm={"x": float("nan"), "y": 0, "w": 1, "h": 1}),
    ])
    session.commit()
    all_reads_ok(client, page["id"])
    ws = {w["id"]: w for w in client.get(f"/api/dashboards/{page['id']}").json()["widgets"]}
    assert ws["bad1"]["x"] == schemas.X_MAX and ws["bad1"]["y"] == 0 and ws["bad1"]["w"] > 0
    assert ws["bad1"]["layout_sm"]["x"] <= schemas.X_MAX
    assert ws["bad2"]["layout_sm"] is None and ws["bad3"]["layout_sm"] is None
    assert client.get(f"/api/dashboards/{page['id']}").json()["settings"] == {}
    assert client.post("/api/widgets/bad2/duplicate", headers=HEADERS).status_code == 201

    monkeypatch.setattr(main, "SessionLocal", lambda: contextlib.nullcontext(session))
    main._repair_rows()
    row = session.get(models.Widget, "bad1")
    assert row.x == schemas.X_MAX and row.y == 0 and row.w > 0 and row.h == schemas.Y_MAX
    assert session.get(models.Widget, "bad2").layout_sm is None
    assert session.get(models.Dashboard, page["id"]).settings == {}
    assert session.get(models.Widget, good).x == 0


# ---------------------------------------------------------------- B2: host / path / URL rules
@pytest.mark.parametrize("host,stored", [
    ("nas.local", "nas.local"), ("nas.local.", "nas.local."), ("[fd00::5]", "fd00::5"), ("FD00:0:0::5", "fd00::5"),
    ("192.168.1.5", "192.168.1.5"), ("bücher.example", "xn--bcher-kva.example"),
])
def test_accepted_hosts_are_stored_canonically(host, stored):
    assert validation.normalise_host(host) == stored


@pytest.mark.parametrize("host", ["a b", "a@b", "a/b", "a\\b", "a:80", "x\x00y", "", "-a", "a..b", "[a]", "fe80::1%eth0", "a?b", "a#b"])
def test_dangerous_or_malformed_hosts_are_refused(host):
    assert validation.normalise_host(host) is None


def test_paths_with_query_fragment_and_non_ascii_are_encoded_not_refused():
    assert validation.clean_path("/web/index.html#!/") == "/web/index.html#!/"
    assert validation.clean_path("admin?x=1") == "/admin?x=1"
    assert validation.clean_path("/a b/é") == "/a%20b/%C3%A9"
    assert validation.clean_path(validation.clean_path("/a b/é")) == "/a%20b/%C3%A9"  # idempotent
    assert validation.clean_path("/a\nb") is None and validation.clean_path("/a\\b") is None


def test_health_url_accepts_ipv6_and_userinfo_but_not_other_schemes():
    ok = validation.parse_url("http://user:pw@[fd00::5]:8080/x?y=1", allow_userinfo=True)[0]
    assert ok["host"] == "fd00::5" and ok["port"] == 8080
    assert validation.parse_url("http://u:p@h/x")[0] is None  # not for links
    assert validation.parse_url("ftp://h/x")[0] is None and validation.parse_url("http://h:0/")[0] is None


def test_service_url_brackets_ipv6_and_refuses_bad_protocols():
    s = models.Service(name="a", protocol="http", host="fd00::5", port=8080, path="/admin?x=1")
    assert s.url == "http://[fd00::5]:8080/admin?x=1"
    assert models.Service(name="a", protocol="javascript", host="h", port=None, path="").url == ""
    assert models.Service(name="a", protocol="HTTPS", host="h", port=443, path="").url == "https://h"


VARIED = [
    {"name": "Plex", "host": "nas.local", "port": 32400, "path": "/web/index.html#!/", "health_url": "http://user:pw@nas.local/identity"},
    {"name": "V6", "host": "[fd00::5]", "port": 8080, "path": "/admin?x=1", "health_url": "http://[fd00::5]:8080/health"},
    {"name": "IDN", "host": "bücher.example", "path": "/ü ber", "protocol": "https"},
    {"name": "Dot", "host": "router.lan.", "port": 8443, "protocol": "https"},
    {"name": "Long", "host": "h" + "a" * 60 + ".example", "path": "/" + "p" * 300, "description": "d" * 300, "category": "c" * 60},
    {"name": "Proxmox", "host": "10.0.0.2", "port": 8006, "protocol": "https", "path": "/#v1:0:18:4::::::"},
]


def test_backup_and_restore_of_varied_services_is_lossless(client):
    for body in VARIED:
        r = client.post("/api/services", json={**body}, headers=HEADERS)
        assert r.status_code == 201, (body["name"], r.text)
    before = sorted(client.get("/api/services").json(), key=lambda s: s["id"])
    bundle = client.get("/api/config/export").json()
    res = client.post("/api/config/import?replace=true", json=bundle, headers=HEADERS)
    assert res.status_code == 200 and res.json()["warnings"] == [], res.text
    after = sorted(client.get("/api/services").json(), key=lambda s: s["id"])
    assert after == before
    assert not any(s["metadata"].get("import_original") for s in after)
    by_name = {s["name"]: s for s in after}
    assert by_name["V6"]["url"] == "http://[fd00::5]:8080/admin?x=1"
    assert by_name["Plex"]["health_url"] == "http://user:pw@nas.local/identity"
    assert by_name["IDN"]["host"] == "xn--bcher-kva.example"


def test_restore_salvages_legacy_host_port_and_keeps_the_original(client):
    bundle = {"dashboards": [], "integrations": [], "settings": {}, "services": [
        {"id": "s1", "name": "A", "host": "nas.local:32400", "url": ""},
        {"id": "s2", "name": "B", "host": "nas.local:9", "port": 8080, "url": ""},
        {"id": "s3", "name": "C", "host": "evil host", "url": ""},
        {"id": "s4", "name": "D", "host": "ok.lan", "protocol": "HTTPS", "path": "/x", "url": ""},
    ]}
    res = client.post("/api/config/import", json=bundle, headers=HEADERS)
    assert res.status_code == 200, res.text
    s = {x["id"]: x for x in client.get("/api/services").json()}
    assert (s["s1"]["host"], s["s1"]["port"]) == ("nas.local", 32400)
    assert s["s1"]["metadata"]["import_original"]["host"] == "nas.local:32400"
    assert (s["s2"]["host"], s["s2"]["port"]) == ("nas.local", 8080)  # the port field wins
    assert s["s3"]["host"] == "" and s["s3"]["metadata"]["import_original"]["host"] == "evil host"
    assert s["s4"]["protocol"] == "https"
    assert len(res.json()["warnings"]) >= 3


def test_startup_repair_fixes_a_stored_javascript_protocol(client, session, monkeypatch):
    from app import main
    session.add(models.Service(id="bad", name="x", protocol="javascript", host="h", port=None, path=""))
    session.add(models.Service(id="hp", name="y", protocol="http", host="nas:81", port=None, path=""))
    session.commit()
    out = client.get("/api/services/bad").json()
    assert out["url"] == ""  # defensive on output even before the repair
    monkeypatch.setattr(main, "SessionLocal", lambda: contextlib.nullcontext(session))
    main._repair_rows()
    assert session.get(models.Service, "bad").protocol == "http"
    assert (session.get(models.Service, "hp").host, session.get(models.Service, "hp").port) == ("nas", 81)


# ---------------------------------------------------------------- S11: widget URL denylist
@pytest.mark.parametrize("cfg", [
    {"url": ["javascript:alert(1)"]}, {"URL": "javascript:alert(1)"}, {"items": [{"target": "javascript:alert(1)"}]},
    {"links": [{"Href": " JaVaScript:x"}]}, {"src": {"a": "vbscript:x"}}, {"link": "data:text/html,x"},
])
def test_widget_writes_refuse_dangerous_urls_anywhere_under_url_keys(client, cfg):
    page = mkpage(client)
    assert mkwidget(client, page["id"], type="link", config=cfg).status_code == 422
    assert mkwidget(client, page["id"], type="link", config={"url": "obsidian://x", "label": "javascript: in text"}).status_code == 201


# ---------------------------------------------------------------- B3: summaries
def test_import_returns_a_summary_without_any_secret(client, session):
    session.add(models.Integration(id="i1", name="J", type="jellyfin", config={"url": "http://x", "api_key": "TOPSECRET"}))
    session.commit()
    bundle = client.get("/api/config/export").json()
    res = client.post("/api/config/import", json=bundle, headers=HEADERS)
    assert res.status_code == 200 and "TOPSECRET" not in res.text
    assert set(res.json()) == {"counts", "warnings"}
    assert res.json()["counts"] == {"services": 0, "dashboards": 0, "widgets": 0, "integrations": 1}


# ---------------------------------------------------------------- page import / export
def page_file(**over):
    doc = {"format": "startpage-page", "version": 1, "name": "P", "columns": 24, "row_height": 40, "settings": {},
           "widgets": [], "services": [], "integrations": []}
    doc.update(over)
    return doc


def w(type, config, **over):
    return {"type": type, "x": 0, "y": 0, "w": 4, "h": 2, "config": config, **over}


def post_import(client, doc, preview=False, **opts):
    path = "/api/dashboards/import/preview" if preview else "/api/dashboards/import"
    return client.post(path, json={"page": doc, **opts}, headers=HEADERS)


SVC = {"ref": "s1", "name": "A", "host": "10.0.0.1", "port": 80}
INT = {"ref": "i1", "type": "ics", "name": "Cal"}


@pytest.mark.parametrize("widget", [
    w("calendar", {"integration_ids": "i1"}),
    w("group", {"mode": "ids", "service_ids": "s1"}),
    w("uptime", {"service_ids": 5}),
    w("custom", {"integration_id": ["i1"]}),
    w("text", {"content": {"a": 1}}),
    w("clock", {"showSeconds": "yes"}),
    w("clock", {"timezone": 5}),
    w("weather", {"units": "kelvin"}),
    w("bookmarks", {"columns": "3"}),
])
def test_page_import_enforces_config_shapes(client, session, widget):
    for preview in (True, False):
        r = post_import(client, page_file(widgets=[widget], services=[SVC], integrations=[INT]), preview=preview)
        assert r.status_code == 422, (preview, r.text)
    assert session.query(models.Dashboard).count() == 0


def test_page_import_drops_unknown_config_keys_with_a_warning_in_preview_and_import(client):
    doc = page_file(widgets=[w("custom", {"html": "<b>x</b>", "extra": {"integration_id": "i1"}})])
    pre = post_import(client, doc, preview=True).json()
    real = post_import(client, doc).json()
    assert any("extra" in x for x in pre["warnings"]) and pre["warnings"] == real["warnings"]
    assert "extra" not in real["dashboard"]["widgets"][0]["config"]


def test_error_messages_do_not_echo_huge_values(client):
    doc = page_file(widgets=[w("calendar", {"integration_ids": ["x" * 50000]})], integrations=[INT])
    r = post_import(client, doc)
    assert r.status_code == 422 and len(r.text) < 2000


def test_deeply_nested_body_is_422_not_500(client):
    r = client.post("/api/dashboards/import", content="[" * 200000, headers=JSON_HEADERS)
    assert r.status_code == 422, r.status_code


def test_phone_settings_round_trip_and_unfitting_phone_layout_is_dropped(client):
    page = mkpage(client, settings={"columns_sm": 3, "phone_layout": "custom", "gap": 4})
    wid = mkwidget(client, page["id"], layout_sm={"x": 1, "y": 0, "w": 2, "h": 2}).json()["id"]
    doc = client.get(f"/api/dashboards/{page['id']}/export").json()
    assert doc["settings"]["columns_sm"] == 3 and doc["settings"]["phone_layout"] == "custom"
    new = post_import(client, doc).json()
    assert new["dashboard"]["settings"]["columns_sm"] == 3
    assert new["dashboard"]["widgets"][0]["layout_sm"]["x"] == 1
    doc["settings"]["columns_sm"] = 2  # now x 1 + w 2 does not fit
    again = post_import(client, doc).json()
    assert again["dashboard"]["widgets"][0]["layout_sm"] is None
    assert any("phone position" in x for x in again["warnings"])
    doc["settings"]["columns_sm"] = 99
    assert any("columns_sm" in x for x in post_import(client, doc).json()["warnings"])
    assert wid


def test_secret_scan_finds_bearer_path_tokens_and_code_literals(client):
    token = "a1B2c3D4e5F6g7H8i9J0k1L2"
    page = mkpage(client)
    for cfg_type, cfg in (
        ("text", {"content": "curl -H 'Authorization: Bearer abcdef123456789'"}),
        ("link", {"url": f"https://hooks.example/api/webhook/{token}"}),
        ("custom", {"js": f"const key = '{token}{token}';"}),
    ):
        assert mkwidget(client, page["id"], type=cfg_type, config=cfg).status_code == 201
    doc = client.get(f"/api/dashboards/{page['id']}/export").json()
    text = "\n".join(doc["warnings"])
    assert "Bearer" in text and "URL path" in text and "string literal" in text
    assert token not in text and "abcdef123456789" not in text


def test_export_has_a_contents_summary(client):
    page = mkpage(client)
    mkwidget(client, page["id"], type="text", config={"content": "hello"})
    mkwidget(client, page["id"], type="custom", config={"js": "1"}, y=3)
    c = client.get(f"/api/dashboards/{page['id']}/export").json()["contents"]
    assert c["widget_count"] == 2 and c["widget_types"] == {"custom": 1, "text": 1}
    assert c["includes"]["text_content"] is True and c["includes"]["custom_code"] is True
    assert set(c) == {"widget_count", "widget_types", "service_count", "integration_count", "includes"}


def test_two_file_services_at_one_address_are_created_once(client, session):
    doc = page_file(services=[{**SVC, "ref": "a", "name": "One"}, {**SVC, "ref": "b", "name": "Two"}])
    body = post_import(client, doc).json()
    assert session.query(models.Service).count() == 1
    assert len(body["created_services"]) == 1 and len(body["matched_services"]) == 1


def test_name_only_never_matches_a_service_at_another_address(client, session):
    session.add(models.Service(name="NAS", host="10.9.9.9", port=80))
    session.commit()
    body = post_import(client, page_file(services=[{**SVC, "name": "nas"}])).json()
    assert body["matched_services"] == [] and len(body["created_services"]) == 1


def test_calendar_with_the_older_integration_id_key_is_exported_as_a_ref(client, session):
    session.add(models.Integration(id="ics1", name="Cal", type="ics", config={"url": "http://x"}))
    session.commit()
    page = mkpage(client)
    assert mkwidget(client, page["id"], type="calendar", config={"integration_id": "ics1"}).status_code == 201
    doc = client.get(f"/api/dashboards/{page['id']}/export").json()
    assert doc["widgets"][0]["config"] == {"integration_ids": ["i1"]} and "ics1" not in json.dumps(doc)


def test_shipped_media_template_has_no_widget_that_needs_missing_services(client):
    tpl = client.get("/api/templates/media").json()
    assert not any(x["type"] == "group" for x in tpl["widgets"])


# ---------------------------------------------------------------- headers / favicon
def test_responses_carry_hardening_headers(client):
    r = client.get("/api/settings")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


def test_favicon_only_serves_image_types(client, monkeypatch, tmp_path):
    from app.routers import favicons
    monkeypatch.setattr(favicons, "CACHE_DIR", tmp_path)
    (tmp_path / "k.img").write_bytes(b"<html><script>alert(1)</script>")

    async def fake_meta(url, **kw):
        return {"type": "text/html"}, "k", url

    monkeypatch.setattr(favicons, "_meta", fake_meta)
    r = client.get("/api/favicon?url=http://x.example")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff" and "attachment" in r.headers["content-disposition"]


def test_health_url_credentials_are_stripped_for_guests_only(client, monkeypatch):
    from app import auth
    from app.routers import services

    assert services.strip_userinfo("http://user:pw@nas.local/identity") == "http://nas.local/identity"
    assert services.strip_userinfo("http://[fd00::5]:80/x") == "http://[fd00::5]:80/x"
    svc = client.post("/api/services", json={"name": "P", "host": "nas.local", "health_url": "http://user:pw@nas.local/identity"}, headers=HEADERS).json()
    assert "pw@" in client.get(f"/api/services/{svc['id']}").json()["health_url"]  # the owner still sees it
    monkeypatch.setattr(auth, "is_guest", lambda request: True)
    assert "pw" not in client.get(f"/api/services/{svc['id']}").text
    assert "pw@" not in client.get("/api/services").text
