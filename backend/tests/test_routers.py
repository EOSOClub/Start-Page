"""Router CRUD tests against an in-memory SQLite database.

Covers the dashboards/services/settings/widgets routers — the surface where the
Iteration 001-005 bugs lived (onPage reorder, dashboard delete redo, widget
history paths). Every mutating call carries the CSRF header the real app enforces
(main.py csrf_guard); the one negative test proves the guard actually runs under
TestClient.
"""

import fastapi
import pytest

from app import backups
from conftest import HEADERS


def _create_dashboard(client, name="Home", **kw):
    res = client.post("/api/dashboards", json={"name": name, **kw}, headers=HEADERS)
    assert res.status_code == 201, res.text
    return res.json()


def _create_widget(client, dashboard_id, type="link", **kw):
    res = client.post(
        f"/api/dashboards/{dashboard_id}/widgets",
        json={"type": type, **kw},
        headers=HEADERS,
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_csrf_guard_rejects_unsafe_calls_without_header(client):
    res = client.post("/api/dashboards", json={"name": "X"})
    assert res.status_code == 403


def test_cve_badhost_dependency_floor():
    # CVE-2026-48710 (BadHost) is fixed only in starlette >= 1.0.1 (CVE is in all
    # of 0.x). fastapi >= 0.141 allows it but does not force it (its floor is
    # starlette>=0.46.0), hence the explicit starlette floor. Version canary: red
    # on the vulnerable pair, green on the fixed stack — silently guards the floor
    # against requirements.txt pruning. The CVE itself is NOT exercisable under
    # TestClient (httpx ASGITransport builds the ASGI scope itself; the
    # Host-header -> url.path plumbing never exists), so the floor is the guarantee.
    import starlette

    def _ver(v: str) -> tuple:
        return tuple(int(p) for p in v.split("."))

    assert _ver(starlette.__version__) >= (1, 0, 1)
    assert _ver(fastapi.__version__) >= (0, 141, 0)


def test_dashboard_create_list_get(client):
    dash = _create_dashboard(client, name="Work")
    assert dash["name"] == "Work"
    listed = client.get("/api/dashboards").json()
    assert [d["id"] for d in listed] == [dash["id"]]
    detail = client.get(f"/api/dashboards/{dash['id']}").json()
    assert detail["columns"] == 24
    assert detail["widgets"] == []


def test_dashboard_update(client):
    dash = _create_dashboard(client, name="Old")
    res = client.patch(
        f"/api/dashboards/{dash['id']}",
        json={"name": "New", "row_height": 60},
        headers=HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["name"] == "New"
    assert client.get(f"/api/dashboards/{dash['id']}").json()["row_height"] == 60


def test_dashboard_delete(client):
    dash = _create_dashboard(client)
    res = client.delete(f"/api/dashboards/{dash['id']}", headers=HEADERS)
    assert res.status_code == 204
    assert client.get(f"/api/dashboards/{dash['id']}").status_code == 404


def test_save_layout_updates_positions(client):
    dash = _create_dashboard(client)
    w1 = _create_widget(client, dash["id"], x=0, y=0, w=4, h=3)
    w2 = _create_widget(client, dash["id"], x=4, y=0, w=4, h=3)
    res = client.put(
        f"/api/dashboards/{dash['id']}/layout",
        json=[
            {"id": w1["id"], "x": 8, "y": 5, "w": 6, "h": 2},
            {"id": w2["id"], "x": 0, "y": 2, "w": 4, "h": 1},
        ],
        headers=HEADERS,
    )
    assert res.status_code == 200
    by_id = {w["id"]: w for w in res.json()}
    assert (by_id[w1["id"]]["x"], by_id[w1["id"]]["y"], by_id[w1["id"]]["w"], by_id[w1["id"]]["h"]) == (8, 5, 6, 2)
    assert (by_id[w2["id"]]["x"], by_id[w2["id"]]["y"]) == (0, 2)


def test_widget_update_moves_and_changes(client):
    dash = _create_dashboard(client)
    dash2 = _create_dashboard(client, name="Second")
    w = _create_widget(client, dash["id"])
    res = client.patch(
        f"/api/widgets/{w['id']}",
        json={"dashboard_id": dash2["id"], "config": {"title": "hi"}},
        headers=HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["dashboard_id"] == dash2["id"]
    assert res.json()["config"] == {"title": "hi"}


def test_widget_duplicate_copies_config_and_offsets(client):
    dash = _create_dashboard(client)
    w = _create_widget(client, dash["id"], config={"title": "orig"})
    res = client.post(f"/api/widgets/{w['id']}/duplicate", headers=HEADERS)
    assert res.status_code == 201
    clone = res.json()
    assert clone["id"] != w["id"]
    assert clone["config"] == {"title": "orig"}
    assert (clone["x"], clone["y"]) == (w["x"] + 1, w["y"] + 1)
    assert len(client.get(f"/api/dashboards/{dash['id']}").json()["widgets"]) == 2


def test_widget_delete(client):
    dash = _create_dashboard(client)
    w = _create_widget(client, dash["id"])
    res = client.delete(f"/api/widgets/{w['id']}", headers=HEADERS)
    assert res.status_code == 204
    assert client.get(f"/api/dashboards/{dash['id']}").json()["widgets"] == []


def test_services_crud(client):
    res = client.post(
        "/api/services",
        json={"name": "Router", "protocol": "http", "host": "192.168.1.1"},
        headers=HEADERS,
    )
    assert res.status_code == 201
    svc = res.json()
    assert svc["url"] == "http://192.168.1.1"
    assert client.get("/api/services").json()[0]["name"] == "Router"

    res = client.patch(
        f"/api/services/{svc['id']}",
        json={"port": 8080, "health_interval": 10},
        headers=HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["url"] == "http://192.168.1.1:8080"
    assert res.json()["health_interval"] == 10

    assert client.delete(f"/api/services/{svc['id']}", headers=HEADERS).status_code == 204
    assert client.get(f"/api/services/{svc['id']}").status_code == 404


def test_settings_defaults_and_update(client):
    default = client.get("/api/settings").json()
    assert default["title"] == "Start Page"
    assert default["theme"] == "system"

    res = client.patch(
        "/api/settings",
        json={"title": "My Start", "open_new_tab": True},
        headers=HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["title"] == "My Start"
    assert client.get("/api/settings").json()["open_new_tab"] is True


# ---------- Iteration 011: backend hardening regressions ----------


def test_unknown_api_path_returns_404_json(client):
    res = client.get("/api/definitely-not-a-route")
    assert res.status_code == 404
    assert res.json()["detail"] == "Not found"
    assert not res.headers.get("content-type", "").startswith("text/html")


@pytest.mark.parametrize("path", ["/..%2frequirements.txt", "/%2e%2e/requirements.txt"])
def test_static_traversal_is_rejected(client, path):
    # Both decode to backend/static/../requirements.txt; the resolve()-then-
    # is_relative_to() guard must 404 and never leak the tracked file's bytes.
    res = client.get(path)
    assert res.status_code == 404
    assert b"fastapi" not in res.content


def test_index_html_served(client):
    res = client.get("/index.html")
    assert res.status_code == 200
    assert res.headers.get("content-type", "").startswith("text/html")


def test_widget_create_bogus_service_id_is_400(client):
    dash = _create_dashboard(client)
    res = client.post(
        f"/api/dashboards/{dash['id']}/widgets",
        json={"type": "link", "service_id": "does-not-exist"},
        headers=HEADERS,
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid reference or duplicate id"


def test_widget_patch_bogus_service_id_is_400(client):
    dash = _create_dashboard(client)
    w = _create_widget(client, dash["id"])
    res = client.patch(
        f"/api/widgets/{w['id']}",
        json={"service_id": "does-not-exist"},
        headers=HEADERS,
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid reference or duplicate id"


def test_widget_duplicate_client_id_is_400(client):
    dash = _create_dashboard(client)
    res = client.post(
        f"/api/dashboards/{dash['id']}/widgets",
        json={"type": "link", "id": "same-id"},
        headers=HEADERS,
    )
    assert res.status_code == 201
    res = client.post(
        f"/api/dashboards/{dash['id']}/widgets",
        json={"type": "link", "id": "same-id"},
        headers=HEADERS,
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid reference or duplicate id"


def test_merge_import_moved_widget_gets_fresh_id(client):
    a = _create_dashboard(client, name="A")
    b = _create_dashboard(client, name="B")
    w = _create_widget(client, b["id"], config={"title": "orig"})
    wid = w["id"]

    bundle = client.get("/api/config/export").json()
    adash = next(d for d in bundle["dashboards"] if d["id"] == a["id"])
    bdash = next(d for d in bundle["dashboards"] if d["id"] == b["id"])
    moved = next(x for x in bdash["widgets"] if x["id"] == wid)
    bdash["widgets"].remove(moved)
    adash["widgets"].append(moved)

    res = client.post("/api/config/import?replace=false", json=bundle, headers=HEADERS)
    assert res.status_code == 200

    a_widgets = client.get(f"/api/dashboards/{a['id']}").json()["widgets"]
    assert len(a_widgets) == 1
    ready = a_widgets[0]
    assert ready["id"] != wid
    assert ready["config"] == {"title": "orig"}
    assert client.get(f"/api/dashboards/{b['id']}").json()["widgets"] == []


def test_create_dashboard_empty_id_rejected(client):
    res = client.post("/api/dashboards", json={"name": "X", "id": ""}, headers=HEADERS)
    assert res.status_code == 422


def test_backup_same_label_same_second_distinct_files(monkeypatch, tmp_path):
    # Same-label same-second backups (double-click restore / "Backup now") used to
    # clobber each other; the %f stamp makes names unique. Patched so the real
    # backend/data/backups/ is never touched (ADR-009 isolation contract).
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(
        backups, "_export_bundle", lambda: {"services": [], "dashboards": [], "integrations": [], "settings": {}}
    )
    first = backups.create_backup("manual")
    second = backups.create_backup("manual")
    assert first["name"] != second["name"]
    assert {p.name for p in tmp_path.glob("*.json")} == {first["name"], second["name"]}


def test_revision_counts_only_successful_external_writes(client):
    def rev():
        return client.get("/api/revision").json()["rev"]

    start = rev()
    page = _create_dashboard(client)  # the app's own write
    client.get(f"/api/dashboards/{page['id']}", headers={"x-requested-by": "claude"})  # a read
    client.patch("/api/widgets/missing", json={"x": 1}, headers={"x-requested-by": "claude"})  # 404
    assert rev() == start

    res = client.post(f"/api/dashboards/{page['id']}/widgets", json={"type": "clock"}, headers={"x-requested-by": "claude"})
    assert res.status_code == 201
    assert rev() == start + 1
    assert client.get("/api/revision").json()["boot"]


# ---------- 2026-11-18 bug run ----------
def test_patch_with_explicit_null_does_not_corrupt_json_columns(client):
    # {"config": null} used to store JSON null, after which every read of the page was a 500.
    page = _create_dashboard(client, settings={"gap": 6})
    w = _create_widget(client, page["id"], config={"name": "A"})
    res = client.patch(f"/api/widgets/{w['id']}", json={"config": None, "x": 3}, headers=HEADERS)
    assert res.status_code == 200, res.text
    assert res.json()["config"] == {"name": "A"} and res.json()["x"] == 3
    res = client.patch(f"/api/dashboards/{page['id']}", json={"settings": None, "name": None}, headers=HEADERS)
    assert res.status_code == 200, res.text
    assert res.json()["settings"] == {"gap": 6} and res.json()["name"] == "Home"
    assert client.get("/api/dashboards").status_code == 200
    assert client.get(f"/api/dashboards/{page['id']}").status_code == 200
    assert client.get("/api/config/export").status_code == 200


def test_widget_patch_can_unlink_service(client):
    svc = client.post("/api/services", json={"name": "S", "host": "h"}, headers=HEADERS).json()
    page = _create_dashboard(client)
    w = _create_widget(client, page["id"], type="service", service_id=svc["id"])
    for empty in (None, ""):
        client.patch(f"/api/widgets/{w['id']}", json={"service_id": svc["id"]}, headers=HEADERS)
        res = client.patch(f"/api/widgets/{w['id']}", json={"service_id": empty}, headers=HEADERS)
        assert res.status_code == 200, res.text
        assert res.json()["service_id"] is None


def test_import_with_bad_settings_is_rejected_and_writes_nothing(client):
    # Used to commit the bad value and then 500 on settings, export and every backup.
    bundle = {
        "services": [],
        "dashboards": [{"id": "imported", "name": "Imported", "widgets": []}],
        "settings": {"backup_keep": "many"},
    }
    res = client.post("/api/config/import", json=bundle, headers=HEADERS)
    assert res.status_code == 422, res.text
    assert "backup_keep" in res.text
    assert client.get("/api/settings").json()["backup_keep"] == 14
    assert client.get("/api/config/export").status_code == 200


def test_bad_stored_setting_falls_back_to_default(client, session):
    from app import models

    session.add(models.AppSetting(key="app", value={"backup_keep": "many", "title": "Mine"}))
    session.commit()
    got = client.get("/api/settings")
    assert got.status_code == 200, got.text
    assert got.json()["backup_keep"] == 14 and got.json()["title"] == "Mine"
    # An unrelated save heals the stored value instead of failing on it.
    res = client.patch("/api/settings", json={"accent": "#ff0000"}, headers=HEADERS)
    assert res.status_code == 200, res.text
    assert res.json()["backup_keep"] == 14 and res.json()["accent"] == "#ff0000"


def test_invalid_backup_file_is_422_not_500(client, monkeypatch, tmp_path):
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path)
    (tmp_path / "truncated.json").write_text('{"services": [', encoding="utf-8")
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "other.json").write_text('{"hello": "world"}', encoding="utf-8")
    for name in ("truncated.json", "list.json", "other.json"):
        assert client.get(f"/api/backups/{name}").status_code == 422, name
        assert client.post(f"/api/backups/{name}/restore", headers=HEADERS).status_code == 422, name
    assert client.get("/api/backups/missing.json").status_code == 404
    assert {p.name for p in tmp_path.glob("*.json")} == {"truncated.json", "list.json", "other.json"}  # no pre-restore backup


@pytest.mark.parametrize("label", ["auto", "pre-auto"])
def test_manual_backup_cannot_use_the_auto_suffix(client, monkeypatch, tmp_path, label):
    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(
        backups, "_export_bundle", lambda: {"services": [], "dashboards": [], "integrations": [], "settings": {}}
    )
    res = client.post("/api/backups", params={"label": label}, headers=HEADERS)
    assert res.status_code == 201, res.text
    assert not list(tmp_path.glob("*-auto.json"))
    assert res.json()["name"].endswith(f"-{label}-manual.json")


def test_integration_patch_rejects_unknown_type(client, session):
    from app import models

    session.add(models.Integration(id="i1", name="Feed", type="rss", enabled=False, config={}))
    session.commit()
    res = client.patch("/api/integrations/i1", json={"type": "dockerr"}, headers=HEADERS)
    assert res.status_code == 400, res.text
    assert client.get("/api/integrations").json()[0]["type"] == "rss"


def test_import_keeps_a_free_widget_id_so_page_restore_keeps_undo_targets(client):
    page = _create_dashboard(client)
    w = _create_widget(client, page["id"], config={"name": "A"})
    detail = client.get(f"/api/dashboards/{page['id']}").json()
    assert client.delete(f"/api/dashboards/{page['id']}", headers=HEADERS).status_code == 204

    # What the app's "undo delete page" sends.
    bundle = {"services": [], "integrations": [], "settings": {}, "dashboards": [detail]}
    assert client.post("/api/config/import?replace=false", json=bundle, headers=HEADERS).status_code == 200
    assert [x["id"] for x in client.get(f"/api/dashboards/{page['id']}").json()["widgets"]] == [w["id"]]
    # An older undo step for that widget reaches it again.
    assert client.patch(f"/api/widgets/{w['id']}", json={"x": 7}, headers=HEADERS).status_code == 200

    # Re-importing over the live page keeps the id too; a repeat inside one bundle gets a fresh one.
    detail["widgets"].append(dict(detail["widgets"][0]))
    assert client.post("/api/config/import?replace=false", json=bundle, headers=HEADERS).status_code == 200
    ids = [x["id"] for x in client.get(f"/api/dashboards/{page['id']}").json()["widgets"]]
    assert len(ids) == 2 and len(set(ids)) == 2 and w["id"] in ids


def test_import_rejects_unknown_integration_type_before_writing(client):
    bundle = {
        "services": [],
        "dashboards": [{"id": "imported", "name": "Imported", "widgets": []}],
        "integrations": [{"id": "i1", "name": "X", "type": "dockerr"}],
    }
    res = client.post("/api/config/import?replace=true", json=bundle, headers=HEADERS)
    assert res.status_code == 422, res.text
    assert "dockerr" in res.text
    assert client.get("/api/dashboards").json() == []
    assert client.get("/api/integrations").json() == []


def test_reorder_dashboards_is_all_or_nothing(client):
    a, b, c = (_create_dashboard(client, name=n) for n in "ABC")
    res = client.put("/api/dashboards/order", json=[c["id"], a["id"], b["id"]], headers=HEADERS)
    assert res.status_code == 200, res.text
    assert [d["name"] for d in res.json()] == ["C", "A", "B"]
    res = client.put("/api/dashboards/order", json=[b["id"], "missing", a["id"]], headers=HEADERS)
    assert res.status_code == 404
    assert [d["name"] for d in client.get("/api/dashboards").json()] == ["C", "A", "B"]


def test_icon_search_rejects_negative_limit(client):
    assert client.get("/api/icons", params={"q": "x", "limit": -1}).status_code == 422


@pytest.mark.parametrize("url", ["http://[::1", "http://[bad"])
def test_malformed_url_is_400_not_500(client, url):
    assert client.get("/api/favicon", params={"url": url}).status_code == 400
    assert client.get("/api/sitemeta", params={"url": url}).status_code == 400


def test_sitemeta_title_is_per_page_not_per_origin(client, monkeypatch, tmp_path):
    import httpx

    from app.routers import favicons

    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/favicon.ico":
            return httpx.Response(200, content=b"\x89PNG....", headers={"content-type": "image/png"})
        pages.append(request.url.path)
        return httpx.Response(200, html=f"<html><head><title>Title of {request.url.path}</title></head></html>")

    real = httpx.AsyncClient
    monkeypatch.setattr(favicons, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        favicons.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "verify"}),
    )

    def title(path):
        return client.get("/api/sitemeta", params={"url": f"https://site.example{path}"}).json()["title"]

    assert title("/a") == "Title of /a"
    assert title("/b") == "Title of /b"  # used to answer "Title of /a" for 7 days
    assert title("/a") == "Title of /a"
    assert pages == ["/a", "/b"]  # each page fetched once; the third call came from the cache
    # The icon stays cached per origin: asking for it must not refetch any page.
    assert client.get("/api/favicon", params={"url": "https://site.example/c"}).status_code == 200
    assert pages == ["/a", "/b"]
    # A meta file cut off mid-write is a cache miss, not a permanent 500.
    next(tmp_path.glob("*.json")).write_text('{"origin": ', encoding="utf-8")
    assert title("/a") == "Title of /a"