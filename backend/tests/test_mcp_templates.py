"""MCP tools for page templates and sharing (list_templates, apply_template, export_page, import_page).
Needs the `mcp` package; skipped without it (see test_mcp_server.py for the command)."""
import pytest

pytest.importorskip("mcp.server.fastmcp")
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from app import models  # noqa: E402
from conftest import HEADERS  # noqa: E402
from test_mcp_server import server, sp_fixture  # noqa: E402,F401  (the module under test and its client fixture)


def test_instructions_mention_templates(sp):
    assert "template" in sp.INSTRUCTIONS and "apply_template" in sp.INSTRUCTIONS


def test_list_templates(sp):
    items = sp.list_templates()
    assert [t["id"] for t in items] == ["home", "infrastructure", "media", "monitoring", "network", "smart-home"]
    assert all({"title", "description", "tags", "requires", "widget_count"} <= set(t) for t in items)
    assert all("layout" not in t and "widgets" not in t for t in items)


def test_apply_template_creates_a_page_and_reports_unresolved_integrations(sp, client, session):
    session.add(models.Integration(name="Jelly", type="jellyfin", config={}))
    session.commit()
    out = sp.apply_template("media", name="Movies")
    assert out["page"]["name"] == "Movies" and out["page"]["widgets"] == 9
    assert {u["ref"] for u in out["unresolved_integrations"]} == {"sonarr", "radarr", "qbittorrent", "sabnzbd"}
    assert [m["ref"] for m in out["matched_integrations"]] == ["jellyfin"]
    page = client.get(f"/api/dashboards/{out['page']['id']}").json()
    assert len(page["widgets"]) == 9


def test_apply_template_errors(sp):
    with pytest.raises(ToolError, match="Invalid template id"):  # never reaches another route
        sp.apply_template("../main")
    with pytest.raises(ToolError, match="HTTP 404"):
        sp.apply_template("nope")
    with pytest.raises(ToolError, match="service_mode"):
        sp.apply_template("home", service_mode="bogus")
    with pytest.raises(ToolError, match="422"):
        sp.apply_template("media", integration_map={"nope": None})


def test_export_then_import_page(sp, client):
    created = sp.apply_template("monitoring")["page"]
    doc = sp.export_page(created["id"])
    assert doc["format"] == "startpage-page" and len(doc["widgets"]) == created["widgets"]
    again = sp.import_page(doc, name="Copy")
    assert again["page"]["name"] == "Copy" and again["page"]["widgets"] == created["widgets"]
    assert len(client.get("/api/dashboards").json()) == 2


def test_import_page_rejects_bad_files_and_flags_custom_code(sp):
    with pytest.raises(ToolError, match="format"):
        sp.import_page({"format": "nope", "version": 1})
    doc = {"format": "startpage-page", "version": 1, "widgets": [{"type": "custom", "x": 0, "y": 0, "w": 4, "h": 3, "config": {"html": "hi"}}]}
    assert any("custom code" in w for w in sp.import_page(doc)["warnings"])


def test_custom_widget_can_be_added_with_add_widget(sp, client):
    page = client.post("/api/dashboards", json={"name": "P"}, headers=HEADERS).json()
    spec = sp.list_widget_types("custom")["custom"]
    assert {"html", "css", "js", "integration_id", "service_ids", "refresh_seconds", "transparent", "allow_network"} <= {f["key"] for f in spec["fields"]}
    w = sp.add_widget(page["id"], "custom", config={"html": "<b>hi</b>"})["widget"]
    assert w["config"]["allow_network"] is False and w["config"]["refresh_seconds"] == 0
    assert "warnings" not in sp.add_widget(page["id"], "custom", config={"js": "1"})
