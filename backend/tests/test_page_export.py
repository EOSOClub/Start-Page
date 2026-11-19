"""Single-page export / import / preview (`startpage-page` v1): the portable format, id remapping,
service and integration matching, untrusted-file validation, auth rules and transaction safety."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import models, pages
from app.main import app

from conftest import HEADERS

SECRET = "SUPERSECRETKEY123"


def post(client, path, body):
    return client.post(path, json=body, headers=HEADERS)


def add_service(session, name="Jellyfin", host="10.0.0.5", port=8096, **kw):
    s = models.Service(name=name, host=host, port=port, **kw)
    session.add(s)
    session.commit()
    return s


def add_integration(session, type="jellyfin", name="My Jellyfin"):
    i = models.Integration(name=name, type=type, config={"url": "http://x", "api_key": SECRET, "token": SECRET})
    session.add(i)
    session.commit()
    return i


def make_page(client, name="Source", widgets=()):
    d = post(client, "/api/dashboards", {"name": name, "columns": 24, "row_height": 40, "settings": {"gap": 8, "snap": "cell"}}).json()
    for w in widgets:
        assert post(client, f"/api/dashboards/{d['id']}/widgets", w).status_code == 201
    return d


def widget(type, x, y, w, h, config=None, service_id=None):
    return {"type": type, "x": x, "y": y, "w": w, "h": h, "config": config or {}, "service_id": service_id}


def page_file(**over):
    doc = {"format": "startpage-page", "version": 1, "name": "P", "columns": 24, "row_height": 40, "settings": {},
           "widgets": [], "services": [], "integrations": []}
    doc.update(over)
    return doc


def do_import(client, page, **opts):
    return post(client, "/api/dashboards/import", {"page": page, **opts})


def widgets_of(client, dashboard_id):
    return sorted(client.get(f"/api/dashboards/{dashboard_id}").json()["widgets"], key=lambda w: (w["y"], w["x"], w["type"]))


# ---------- export ----------
def test_export_uses_refs_never_ids_and_no_integration_config(client, session):
    svc = add_service(session, category="Media")
    other = add_service(session, name="Sonarr", host="10.0.0.6", port=8989, category="Media")
    integ = add_integration(session)
    d = make_page(client, widgets=[
        widget("service", 0, 0, 4, 2, service_id=svc.id),
        widget("group", 4, 0, 8, 4, {"mode": "ids", "service_ids": [svc.id, other.id, "gone"]}),
        widget("jellyfin", 0, 4, 8, 5, {"integration_id": integ.id, "title": "J"}),
        widget("topology", 12, 0, 6, 6, {"root": svc.id}),
    ])
    r = client.get(f"/api/dashboards/{d['id']}/export")
    assert r.status_code == 200
    doc = r.json()
    text = json.dumps(doc)
    assert (doc["format"], doc["version"], doc["name"], doc["columns"], doc["row_height"]) == ("startpage-page", 1, "Source", 24, 40)
    assert svc.id not in text and other.id not in text and integ.id not in text
    assert SECRET not in text and "api_key" not in text
    assert doc["integrations"] == [{"ref": "i1", "type": "jellyfin", "name": "My Jellyfin"}]
    refs = {s["ref"]: s["name"] for s in doc["services"]}
    assert set(refs.values()) == {"Jellyfin", "Sonarr"}
    by_type = {w["type"]: w for w in doc["widgets"]}
    assert by_type["service"]["service_ref"] in refs
    assert sorted(by_type["group"]["config"]["service_ids"]) == sorted(refs)  # dangling id dropped
    assert by_type["jellyfin"]["config"]["integration_id"] == "i1"
    assert by_type["topology"]["config"]["root"] in refs
    assert not any("service_id" in w for w in doc["widgets"])
    assert doc["settings"] == {"gap": 8, "snap": "cell"}


def test_export_includes_category_services_for_category_widgets(client, session):
    add_service(session, name="A", host="a", port=1, category="Media")
    add_service(session, name="B", host="b", port=2, category="Other")
    d = make_page(client, widgets=[widget("group", 0, 0, 8, 4, {"mode": "category", "category": "Media"})])
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    assert [s["name"] for s in doc["services"]] == ["A"]


def test_export_404_and_services_are_cleaned(client, session):
    assert client.get("/api/dashboards/nope/export").status_code == 404
    svc = add_service(session, path="/x?token=abc&ok=1", health_url="http://u:p@h/health?apikey=zzz")
    d = make_page(client, widgets=[widget("service", 0, 0, 4, 2, service_id=svc.id)])
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    s = doc["services"][0]
    assert "abc" not in json.dumps(doc) and "zzz" not in json.dumps(doc) and ":p@" not in json.dumps(doc)
    assert s["path"] == "/x?ok=1"  # a path may hold a query; only the secret-looking parameter goes
    assert any("Service" in w for w in doc["warnings"])


def test_export_warns_about_secrets_in_widget_configs_without_leaking_them(client):
    d = make_page(client, widgets=[
        widget("iframe", 0, 0, 8, 6, {"url": "http://admin:hunter2@grafana.lan/d/1"}),
        widget("link", 8, 0, 4, 2, {"url": "https://x.example/?token=abc123&a=1", "name": "n"}),
        widget("image", 12, 0, 4, 4, {"url": "https://x.example/p.png?apikey=k"}),
        widget("text", 16, 0, 6, 4, {"content": "see https://x.example/?access_token=zz"}),
        widget("clock", 0, 6, 4, 2, {"label": "fine https://example.com/?page=2"}),
    ])
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    warnings = "\n".join(doc["warnings"])
    for leaked in ("hunter2", "abc123", "access_token=zz"):
        assert leaked not in warnings
    assert len([w for w in doc["warnings"] if "config." in w]) == 4
    assert "Widget" in warnings and "clock" not in warnings
    # exported as is, not silently dropped
    assert "token=abc123" in json.dumps(doc["widgets"])


def test_export_flags_custom_widgets(client):
    d = make_page(client, widgets=[widget("custom", 0, 0, 6, 4, {"html": "<b>hi</b>"})])
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    assert any("custom code" in w for w in doc["warnings"])


# ---------- round trip ----------
def test_export_then_import_reproduces_the_page(client, session):
    svc = add_service(session)
    integ = add_integration(session)
    d = make_page(client, widgets=[
        widget("service", 0, 0, 4, 2, service_id=svc.id),
        widget("group", 4, 0, 8, 4, {"mode": "ids", "service_ids": [svc.id], "title": "G"}),
        widget("jellyfin", 0, 4, 8, 5, {"integration_id": integ.id, "title": "J", "limit": 3}),
        widget("heading", 0, 9, 12, 1, {"text": "Hello", "bar": True}),
    ])
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    r = do_import(client, doc, name="Copy")
    assert r.status_code == 201
    body = r.json()
    assert body["dashboard"]["name"] == "Copy" and body["dashboard"]["id"] != d["id"]
    assert [s["name"] for s in body["matched_services"]] == ["Jellyfin"] and body["created_services"] == []
    assert body["unresolved_integrations"] == []
    old, new = widgets_of(client, d["id"]), widgets_of(client, body["dashboard"]["id"])
    assert len(old) == len(new) == 4
    assert {w["id"] for w in old}.isdisjoint({w["id"] for w in new})
    for a, b in zip(old, new):
        assert (a["type"], a["x"], a["y"], a["w"], a["h"]) == (b["type"], b["x"], b["y"], b["w"], b["h"])
        assert a["service_id"] == b["service_id"]
        assert a["config"] == b["config"]  # the ids map back to the same existing rows
    page = client.get(f"/api/dashboards/{body['dashboard']['id']}").json()
    assert (page["columns"], page["row_height"], page["settings"]) == (24, 40, {"gap": 8, "snap": "cell"})
    assert len(client.get("/api/dashboards").json()) == 2  # a new page; the source is untouched


def test_import_into_an_empty_install_creates_services_and_leaves_integration_unset(client, session):
    svc = add_service(session)
    integ = add_integration(session)
    d = make_page(client, widgets=[
        widget("service", 0, 0, 4, 2, service_id=svc.id),
        widget("jellyfin", 0, 4, 8, 5, {"integration_id": integ.id}),
    ])
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    session.query(models.Widget).delete()
    session.query(models.Service).delete()
    session.query(models.Integration).delete()
    session.commit()
    body = do_import(client, doc).json()
    assert [s["name"] for s in body["created_services"]] == ["Jellyfin"]
    new_service = session.query(models.Service).one()
    assert (new_service.host, new_service.port) == ("10.0.0.5", 8096)
    widgets = {w["type"]: w for w in body["dashboard"]["widgets"]}
    assert widgets["service"]["service_id"] == new_service.id
    assert "integration_id" not in widgets["jellyfin"]["config"]
    [un] = body["unresolved_integrations"]
    assert (un["ref"], un["type"], un["name"]) == ("i1", "jellyfin", "My Jellyfin")
    assert un["widget_ids"] == [widgets["jellyfin"]["id"]]


# ---------- service modes ----------
def svc_doc(**s):
    entry = {"ref": "a", "name": "Jellyfin", "protocol": "http", "host": "10.0.0.5", "port": 8096}
    entry.update(s)
    return page_file(services=[entry], widgets=[
        {"type": "service", "x": 0, "y": 0, "w": 4, "h": 2, "config": {}, "service_ref": "a"},
        {"type": "group", "x": 4, "y": 0, "w": 8, "h": 4, "config": {"mode": "ids", "service_ids": ["a"]}},
    ])


def test_service_mode_match_by_host_and_port(client, session):
    existing = add_service(session, name="Totally different", host="10.0.0.5", port=8096)
    body = do_import(client, svc_doc(), service_mode="match").json()
    assert [(s["id"], s["matched_by"]) for s in body["matched_services"]] == [(existing.id, "address")]
    assert body["created_services"] == [] and session.query(models.Service).count() == 1
    assert body["dashboard"]["widgets"][0]["service_id"] == existing.id


def test_service_mode_match_by_name_case_insensitive(client, session):
    existing = add_service(session, name="JELLYFIN", host=svc_doc()["services"][0]["host"], port=1)
    body = do_import(client, svc_doc(), service_mode="match").json()
    assert [(s["id"], s["matched_by"]) for s in body["matched_services"]] == [(existing.id, "name")]


def test_service_mode_match_creates_the_rest(client, session):
    add_service(session, name="Other", host="o", port=1)
    body = do_import(client, svc_doc()).json()  # "match" is the default
    assert len(body["created_services"]) == 1 and session.query(models.Service).count() == 2


def test_service_mode_create_always_creates(client, session):
    add_service(session)
    body = do_import(client, svc_doc(), service_mode="create").json()
    assert len(body["created_services"]) == 1 and body["matched_services"] == []
    assert session.query(models.Service).count() == 2


def test_service_mode_skip_leaves_services_unlinked(client, session):
    body = do_import(client, svc_doc(), service_mode="skip").json()
    assert session.query(models.Service).count() == 0
    service_w, group_w = body["dashboard"]["widgets"][0], body["dashboard"]["widgets"][1]
    assert service_w["service_id"] is None
    assert group_w["config"].get("service_ids", []) == []
    assert body["skipped_services"] == [{"ref": "a", "name": "Jellyfin"}]


def test_created_services_keep_their_dependency_links(client, session):
    doc = page_file(services=[
        {"ref": "a", "name": "A", "host": "a", "port": 1},
        {"ref": "b", "name": "B", "host": "b", "port": 2, "depends_on": ["a"]},
    ])
    do_import(client, doc, service_mode="create")
    a, b = (session.query(models.Service).filter_by(name=n).one() for n in "AB")
    assert b.depends_on == [a.id]


# ---------- integrations ----------
def int_doc(*types):
    return page_file(
        integrations=[{"ref": f"r{n}", "type": t, "name": t.title()} for n, t in enumerate(types)],
        widgets=[{"type": t, "x": 0, "y": n * 5, "w": 8, "h": 5, "config": {"integration_id": f"r{n}"}} for n, t in enumerate(types)],
    )


def test_integration_auto_matches_the_single_existing_one(client, session):
    one = add_integration(session, "jellyfin")
    body = do_import(client, int_doc("jellyfin")).json()
    assert body["unresolved_integrations"] == []
    assert body["dashboard"]["widgets"][0]["config"]["integration_id"] == one.id
    assert body["matched_integrations"][0]["via"] == "auto"


def test_integration_not_auto_matched_when_two_exist(client, session):
    add_integration(session, "jellyfin", "A")
    add_integration(session, "jellyfin", "B")
    body = do_import(client, int_doc("jellyfin")).json()
    assert [u["ref"] for u in body["unresolved_integrations"]] == ["r0"]
    assert "integration_id" not in body["dashboard"]["widgets"][0]["config"]


def test_integration_map_wins_and_none_means_later(client, session):
    a = add_integration(session, "jellyfin", "A")
    add_integration(session, "jellyfin", "B")
    body = do_import(client, int_doc("jellyfin"), integration_map={"r0": a.id}).json()
    assert body["dashboard"]["widgets"][0]["config"]["integration_id"] == a.id
    assert body["matched_integrations"][0]["via"] == "map"
    only = add_integration(session, "sonarr")
    body = do_import(client, int_doc("sonarr"), integration_map={"r0": None}).json()  # explicit "set up later"
    assert [u["ref"] for u in body["unresolved_integrations"]] == ["r0"] and only


def test_integration_map_is_checked(client, session):
    jf = add_integration(session, "jellyfin")
    assert do_import(client, int_doc("sonarr"), integration_map={"r0": jf.id}).status_code == 422  # wrong type
    assert do_import(client, int_doc("jellyfin"), integration_map={"r0": "nope"}).status_code == 422  # no such id
    assert do_import(client, int_doc("jellyfin"), integration_map={"zzz": jf.id}).status_code == 422  # not in the file
    assert session.query(models.Dashboard).count() == 0


def test_multiple_integration_field_is_remapped(client, session):
    ics = add_integration(session, "ics", "Cal")
    doc = page_file(integrations=[{"ref": "c", "type": "ics", "name": "Cal"}],
                    widgets=[{"type": "calendar", "x": 0, "y": 0, "w": 6, "h": 6, "config": {"integration_ids": ["c"]}}])
    body = do_import(client, doc).json()
    assert body["dashboard"]["widgets"][0]["config"]["integration_ids"] == [ics.id]


# ---------- preview ----------
def test_preview_writes_nothing_and_matches_the_real_summary(client, session):
    add_service(session)
    jf = add_integration(session, "jellyfin")
    doc = svc_doc()
    doc["integrations"] = [{"ref": "r0", "type": "jellyfin", "name": "J"}, {"ref": "r1", "type": "sonarr", "name": "S"}]
    doc["widgets"].append({"type": "sonarr", "x": 0, "y": 6, "w": 8, "h": 6, "config": {"integration_id": "r1"}})
    before = (session.query(models.Dashboard).count(), session.query(models.Widget).count(), session.query(models.Service).count())
    pre = post(client, "/api/dashboards/import/preview", {"page": doc}).json()
    assert before == (session.query(models.Dashboard).count(), session.query(models.Widget).count(), session.query(models.Service).count())
    assert pre["dashboard"]["id"] is None and pre["widget_count"] == 3
    assert [s["name"] for s in pre["matched_services"]] == ["Jellyfin"]
    assert [u["ref"] for u in pre["unresolved_integrations"]] == ["r1"] and pre["unresolved_integrations"][0]["widget_ids"] == []
    assert pre["unresolved_integrations"][0]["widget_count"] == 1 and jf.id == pre["matched_integrations"][0]["id"]
    real = do_import(client, doc).json()
    for key in ("matched_services", "created_services", "warnings"):
        assert real[key] == pre[key]
    assert [u["ref"] for u in real["unresolved_integrations"]] == ["r1"] and len(real["unresolved_integrations"][0]["widget_ids"]) == 1


def test_preview_flags_custom_code(client):
    doc = page_file(widgets=[{"type": "custom", "x": 0, "y": 0, "w": 6, "h": 4, "config": {"html": "<b>x</b>", "js": "1"}}])
    pre = post(client, "/api/dashboards/import/preview", {"page": doc}).json()
    assert pre["custom_widgets"] == 1
    assert any("Contains custom code; it runs sandboxed." in w for w in pre["warnings"])
    assert do_import(client, doc).json()["custom_widgets"] == 1


def test_import_warns_about_secret_looking_urls(client):
    doc = page_file(widgets=[{"type": "iframe", "x": 0, "y": 0, "w": 8, "h": 6, "config": {"url": "http://a:b@host/?token=zz"}}])
    warnings = "\n".join(do_import(client, doc).json()["warnings"])
    assert "credentials" in warnings and "token" in warnings and "zz" not in warnings.replace("zzz", "")


# ---------- untrusted files ----------
def bad(client, page, status=422, **opts):
    before = (client.get("/api/dashboards").json())
    r = do_import(client, page, **opts)
    assert r.status_code == status, r.text
    assert client.get("/api/dashboards").json() == before  # nothing was created
    return r


def w(**over):
    base = {"type": "clock", "x": 0, "y": 0, "w": 4, "h": 2, "config": {}}
    base.update(over)
    return base


def test_rejects_wrong_format_and_versions(client):
    for page, text in [
        (page_file(format="something-else"), "format"),
        ({"version": 1}, "format"),
        (page_file(version=2), "version 2"),
        (page_file(version=0), "version"),
        (page_file(version="1"), "version"),
        (page_file(version=True), "version"),
    ]:
        assert text in bad(client, page).text
    assert post(client, "/api/dashboards/import", {"page": "not an object"}).status_code == 422
    assert client.post("/api/dashboards/import", content=b"{nope", headers={**HEADERS, "content-type": "application/json"}).status_code == 422


def test_rejects_unknown_widget_type_and_bad_geometry(client):
    assert "unknown widget type" in bad(client, page_file(widgets=[w(type="evil")])).text
    bad(client, page_file(widgets=[w(w=-1)]))
    bad(client, page_file(widgets=[w(h=0)]))
    bad(client, page_file(widgets=[w(x=-2)]))
    bad(client, page_file(widgets=[w(y=-0.5)]))
    bad(client, page_file(widgets=[w(w=30)]))  # wider than the 24 columns
    bad(client, page_file(widgets=[w(x=22, w=4)]))  # runs past the edge
    bad(client, page_file(widgets=[w(w="wide")]))
    bad(client, page_file(columns=1000))
    bad(client, page_file(row_height=-4))


def test_rejects_too_many_widgets(client):
    bad(client, page_file(widgets=[w() for _ in range(pages.MAX_WIDGETS + 1)]))
    ok = do_import(client, page_file(widgets=[w(x=i % 20, y=i // 20) for i in range(pages.MAX_WIDGETS)]))
    assert ok.status_code == 201 and ok.json()["widget_count"] == pages.MAX_WIDGETS


def test_rejects_oversized_values_and_bodies(client):
    assert "longer than" in bad(client, page_file(widgets=[w(config={"label": "x" * (pages.MAX_STRING + 1)})])).text
    assert do_import(client, page_file(widgets=[w(config={"label": "x" * pages.MAX_STRING})])).status_code == 201
    bad(client, page_file(widgets=[w(config={"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": 1}}}}}}}}}})]))
    bad(client, page_file(widgets=[w(config={"l": list(range(pages.MAX_ITEMS + 1))})]))
    big = page_file(widgets=[w(config={"label": "x" * (10 * 1024 * 1024)})])  # a 10 MB config string
    r = post(client, "/api/dashboards/import", {"page": big})
    assert r.status_code == 413
    assert post(client, "/api/dashboards/import/preview", {"page": big}).status_code == 413
    assert len(client.get("/api/dashboards").json()) == 1  # only the legitimate 100 000 character import above


def test_rejects_ids_instead_of_refs(client, session):
    svc = add_service(session)
    integ = add_integration(session)
    text = bad(client, page_file(widgets=[w(type="group", w=8, h=4, config={"mode": "ids", "service_ids": [svc.id]})])).text
    assert "never by database id" in text
    bad(client, page_file(widgets=[w(type="jellyfin", w=8, h=5, config={"integration_id": integ.id})]))
    bad(client, page_file(widgets=[w(service_ref="nope")]))
    bad(client, page_file(widgets=[w(service_id=svc.id)]))  # a raw id field is not part of the format
    bad(client, page_file(services=[{"ref": "a", "id": svc.id, "name": "x", "host": "h"}]))


def test_rejects_integration_config_and_bad_entries(client):
    bad(client, page_file(integrations=[{"ref": "i", "type": "jellyfin", "name": "J", "config": {"api_key": "k"}}]))
    bad(client, page_file(integrations=[{"ref": "i", "type": "not-a-type", "name": "J"}]))
    bad(client, page_file(integrations=[{"ref": "i", "type": "jellyfin", "name": "J"}, {"ref": "i", "type": "plex", "name": "P"}]))
    bad(client, page_file(services=[{"ref": "a", "name": "x", "host": "a b"}]))
    bad(client, page_file(services=[{"ref": "a", "name": "x", "host": "u@h"}]))
    bad(client, page_file(services=[{"ref": "a", "name": "x", "host": "h", "port": 70000}]))
    bad(client, page_file(services=[{"ref": "bad ref!", "name": "x", "host": "h"}]))
    bad(client, page_file(services=[{"ref": "a", "name": "x", "host": "h"}, {"ref": "a", "name": "y", "host": "h"}]))
    bad(client, page_file(services=[{"ref": "a", "name": "x", "host": "h", "depends_on": ["zz"]}]))


def test_page_settings_are_whitelisted(client):
    doc = page_file(settings={"gap": 12, "snap": "half", "evil": "x", "background_image": "javascript:alert(1)", "show_grid": True})
    body = do_import(client, doc).json()
    assert body["dashboard"]["settings"] == {"gap": 12, "snap": "half", "show_grid": True}
    assert any("background image" in x for x in body["warnings"])


def test_import_name_default_and_options(client):
    assert do_import(client, page_file(name="From file")).json()["dashboard"]["name"] == "From file"
    assert do_import(client, page_file(name="From file"), name="  Mine ").json()["dashboard"]["name"] == "Mine"
    assert do_import(client, page_file(), service_mode="nonsense").status_code == 422
    names = [d["name"] for d in client.get("/api/dashboards").json()]
    assert names == ["From file", "Mine"]


# ---------- transaction ----------
def test_failure_midway_rolls_everything_back(client, session, monkeypatch):
    doc = svc_doc()
    doc["widgets"] += [w(x=0, y=8), w(x=4, y=8)]
    original = models.Widget.__init__
    calls = {"n": 0}

    def boom(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk on fire")
        original(self, *a, **kw)

    monkeypatch.setattr(models.Widget, "__init__", boom)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/dashboards/import", json={"page": doc, "service_mode": "create"}, headers=HEADERS)
    assert r.status_code == 500
    monkeypatch.undo()
    assert session.query(models.Dashboard).count() == 0
    assert session.query(models.Widget).count() == 0
    assert session.query(models.Service).count() == 0
    assert do_import(client, doc).status_code == 201  # the session is usable afterwards


# ---------- the catalog drives which keys carry ids ----------
def test_every_catalog_widget_type_round_trips_its_id_fields(client, session):
    """No widget type is special-cased: every `service` / `services` / `integration` field in the catalog is rewritten."""
    svc_a = add_service(session, name="A", host="a", port=1)
    svc_b = add_service(session, name="B", host="b", port=2)
    integ_a = add_integration(session, "jellyfin", "IA")
    integ_b = add_integration(session, "sonarr", "IB")
    checked = 0
    for wtype, spec in pages.catalog().items():
        config, wanted = {}, {}
        for f in spec["fields"]:
            if f["type"] == "service":
                config[f["key"]] = svc_a.id
            elif f["type"] == "services":
                config[f["key"]] = [svc_a.id, svc_b.id]
            elif f["type"] == "integration":
                config[f["key"]] = [integ_a.id, integ_b.id] if f.get("multiple") else integ_a.id
        assert pages.id_fields(wtype).keys() == {k for k in config}
        d = make_page(client, name=f"T-{wtype}", widgets=[widget(wtype, 0, 0, 4, 2, config, service_id=svc_a.id if spec["usesService"] else None)])
        doc = client.get(f"/api/dashboards/{d['id']}/export").json()
        text = json.dumps(doc)
        for real in (svc_a.id, svc_b.id, integ_a.id, integ_b.id):
            assert real not in text, f"{wtype}: a database id leaked into the export"
        if config:
            checked += 1
        imap = {i["ref"]: (integ_a.id if i["type"] == "jellyfin" else integ_b.id) for i in doc["integrations"]}
        body = do_import(client, doc, service_mode="match", integration_map=imap).json()
        assert body["unresolved_integrations"] == [], wtype
        new_cfg = body["dashboard"]["widgets"][0]["config"]
        if wtype == "custom":  # code from a file is imported unlinked, see test_custom_widgets_are_imported_unlinked
            assert new_cfg == {}
            continue
        assert new_cfg == config, f"{wtype}: config did not come back with the same ids"
        assert body["dashboard"]["widgets"][0]["service_id"] == (svc_a.id if spec["usesService"] else None)
    assert checked >= 20  # group, uptime, topology, every integration widget, calendar ...


# ---------- phone layout (set by another feature; carried when the column exists) ----------
def test_layout_sm_is_carried_when_the_widget_model_has_it(client, session):
    if not hasattr(models.Widget, "layout_sm"):
        pytest.skip("models.Widget has no layout_sm in this tree")
    d = make_page(client, widgets=[widget("clock", 0, 0, 4, 2)])
    row = session.query(models.Widget).filter_by(dashboard_id=d["id"]).one()
    row.layout_sm = {"x": 0, "y": 3, "w": 4, "h": 2}
    session.commit()
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    want = {"x": 0, "y": 3, "w": 4, "h": 2, "hidden": False}
    assert doc["widgets"][0]["layout_sm"] == want
    body = do_import(client, doc).json()
    new = session.get(models.Widget, body["dashboard"]["widgets"][0]["id"])
    assert new.layout_sm == want


# ---------- auth ----------
PW = "correct horse battery"


@pytest.fixture(name="locked")
def locked_fixture(client):
    assert post(client, "/api/auth/password", {"new": PW}).status_code == 200
    page = make_page(client)
    return client, page


def test_guests_and_anonymous_get_401_on_export_import_and_templates(locked):
    admin, page = locked
    assert admin.patch("/api/auth/settings", json={"guest_access": "read"}, headers=HEADERS).status_code == 200
    guest = TestClient(app)
    assert guest.get("/api/dashboards").status_code == 200  # guests can read pages ...
    assert guest.get(f"/api/dashboards/{page['id']}/export").status_code == 401  # ... but not export them
    assert guest.get(f"/api/dashboards/{page['id']}/export/").status_code == 401
    assert guest.get("/api/templates").status_code == 401
    assert guest.get("/api/templates/home").status_code == 401
    assert guest.post("/api/templates/home/apply", json={}, headers=HEADERS).status_code == 401
    assert do_import_as(guest, page_file()).status_code == 401
    assert guest.post("/api/dashboards/import/preview", json={"page": page_file()}, headers=HEADERS).status_code == 401
    # the signed-in session still works
    assert admin.get(f"/api/dashboards/{page['id']}/export").status_code == 200
    assert admin.get("/api/templates").status_code == 200
    assert do_import(admin, page_file()).status_code == 201


def test_no_guest_access_means_401_everywhere(locked):
    _, page = locked
    anon = TestClient(app)
    for path in (f"/api/dashboards/{page['id']}/export", "/api/templates", "/api/templates/home"):
        assert anon.get(path).status_code == 401
    assert do_import_as(anon, page_file()).status_code == 401


def do_import_as(c, page):
    return c.post("/api/dashboards/import", json={"page": page}, headers=HEADERS)


def test_export_path_rule():
    from app import auth

    for p in ("/api/dashboards/abc/export", "/api/dashboards/abc/export/", "/API/dashboards/abc/EXPORT"):
        assert auth.is_page_export(p)
    for p in ("/api/dashboards/abc", "/api/dashboards/abc/widgets", "/api/config/export"):
        assert not auth.is_page_export(p)


def test_original_page_json_is_not_mutated_by_export(client, session):
    svc = add_service(session)
    d = make_page(client, widgets=[widget("group", 0, 0, 8, 4, {"mode": "ids", "service_ids": [svc.id]})])
    before = copy.deepcopy(client.get(f"/api/dashboards/{d['id']}").json())
    client.get(f"/api/dashboards/{d['id']}/export")
    assert client.get(f"/api/dashboards/{d['id']}").json() == before


# ---------- services and URLs are held to discovery's rules (stored XSS) ----------
def _post_all(client, doc, how):
    if how == "preview":
        return post(client, "/api/dashboards/import/preview", {"page": doc})
    return do_import(client, doc)


BAD_SERVICES = [
    {"protocol": "javascript"},
    {"protocol": "ftp"},
    {"path": "/a\nb"},
    {"path": "/x\x00y"},
    {"health_url": "javascript:alert(1)"},
    {"health_url": "data:text/html,x"},
    {"health_url": "ftp://h/x"},
    {"health_type": "exec"},
    {"health_interval": 1},
    {"host": "javascript:alert(1)//"},
    {"icon": "javascript:alert(1)"},
]


@pytest.mark.parametrize("how", ["preview", "import"])
@pytest.mark.parametrize("override", BAD_SERVICES, ids=lambda o: next(iter(o)) + "=" + repr(next(iter(o.values())))[:20])
def test_bad_services_are_refused_in_preview_and_import(client, session, how, override):
    entry = {"ref": "a", "name": "X", "host": "h", "port": 80, **override}
    r = _post_all(client, page_file(services=[entry]), how)
    assert r.status_code == 422, r.text
    assert session.query(models.Service).count() == 0 and session.query(models.Dashboard).count() == 0


def test_service_path_is_normalised_on_import(client, session):
    do_import(client, page_file(services=[{"ref": "a", "name": "X", "host": "h", "path": "app/x"}]), service_mode="create")
    assert session.query(models.Service).one().path == "/app/x"


BAD_URLS = [
    ("link", {"url": "javascript:alert(1)", "name": "n"}),
    ("link", {"url": " JaVa\tScript:alert(1)"}),
    ("link", {"url": "vbscript:x"}),
    ("link", {"url": "data:text/html,<script>1</script>"}),
    ("image", {"url": "data:image/svg+xml;base64,PHN2Zz4="}),
    ("image", {"url": "x.png", "link": "javascript:1"}),
    ("iframe", {"url": "javascript:alert(1)"}),
    ("bookmarks", {"links": [{"id": "1", "name": "n", "url": "javascript:alert(1)", "icon": ""}]}),
    ("bookmarks", {"links": [{"id": "1", "name": "n", "url": "https://ok", "icon": "javascript:1"}]}),
    ("search", {"engine": "custom", "customUrl": "javascript:alert({q})"}),
    ("status", {"link": "javascript:1"}),
]


@pytest.mark.parametrize("how", ["preview", "import"])
@pytest.mark.parametrize("wtype,config", BAD_URLS, ids=lambda v: v if isinstance(v, str) else "")
def test_dangerous_widget_urls_are_refused(client, session, how, wtype, config):
    doc = page_file(widgets=[{"type": wtype, "x": 0, "y": 0, "w": 8, "h": 4, "config": config}])
    r = _post_all(client, doc, how)
    assert r.status_code == 422, r.text
    assert "only http, https or relative" in r.text
    assert session.query(models.Dashboard).count() == 0


def test_safe_urls_are_accepted(client):
    png = "data:image/png;base64,iVBORw0KGgo="
    doc = page_file(widgets=[
        {"type": "link", "x": 0, "y": 0, "w": 4, "h": 2, "config": {"url": "https://example.com/a?b=1", "icon": "github"}},
        {"type": "link", "x": 4, "y": 0, "w": 4, "h": 2, "config": {"url": "/local/page", "icon": png}},
        {"type": "link", "x": 8, "y": 0, "w": 4, "h": 2, "config": {"url": "192.168.1.5:8080", "icon": "🐙"}},
        {"type": "image", "x": 0, "y": 2, "w": 4, "h": 4, "config": {"url": png, "link": "http://x"}},
    ])
    assert do_import(client, doc).status_code == 201


@pytest.mark.parametrize("how", ["preview", "import"])
def test_template_apply_path_uses_the_same_checks(client, monkeypatch, how):
    """Template files go through the same parser; a tampered one is refused by apply."""
    from app import page_templates

    real = page_templates.load

    def tampered(tid):
        doc = real(tid)
        doc["services"] = [{"ref": "a", "name": "X", "host": "h", "protocol": "javascript"}]
        return doc

    monkeypatch.setattr(page_templates, "load", tampered)
    assert client.post("/api/templates/home/apply", json={}, headers=HEADERS).status_code == 422

    def tampered_url(tid):
        doc = real(tid)
        next(w for w in doc["widgets"] if w["type"] == "bookmarks")["config"]["links"][0]["url"] = "javascript:alert(1)"
        return doc

    monkeypatch.setattr(page_templates, "load", tampered_url)
    r = client.post("/api/templates/home/apply", json={}, headers=HEADERS)
    assert r.status_code == 422 and "only http, https or relative" in r.text
    # the preview path takes the full template as a page; same parser
    doc = tampered_url("home")
    assert _post_all(client, doc, how).status_code == 422
    assert client.get("/api/dashboards").json() == []


def test_export_leaves_out_services_the_importer_would_refuse(client, session):
    bad = add_service(session, name="Odd", host="h", port=1, protocol="gopher")
    good = add_service(session, name="Fine", host="g", port=2)
    d = make_page(client, widgets=[widget("group", 0, 0, 8, 4, {"mode": "ids", "service_ids": [bad.id, good.id]}),
                                   widget("service", 8, 0, 4, 2, service_id=bad.id)])
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    assert [s["name"] for s in doc["services"]] == ["Fine"]
    assert any("Odd" in w for w in doc["warnings"])
    assert do_import(client, doc).status_code == 201


def test_custom_widgets_are_imported_unlinked(client, session):
    svc = add_service(session)
    integ = add_integration(session, "jellyfin")
    d = make_page(client, widgets=[widget("custom", 0, 0, 6, 4, {"html": "x", "integration_id": integ.id, "service_ids": [svc.id]})])
    doc = client.get(f"/api/dashboards/{d['id']}/export").json()
    assert doc["widgets"][0]["config"]["integration_id"] == "i1"
    body = do_import(client, doc).json()  # the only jellyfin integration would be auto-matched for any other widget
    cfg = body["dashboard"]["widgets"][0]["config"]
    assert "integration_id" not in cfg and "service_ids" not in cfg and cfg["html"] == "x"
    assert any("without integration or service links" in w for w in body["warnings"])


def test_scheme_like_paths_are_harmless_and_normalised(client, session):
    """A path is only ever appended to protocol://host, so `javascript:` there is plain path text (discovery allows it too)."""
    do_import(client, page_file(services=[{"ref": "a", "name": "X", "host": "h", "path": "javascript:alert(1)"}]), service_mode="create")
    assert session.query(models.Service).one().url == "http://h/javascript:alert(1)"
