"""Tests for the Claude MCP server (mcp/startpage_mcp.py) against the in-memory app.

The server needs the `mcp` package, which the backend doesn't install, so this module
skips unless run with it, e.g. from the repo root:
  uv run --no-project --with "mcp>=1.10,<2" --with-requirements backend/requirements.txt \
    --with-requirements backend/requirements-dev.txt pytest backend/tests/test_mcp_server.py -q
"""

import importlib.util
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp.server.fastmcp")
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from conftest import HEADERS  # noqa: E402

_PATH = Path(__file__).resolve().parents[2] / "mcp" / "startpage_mcp.py"
_spec = importlib.util.spec_from_file_location("startpage_mcp", _PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


@pytest.fixture(name="sp")
def sp_fixture(client, monkeypatch):
    monkeypatch.setattr(server, "client", client)
    return server


@pytest.fixture(name="page")
def page_fixture(client):
    res = client.post("/api/dashboards", json={"name": "Home"}, headers=HEADERS)
    return res.json()


def test_add_widget_fills_defaults_and_auto_places(sp, page):
    first = sp.add_widget(page["id"], "clock")["widget"]
    size = sp.list_widget_types("clock")["clock"]["size"]
    assert (first["x"], first["y"], first["w"], first["h"]) == (0, 0, size["w"], size["h"])
    second = sp.add_widget(page["id"], "clock")["widget"]
    assert second["x"] >= first["x"] + first["w"] or second["y"] >= first["y"] + first["h"]

    rss = sp.add_widget(page["id"], "rss")["widget"]
    assert rss["config"]["max_items"] == 10  # catalog default


def test_add_widget_rejects_unknown_type_and_warns(sp, page):
    with pytest.raises(ToolError, match="Unknown widget type"):
        sp.add_widget(page["id"], "nope")
    out = sp.add_widget(page["id"], "service", config={"bogus": 1})
    assert any("bogus" in w for w in out["warnings"])
    assert any("service_id" in w for w in out["warnings"])


def test_update_widget_merges_config(sp, page):
    w = sp.add_widget(page["id"], "link", config={"name": "A", "url": "https://a.example"})["widget"]
    updated = sp.update_widget(w["id"], config={"name": "B", "url": None}, x=5)
    assert updated["config"]["name"] == "B"
    assert "url" not in updated["config"]
    assert updated["x"] == 5


def test_writes_are_external_and_errors_are_readable(sp, page, client):
    before = client.get("/api/revision").json()["rev"]
    sp.create_page("Second")
    assert client.get("/api/revision").json()["rev"] == before + 1
    with pytest.raises(ToolError, match="HTTP 404"):
        sp.delete_widget("missing")


def test_overview_hides_integration_config(sp, page):
    overview = sp.get_overview()
    assert overview["pages"][0]["name"] == "Home"
    assert all(set(i) == {"id", "name", "type"} for i in overview["integrations"])


def test_unreachable_app_explains_how_to_start(monkeypatch):
    monkeypatch.setattr(server, "client", httpx.Client(base_url="http://127.0.0.1:9", timeout=2))
    with pytest.raises(ToolError, match="isn't reachable"):
        server.get_overview()


def test_find_free_spot_skips_taken_cells():
    taken = [{"x": 0, "y": 0, "w": 24, "h": 2}, {"x": 0, "y": 2, "w": 10, "h": 3}]
    assert server.find_free_spot(taken, 4, 2, 24) == {"x": 10, "y": 2}


# ---------- 2026-11-18 bug run ----------
def test_set_layout_refuses_ids_that_are_not_on_the_page(sp, page):
    w = sp.add_widget(page["id"], "clock")["widget"]
    with pytest.raises(ToolError, match="Not on page"):
        sp.set_layout(page["id"], [{"id": w["id"], "x": 9, "y": 9, "w": 2, "h": 2}, {"id": "typo", "x": 0, "y": 0, "w": 1, "h": 1}])
    assert sp.get_page(page["id"])["widgets"][0]["x"] == w["x"]  # nothing moved
    assert "1 widget" in sp.set_layout(page["id"], [{"id": w["id"], "x": 9, "y": 9, "w": 2, "h": 2}])
    assert sp.get_page(page["id"])["widgets"][0]["x"] == 9


def test_update_service_rejects_unknown_fields(sp):
    svc = sp.create_service("Jellyfin", "192.168.1.20", port=8096)
    with pytest.raises(ToolError, match="url"):
        sp.update_service(svc["id"], {"url": "http://x:8080"})
    assert sp.update_service(svc["id"], {"port": 9000})["port"] == 9000


def test_update_widget_can_unlink_its_service(sp, page):
    svc = sp.create_service("Jellyfin", "192.168.1.20")
    w = sp.add_widget(page["id"], "service", service_id=svc["id"])["widget"]
    assert w["service_id"] == svc["id"]
    assert sp.update_widget(w["id"], service_id="")["service_id"] is None
