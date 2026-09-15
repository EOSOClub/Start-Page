"""Router CRUD tests against an in-memory SQLite database.

Covers the dashboards/services/settings/widgets routers — the surface where the
Iteration 001-005 bugs lived (onPage reorder, dashboard delete redo, widget
history paths). Every mutating call carries the CSRF header the real app enforces
(main.py csrf_guard); the one negative test proves the guard actually runs under
TestClient.
"""

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