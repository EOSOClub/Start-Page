"""Built-in page templates: the files validate against the live widget catalog and the grid,
the API lists/serves/applies them, and template ids can never reach outside the template directory."""
import json
from pathlib import Path

import pytest

from app import models, page_templates, pages

from conftest import HEADERS

ROOT = Path(__file__).resolve().parents[2]
MCP_CATALOG = ROOT / "mcp" / "widget_catalog.json"
IDS = ["home", "infrastructure", "media", "monitoring", "network", "smart-home"]
COLUMNS = 24


def catalog():
    return json.loads(MCP_CATALOG.read_text(encoding="utf-8"))


def template_docs():
    return {i: json.loads((page_templates.TEMPLATE_DIR / f"{i}.json").read_text(encoding="utf-8")) for i in IDS}


def test_backend_catalog_copy_matches_the_mcp_catalog():
    assert pages.CATALOG_PATH.read_bytes().replace(b"\r\n", b"\n") == MCP_CATALOG.read_bytes().replace(b"\r\n", b"\n"), (
        "backend/app/widget_catalog.json is out of date: run `npm run catalog` in frontend/"
    )


def test_exactly_the_six_templates_ship():
    assert sorted(p.stem for p in page_templates.TEMPLATE_DIR.glob("*.json")) == IDS
    assert page_templates.template_ids() == IDS


@pytest.mark.parametrize("tid", IDS)
def test_template_metadata_and_format(tid):
    doc = template_docs()[tid]
    assert doc["format"] == "startpage-page" and doc["version"] == 1 and doc["id"] == tid
    assert isinstance(doc["title"], str) and doc["title"]
    assert isinstance(doc["description"], str) and len(doc["description"]) > 20
    assert doc["tags"] and all(isinstance(t, str) for t in doc["tags"])
    assert doc["columns"] == COLUMNS
    assert doc["services"] == []  # templates never ship hosts to health-check
    # Integrations by ref + type + name only, and `requires` lists exactly those types.
    assert all(set(i) == {"ref", "type", "name"} for i in doc["integrations"])
    assert doc["requires"] == sorted({i["type"] for i in doc["integrations"]})


@pytest.mark.parametrize("tid", IDS)
def test_template_widgets_match_the_widget_catalog(tid):
    spec_of = catalog()
    doc = template_docs()[tid]
    int_refs = {i["ref"]: i["type"] for i in doc["integrations"]}
    assert doc["widgets"], tid
    for n, w in enumerate(doc["widgets"]):
        where = f"{tid} widget {n} ({w['type']})"
        assert w["type"] in spec_of, where
        assert w["type"] != "custom", f"{where}: built-in templates must not contain custom code"
        fields = {f["key"]: f for f in spec_of[w["type"]]["fields"]}
        assert set(w["config"]) <= set(fields), f"{where}: unknown config keys {set(w['config']) - set(fields)}"
        for key, value in w["config"].items():
            f = fields[key]
            if f["type"] == "select":
                assert value in [o["value"] for o in f["options"]], f"{where}.{key}={value!r} is not an option"
            elif f["type"] == "checkbox":
                assert isinstance(value, bool), f"{where}.{key}"
            elif f["type"] in ("number", "range"):
                assert isinstance(value, (int, float)) and not isinstance(value, bool), f"{where}.{key}"
            elif f["type"] == "integration":
                refs = value if isinstance(value, list) else [value]
                assert all(r in int_refs for r in refs), f"{where}.{key}: not a ref of this template"
                accepted = list(f["integrationType"]) if isinstance(f.get("integrationType"), list) else [f.get("integrationType")]
                if f.get("integrationType"):
                    assert all(int_refs[r] in accepted for r in refs), f"{where}.{key}: wrong integration type"
            elif f["type"] == "links":
                assert all({"id", "name", "url"} <= set(link) for link in value), f"{where}.{key}"
            else:
                assert isinstance(value, str), f"{where}.{key}"
        # integration widgets must point at one of the template's integrations
        if any(f["type"] == "integration" for f in fields.values()):
            assert any(f["type"] == "integration" and key in w["config"] for key, f in fields.items()), f"{where}: no integration set"
    used = {r for w in doc["widgets"] for k, v in w["config"].items() for r in (v if isinstance(v, list) else [v]) if isinstance(r, str) and r in int_refs}
    assert used == set(int_refs), f"{tid}: declared integrations that no widget uses: {set(int_refs) - used}"


@pytest.mark.parametrize("tid", IDS)
def test_template_widgets_fit_the_grid_and_do_not_overlap(tid):
    widgets = template_docs()[tid]["widgets"]
    for w in widgets:
        assert w["x"] >= 0 and w["y"] >= 0 and w["w"] > 0 and w["h"] > 0
        assert w["x"] + w["w"] <= COLUMNS, f"{tid}: {w['type']} runs past column {COLUMNS}"
    for i, a in enumerate(widgets):
        for b in widgets[i + 1:]:
            overlap = a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]
            assert not overlap, f"{tid}: {a['type']}@({a['x']},{a['y']}) overlaps {b['type']}@({b['x']},{b['y']})"


@pytest.mark.parametrize("tid", IDS)
def test_template_rows_are_filled_edge_to_edge(tid):
    """Each band of the layout is aligned: widgets in a row start where the previous one ends."""
    widgets = sorted(template_docs()[tid]["widgets"], key=lambda w: (w["y"], w["x"]))
    rows: dict[int, list] = {}
    for w in widgets:
        rows.setdefault(w["y"], []).append(w)
    for y, row in rows.items():
        if all(w["type"] == "heading" for w in row) or len(row) == 1:
            continue
        for a, b in zip(row, row[1:]):
            assert a["x"] + a["w"] <= b["x"], (tid, y)


def test_template_files_pass_the_importer():
    for tid in IDS:
        page = page_templates.page_of(tid)
        assert len(page.widgets) == len(template_docs()[tid]["widgets"])


# ---------- API ----------
def test_list_templates(client):
    r = client.get("/api/templates")
    assert r.status_code == 200
    items = r.json()
    assert [t["id"] for t in items] == IDS
    for t in items:
        assert {"id", "title", "description", "tags", "requires", "name", "columns", "row_height", "rows", "widget_count", "layout"} <= set(t)
        assert "widgets" not in t and "services" not in t and "integrations" not in t
        assert len(t["layout"]) == t["widget_count"]
        assert all(set(r) == {"type", "x", "y", "w", "h"} for r in t["layout"])  # rectangles, no configs
        assert t["rows"] == max(r["y"] + r["h"] for r in t["layout"])


def test_get_template_returns_the_whole_file(client):
    doc = client.get("/api/templates/media").json()
    assert doc["id"] == "media" and doc["format"] == "startpage-page"
    assert len(doc["widgets"]) == 9 and doc["requires"] == ["jellyfin", "qbittorrent", "radarr", "sabnzbd", "sonarr"]


@pytest.mark.parametrize("tid", ["nope", "..", "../main", "..%2F..%2Fapp%2Fmain", "%2e%2e%2fmain", "home.json",
                                 "....//....//etc/passwd", "..\\..\\main", "HOME", "home%00", "templates"])
def test_template_id_cannot_leave_the_template_directory(client, tid):
    # (An HTTP client may collapse a literal `..` before sending; the encoded forms reach the server as is.)
    assert client.get(f"/api/templates/{tid}").status_code in (404, 405)
    assert client.post(f"/api/templates/{tid}/apply", json={}, headers=HEADERS).status_code in (404, 405)
    assert client.get("/api/dashboards").json() == []  # and nothing was created


def test_loader_only_knows_listed_ids():
    for bad in ("../x", "..", "", "home.json", "a/b", None, 5):
        with pytest.raises(Exception) as e:
            page_templates.load(bad)
        assert getattr(e.value, "status_code", None) == 404


@pytest.mark.parametrize("tid", IDS)
def test_apply_every_template(client, session, tid):
    expected = len(template_docs()[tid]["widgets"])
    r = client.post(f"/api/templates/{tid}/apply", json={}, headers=HEADERS)
    assert r.status_code == 201, r.text
    body = r.json()
    dash = client.get(f"/api/dashboards/{body['dashboard']['id']}").json()
    assert len(dash["widgets"]) == expected == body["widget_count"]
    assert dash["name"] == template_docs()[tid]["name"] and dash["columns"] == COLUMNS
    needed = {i["ref"] for i in template_docs()[tid]["integrations"]}
    assert {u["ref"] for u in body["unresolved_integrations"]} == needed  # no integrations exist yet
    assert body["created_services"] == [] and body["custom_widgets"] == 0
    assert not any(w["type"] == "custom" for w in dash["widgets"])


def test_apply_with_name_and_integration_map(client, session):
    jf = models.Integration(name="Jelly", type="jellyfin", config={})
    sonarr_a = models.Integration(name="S1", type="sonarr", config={})
    sonarr_b = models.Integration(name="S2", type="sonarr", config={})
    session.add_all([jf, sonarr_a, sonarr_b])
    session.commit()
    r = client.post("/api/templates/media/apply", json={"name": "Movie night", "integration_map": {"sonarr": sonarr_b.id}}, headers=HEADERS)
    body = r.json()
    assert body["dashboard"]["name"] == "Movie night"
    by_type = {w["type"]: w for w in body["dashboard"]["widgets"]}
    assert by_type["jellyfin"]["config"]["integration_id"] == jf.id  # the only one: auto-matched
    assert by_type["sonarr"]["config"]["integration_id"] == sonarr_b.id  # from the map
    assert "integration_id" not in by_type["radarr"]["config"]
    assert {u["ref"] for u in body["unresolved_integrations"]} == {"radarr", "qbittorrent", "sabnzbd"}


def test_apply_rejects_bad_options(client):
    assert client.post("/api/templates/home/apply", json={"service_mode": "weird"}, headers=HEADERS).status_code == 422
    assert client.post("/api/templates/home/apply", json={"integration_map": {"zzz": None}}, headers=HEADERS).status_code == 422
    assert client.get("/api/dashboards").json() == []


def test_apply_accepts_an_empty_body(client):
    assert client.post("/api/templates/home/apply", headers=HEADERS).status_code == 201


def test_template_pages_export_and_import_again(client):
    new = client.post("/api/templates/monitoring/apply", json={}, headers=HEADERS).json()["dashboard"]
    doc = client.get(f"/api/dashboards/{new['id']}/export").json()
    again = client.post("/api/dashboards/import", json={"page": doc}, headers=HEADERS)
    assert again.status_code == 201 and again.json()["widget_count"] == len(new["widgets"])
