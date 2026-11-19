"""Phone layouts: widgets.layout_sm, the breakpoint on the layout route, and everything that copies widgets."""
import pytest
from sqlalchemy import create_engine, inspect, text

from app import main
from conftest import HEADERS

SM = {"x": 0, "y": 2, "w": 2, "h": 3, "hidden": False}


def make_page(client, **kw):
    res = client.post("/api/dashboards", json={"name": "P", **kw}, headers=HEADERS)
    assert res.status_code == 201, res.text
    return res.json()


def make_widget(client, page_id, **kw):
    res = client.post(f"/api/dashboards/{page_id}/widgets", json={"type": "clock", **kw}, headers=HEADERS)
    assert res.status_code == 201, res.text
    return res.json()


def widgets(client, page_id):
    return {w["id"]: w for w in client.get(f"/api/dashboards/{page_id}").json()["widgets"]}


def put_layout(client, page_id, items, **params):
    return client.put(f"/api/dashboards/{page_id}/layout", json=items, params=params, headers=HEADERS)


# ---------- migration ----------
def test_migration_adds_layout_sm_to_an_old_widgets_table(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE widgets (id VARCHAR PRIMARY KEY, dashboard_id VARCHAR, type VARCHAR, x FLOAT)"))
        c.execute(text("INSERT INTO widgets (id, dashboard_id, type, x) VALUES ('a', 'd', 'clock', 1)"))
    monkeypatch.setattr(main, "engine", eng)
    assert "layout_sm" not in {c["name"] for c in inspect(eng).get_columns("widgets")}
    main._migrate()
    assert "layout_sm" in {c["name"] for c in inspect(eng).get_columns("widgets")}
    main._migrate()  # idempotent
    with eng.begin() as c:
        assert c.execute(text("SELECT layout_sm FROM widgets WHERE id = 'a'")).scalar() is None


def test_widgets_without_phone_data_read_back_as_null(client):
    page = make_page(client)
    w = make_widget(client, page["id"])
    assert w["layout_sm"] is None
    assert client.get(f"/api/dashboards/{page['id']}").json()["widgets"][0]["layout_sm"] is None


# ---------- layout route ----------
def test_sm_layout_save_writes_only_the_phone_placement(client):
    page = make_page(client)
    w = make_widget(client, page["id"], x=3, y=4, w=5, h=6)
    res = put_layout(client, page["id"], [{"id": w["id"], "x": 1, "y": 2, "w": 3, "h": 2}], breakpoint="sm")
    assert res.status_code == 200, res.text
    got = widgets(client, page["id"])[w["id"]]
    assert got["layout_sm"] == {"x": 1, "y": 2, "w": 3, "h": 2, "hidden": False}
    assert (got["x"], got["y"], got["w"], got["h"]) == (3, 4, 5, 6)  # desktop untouched


def test_desktop_layout_call_is_unchanged_and_leaves_phone_alone(client):
    page = make_page(client)
    w = make_widget(client, page["id"], layout_sm=SM)
    res = put_layout(client, page["id"], [{"id": w["id"], "x": 7, "y": 8, "w": 9, "h": 2}])
    assert res.status_code == 200
    got = widgets(client, page["id"])[w["id"]]
    assert (got["x"], got["y"], got["w"], got["h"]) == (7, 8, 9, 2)
    assert got["layout_sm"] == SM
    explicit = put_layout(client, page["id"], [{"id": w["id"], "x": 1, "y": 1, "w": 2, "h": 2}], breakpoint="lg")
    assert explicit.status_code == 200
    assert widgets(client, page["id"])[w["id"]]["layout_sm"] == SM


def test_sm_layout_keeps_hidden_unless_sent(client):
    page = make_page(client)
    w = make_widget(client, page["id"])
    item = {"id": w["id"], "x": 0, "y": 0, "w": 2, "h": 2}
    put_layout(client, page["id"], [{**item, "hidden": True}], breakpoint="sm")
    put_layout(client, page["id"], [{**item, "x": 1}], breakpoint="sm")
    assert widgets(client, page["id"])[w["id"]]["layout_sm"]["hidden"] is True
    put_layout(client, page["id"], [{**item, "hidden": False}], breakpoint="sm")
    assert widgets(client, page["id"])[w["id"]]["layout_sm"]["hidden"] is False


@pytest.mark.parametrize(
    "item",
    [
        {"x": 3, "y": 0, "w": 2, "h": 1},  # 3 + 2 > 4 columns
        {"x": 0, "y": 0, "w": 5, "h": 1},
        {"x": -1, "y": 0, "w": 1, "h": 1},
        {"x": 0, "y": -1, "w": 1, "h": 1},
        {"x": 0, "y": 0, "w": 0, "h": 1},
        {"x": 0, "y": 0, "w": 1, "h": 0},
    ],
)
def test_sm_layout_rejects_what_does_not_fit_and_writes_nothing(client, item):
    page = make_page(client)
    a = make_widget(client, page["id"])
    b = make_widget(client, page["id"])
    ok = {"id": a["id"], "x": 0, "y": 0, "w": 1, "h": 1}
    res = put_layout(client, page["id"], [ok, {"id": b["id"], **item}], breakpoint="sm")
    assert res.status_code == 422
    assert widgets(client, page["id"])[a["id"]]["layout_sm"] is None  # all or nothing


def test_sm_layout_follows_columns_sm_and_ignores_foreign_ids(client):
    page = make_page(client, settings={"columns_sm": 2})
    w = make_widget(client, page["id"])
    wide = {"id": w["id"], "x": 0, "y": 0, "w": 3, "h": 1}
    assert put_layout(client, page["id"], [wide], breakpoint="sm").status_code == 422
    client.patch(f"/api/dashboards/{page['id']}", json={"settings": {"columns_sm": 6}}, headers=HEADERS)
    assert put_layout(client, page["id"], [wide, {"id": "nope", "x": 0, "y": 0, "w": 1, "h": 1}], breakpoint="sm").status_code == 200


def test_unknown_breakpoint_is_rejected(client):
    page = make_page(client)
    assert put_layout(client, page["id"], [], breakpoint="md").status_code == 422


def test_clear_phone_layout(client):
    page = make_page(client)
    w = make_widget(client, page["id"], layout_sm=SM)
    other = make_page(client)
    o = make_widget(client, other["id"], layout_sm=SM)
    res = client.delete(f"/api/dashboards/{page['id']}/layout", params={"breakpoint": "sm"}, headers=HEADERS)
    assert res.status_code == 204
    assert widgets(client, page["id"])[w["id"]]["layout_sm"] is None
    assert widgets(client, other["id"])[o["id"]]["layout_sm"] == SM  # other pages keep theirs
    assert client.delete(f"/api/dashboards/{page['id']}/layout", params={"breakpoint": "lg"}, headers=HEADERS).status_code == 422
    assert client.delete(f"/api/dashboards/{page['id']}/layout", headers=HEADERS).status_code == 422


# ---------- widget routes ----------
def test_create_and_patch_layout_sm(client):
    page = make_page(client)
    w = make_widget(client, page["id"], layout_sm={"x": 1, "y": 0, "w": 2, "h": 2})
    assert w["layout_sm"] == {"x": 1, "y": 0, "w": 2, "h": 2, "hidden": False}
    res = client.patch(f"/api/widgets/{w['id']}", json={"layout_sm": {**SM, "hidden": True}}, headers=HEADERS)
    assert res.json()["layout_sm"]["hidden"] is True
    res = client.patch(f"/api/widgets/{w['id']}", json={"config": {"a": 1}}, headers=HEADERS)
    assert res.json()["layout_sm"]["hidden"] is True  # untouched by other edits
    res = client.patch(f"/api/widgets/{w['id']}", json={"layout_sm": None}, headers=HEADERS)
    assert res.json()["layout_sm"] is None
    bad = client.patch(f"/api/widgets/{w['id']}", json={"layout_sm": {"x": -1, "y": 0, "w": 1, "h": 1}}, headers=HEADERS)
    assert bad.status_code == 422


def test_duplicate_carries_layout_sm_below_the_original(client):
    page = make_page(client)
    w = make_widget(client, page["id"], layout_sm={"x": 1, "y": 2, "w": 2, "h": 3, "hidden": True})
    clone = client.post(f"/api/widgets/{w['id']}/duplicate", headers=HEADERS).json()
    assert clone["layout_sm"] == {"x": 1, "y": 5, "w": 2, "h": 3, "hidden": True}
    plain = make_widget(client, page["id"])
    assert client.post(f"/api/widgets/{plain['id']}/duplicate", headers=HEADERS).json()["layout_sm"] is None


def test_moving_to_another_page_keeps_layout_sm(client):
    a, b = make_page(client), make_page(client)
    w = make_widget(client, a["id"], layout_sm=SM)
    res = client.patch(f"/api/widgets/{w['id']}", json={"dashboard_id": b["id"], "x": 2, "y": 2}, headers=HEADERS)
    assert res.json()["dashboard_id"] == b["id"]
    assert res.json()["layout_sm"] == SM


# ---------- page settings ----------
@pytest.mark.parametrize("settings", [{"columns_sm": 0}, {"columns_sm": 13}, {"columns_sm": "4"}, {"columns_sm": 2.5},
                                      {"columns_sm": True}, {"phone_layout": "tablet"}])
def test_bad_phone_page_settings_are_rejected(client, settings):
    page = make_page(client)
    assert client.patch(f"/api/dashboards/{page['id']}", json={"settings": settings}, headers=HEADERS).status_code == 422
    assert client.post("/api/dashboards", json={"name": "x", "settings": settings}, headers=HEADERS).status_code == 422


def test_phone_page_settings_round_trip(client):
    page = make_page(client)
    res = client.patch(
        f"/api/dashboards/{page['id']}", json={"settings": {"gap": 8, "columns_sm": 6, "phone_layout": "custom"}}, headers=HEADERS
    )
    assert res.status_code == 200
    assert res.json()["settings"] == {"gap": 8, "columns_sm": 6, "phone_layout": "custom"}


# ---------- export / import / backup ----------
def test_export_import_round_trip_carries_layout_sm(client):
    page = make_page(client, settings={"columns_sm": 3, "phone_layout": "custom"})
    w = make_widget(client, page["id"], layout_sm={"x": 1, "y": 1, "w": 2, "h": 2, "hidden": True})
    plain = make_widget(client, page["id"])
    bundle = client.get("/api/config/export").json()
    exported = {x["id"]: x for x in bundle["dashboards"][0]["widgets"]}
    assert exported[w["id"]]["layout_sm"]["hidden"] is True
    assert exported[plain["id"]]["layout_sm"] is None

    client.delete(f"/api/dashboards/{page['id']}", headers=HEADERS)
    res = client.post("/api/config/import?replace=true", json=bundle, headers=HEADERS)
    assert res.status_code == 200, res.text
    back = client.get(f"/api/dashboards/{page['id']}").json()
    assert back["settings"]["phone_layout"] == "custom"
    got = {x["id"]: x for x in back["widgets"]}
    assert got[w["id"]]["layout_sm"] == {"x": 1, "y": 1, "w": 2, "h": 2, "hidden": True}
    assert got[plain["id"]]["layout_sm"] is None


def test_import_of_an_older_bundle_without_layout_sm(client):
    old = {
        "version": 2,
        "services": [],
        "dashboards": [
            {
                "id": "old1",
                "name": "Old",
                "position": 0,
                "columns": 24,
                "row_height": 40,
                "settings": {},
                "widgets": [{"id": "w1", "dashboard_id": "old1", "type": "clock", "x": 0, "y": 0, "w": 4, "h": 3, "config": {}}],
            }
        ],
    }
    res = client.post("/api/config/import?replace=true", json=old, headers=HEADERS)
    assert res.status_code == 200, res.text
    assert widgets(client, "old1")["w1"]["layout_sm"] is None


def test_import_rejects_a_bad_phone_setting(client):
    bundle = {"services": [], "dashboards": [
        {"id": "p", "name": "P", "settings": {"columns_sm": 99}, "widgets": []}]}
    assert client.post("/api/config/import", json=bundle, headers=HEADERS).status_code == 422


def test_backup_and_restore_carry_layout_sm(client, tmp_path, monkeypatch):
    from app import backups

    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path)
    # The real exporter opens the real database; the test's session is what it must read.
    monkeypatch.setattr(backups, "_export_bundle", lambda: client.get("/api/config/export").json())
    page = make_page(client)
    w = make_widget(client, page["id"], layout_sm=SM)
    name = client.post("/api/backups?label=phone", headers=HEADERS).json()["name"]
    client.patch(f"/api/widgets/{w['id']}", json={"layout_sm": None}, headers=HEADERS)
    assert widgets(client, page["id"])[w["id"]]["layout_sm"] is None
    assert client.post(f"/api/backups/{name}/restore", headers=HEADERS).status_code == 200
    assert widgets(client, page["id"])[w["id"]]["layout_sm"] == SM
