"""Router CRUD tests against an in-memory SQLite database.

Covers the dashboards/services/settings/widgets routers — the surface where the
Iteration 001-005 bugs lived (onPage reorder, dashboard delete redo, widget
history paths). Every mutating call carries the CSRF header the real app enforces
(main.py csrf_guard); the one negative test proves the guard actually runs under
TestClient.
"""

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