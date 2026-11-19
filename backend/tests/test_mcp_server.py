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


def test_overview_masks_the_notification_url(sp, client):
    url = "https://discord.example/api/webhooks/1/SECRET"
    client.patch("/api/settings", json={"notify_type": "webhook", "notify_url": url}, headers=HEADERS)
    settings = sp.get_overview()["settings"]
    assert settings["notify_url"] == "********" and settings["notify_url_set"] is True
    assert "SECRET" not in str(sp.update_settings({"title": "T"}))  # the PATCH response is masked too


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


# ---------- authentication ----------
def _enable_auth(client):
    client.post("/api/auth/password", json={"new": "correct horse battery"}, headers=HEADERS)
    return client.post("/api/auth/tokens", json={"name": "mcp"}, headers=HEADERS).json()["token"]


def test_token_header_is_sent_when_env_is_set(sp, client, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    token = _enable_auth(client)
    monkeypatch.setattr(server, "client", TestClient(app))  # no session cookie: only the bearer can authenticate
    monkeypatch.setenv("STARTPAGE_TOKEN", token)
    assert isinstance(sp._call("GET", "/dashboards"), list)
    assert sp._headers()["Authorization"] == f"Bearer {token}"
    monkeypatch.delenv("STARTPAGE_TOKEN")
    assert "Authorization" not in sp._headers()


def test_401_becomes_a_helpful_tool_error(sp, client, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    _enable_auth(client)
    monkeypatch.setattr(server, "client", TestClient(app))
    monkeypatch.delenv("STARTPAGE_TOKEN", raising=False)
    with pytest.raises(ToolError, match=r"Settings -> Security.*STARTPAGE_TOKEN"):
        sp._call("GET", "/dashboards")
    monkeypatch.setenv("STARTPAGE_TOKEN", "sp_revoked")
    with pytest.raises(ToolError, match="rejected STARTPAGE_TOKEN"):
        sp._call("GET", "/dashboards")


# ---------- integrations ----------
def _json_upstream(monkeypatch, handler):
    """Point the `json` type at a mock upstream instead of the network."""
    from app.integrations.registry import SPECS

    monkeypatch.setattr(SPECS["json"], "client_factory", lambda ctx: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _stored_config(client, integration_id):
    return next(i for i in client.get("/api/config/export").json()["integrations"] if i["id"] == integration_id)["config"]


def test_list_integration_types_names_required_and_secret_fields(sp):
    types = {t["type"]: t for t in sp.list_integration_types()}
    assert {"docker", "uptime_kuma", "json", "host", "rss", "pihole", "prometheus"} <= set(types)
    pihole = types["pihole"]
    assert pihole["required"] == ["url"] and pihole["secret"] == ["password", "api_token"]
    assert pihole["provides"] == ["stats"] and pihole["actions"][0]["name"] == "set_blocking"
    assert [p["key"] for p in pihole["actions"][0]["payload"]] == ["enabled", "seconds"]
    assert next(f for f in pihole["fields"] if f["key"] == "version")["options"] == ["auto", "6", "5"]
    assert types["json"]["secret"] == ["headers"] and types["host"]["required"] == []


def test_integration_tools_never_show_secrets(sp, client, monkeypatch):
    _json_upstream(monkeypatch, lambda r: httpx.Response(200, json={"up": True}))
    out = sp.create_integration("Status", "json", {"url": "http://j/x", "headers": {"X-Key": "abc-SECRET"}})
    iid = out["integration"]["id"]
    assert out["status"]["ok"] is True and out["integration"]["config"]["headers"] == "********"
    listed = sp.list_integrations()
    assert listed[0]["config"]["headers"] == "********" and listed[0]["config"]["url"] == "http://j/x"
    assert listed[0]["status"]["ok"] is True
    assert "SECRET" not in str([out, listed, sp.get_integration_data(iid), sp.get_overview()])


def test_update_integration_merges_and_keeps_secrets(sp, client, monkeypatch):
    _json_upstream(monkeypatch, lambda r: httpx.Response(200, json={}))
    iid = sp.create_integration("Status", "json", {"url": "http://j/x", "headers": {"X-Key": "abc"}})["integration"]["id"]
    out = sp.update_integration(iid, config={"method": "POST"}, name="Renamed")
    assert out["integration"]["name"] == "Renamed" and out["integration"]["config"]["method"] == "POST"
    assert _stored_config(client, iid) == {"url": "http://j/x", "method": "POST", "headers": {"X-Key": "abc"}}  # secret kept
    sp.update_integration(iid, config={"headers": None, "method": None})  # null clears, secrets too
    assert _stored_config(client, iid) == {"url": "http://j/x"}
    with pytest.raises(ToolError, match="Nothing to change"):
        sp.update_integration(iid)
    with pytest.raises(ToolError, match="No integration"):
        sp.update_integration("missing", name="x")


def test_integration_errors_are_readable(sp):
    with pytest.raises(ToolError, match=r"HTTP 422.*config\.url"):
        sp.create_integration("Bad", "json", {"url": "ftp://x"})
    with pytest.raises(ToolError, match="HTTP 400"):
        sp.create_integration("Bad", "nope")


def test_test_integration_resolves_masked_secrets_and_saves_nothing(sp, client, monkeypatch):
    _json_upstream(monkeypatch, lambda r: httpx.Response(200, json={"key": r.headers.get("x-key")}))
    iid = sp.create_integration("Status", "json", {"url": "http://j/x", "headers": {"X-Key": "abc"}})["integration"]["id"]
    res = sp.test_integration("json", {"url": "http://j/x", "headers": "********"}, integration_id=iid)
    assert res["ok"] is True and res["sample"] == {"key": "abc"}
    assert sp.test_integration("json", {"url": "http://j/x"})["sample"] == {"key": None}
    assert len(sp.list_integrations()) == 1


def test_get_integration_data_is_trimmed(sp, monkeypatch):
    _json_upstream(monkeypatch, lambda r: httpx.Response(200, json={"rows": list(range(50)), "text": "x" * 500}))
    iid = sp.create_integration("Big", "json", {"url": "http://j/x"})["integration"]["id"]
    data = sp.get_integration_data(iid)["data"]
    assert data["rows"][:10] == list(range(10)) and data["rows"][10] == "… 40 more" and len(data["rows"]) == 11
    assert data["text"].endswith("<500 chars>")
    with pytest.raises(ToolError, match="No integration"):
        sp.get_integration_data("missing")


def test_integration_action_and_delete(sp, client, monkeypatch):
    from app.integrations.registry import SPECS

    posted = []

    def handler(request):
        if request.method == "POST":
            posted.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(200, json=[] if request.url.path.endswith("json") else {"Name": "nas"})

    monkeypatch.setattr(SPECS["docker"], "client_factory", lambda ctx: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    iid = sp.create_integration("Docker", "docker", {"url": "http://dock:2375"})["integration"]["id"]
    res = sp.integration_action(iid, "container", {"id": "web", "action": "restart"})
    assert res["ok"] is True and posted == ["/containers/web/restart"]
    with pytest.raises(ToolError, match="HTTP 404"):
        sp.integration_action(iid, "nope")
    with pytest.raises(ToolError, match=r"HTTP 422.*action"):
        sp.integration_action(iid, "container", {"id": "web", "action": "explode"})
    assert "Deleted" in sp.delete_integration(iid)
    assert sp.list_integrations() == []


def test_server_instructions_mention_integrations(sp):
    assert "list_integration_types" in sp.INSTRUCTIONS and "write-only" in sp.INSTRUCTIONS


# ---------- service discovery ----------
def _docker_with(monkeypatch, containers):
    from app.integrations.registry import SPECS

    def handler(request):
        return httpx.Response(200, json={"Name": "nas"} if request.url.path.endswith("/info") else containers)

    monkeypatch.setattr(SPECS["docker"], "client_factory", lambda ctx: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


_JELLY = {"Id": "a" * 64, "Names": ["/jf"], "Image": "jellyfin/jellyfin", "State": "running",
          "Labels": {"startpage.group": "Media"}, "Ports": [{"IP": "0.0.0.0", "PrivatePort": 8096, "PublicPort": 8096, "Type": "tcp"}],
          "HostConfig": {"NetworkMode": "bridge"}}
_DB = {**_JELLY, "Id": "b" * 64, "Names": ["/db"], "Image": "postgres:16", "Labels": {}}


def test_discover_and_import_services_round_trip(sp, monkeypatch):
    _docker_with(monkeypatch, [_JELLY, _DB])
    sp.create_integration("Docker", "docker", {"url": "http://nas.lan:2375"})
    found = sp.discover_services()
    assert [c["name"] for c in found["candidates"]] == ["Jf"] and found["skipped"][0]["container_name"] == "db"
    c = found["candidates"][0]
    assert c["url"] == "http://nas.lan:8096" and c["category"] == "Media" and c["container"] == "jf" and "source" not in c
    fields = {k: c[k] for k in ("name", "protocol", "host", "port", "category")}
    res = sp.import_discovered_services([{"key": c["key"], "action": "create", "fields": {**fields, "name": "Jellyfin"}},
                                         {"key": "bad", "action": "create", "fields": {"name": "x", "host": "no good"}}])
    assert [r["name"] for r in res["created"]] == ["Jellyfin"] and res["created"][0]["url"] == "http://nas.lan:8096"
    assert res["errors"][0]["key"] == "bad"
    again = sp.discover_services(default_host="ignored.lan")["candidates"][0]
    assert again["existing_service_id"] == res["created"][0]["id"]
    assert [s["name"] for s in sp.list_services()] == ["Jellyfin"]


def test_discover_services_without_docker_and_with_bad_input(sp):
    assert sp.discover_services() == {"candidates": [], "skipped": [], "errors": []}
    with pytest.raises(ToolError, match="HTTP 404"):
        sp.discover_services(integration_id="missing")
    with pytest.raises(ToolError, match="HTTP 422"):
        sp.discover_services(default_host="not a host")


def test_discovery_needs_the_token_when_auth_is_on(sp, client, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    token = _enable_auth(client)
    monkeypatch.setattr(server, "client", TestClient(app))
    monkeypatch.delenv("STARTPAGE_TOKEN", raising=False)
    with pytest.raises(ToolError, match="requires authentication"):
        sp.discover_services()
    monkeypatch.setenv("STARTPAGE_TOKEN", token)
    assert sp.discover_services()["candidates"] == []


def test_server_instructions_mention_discovery(sp):
    assert "discover_services" in sp.INSTRUCTIONS and "confirms" in sp.INSTRUCTIONS


# ---------- phone layouts ----------
def test_get_page_reports_phone_layout_with_defaults(sp, page):
    w = sp.add_widget(page["id"], "clock")["widget"]
    got = sp.get_page(page["id"])
    assert (got["columns_sm"], got["phone_layout"]) == (4, "auto")
    assert got["widgets"][0]["layout_sm"] is None
    sp.update_page(page["id"], columns_sm=3, phone_layout="custom")
    sp.set_layout(page["id"], [{"id": w["id"], "x": 1, "y": 0, "w": 2, "h": 2, "hidden": True}], breakpoint="sm")
    got = sp.get_page(page["id"])
    assert (got["columns_sm"], got["phone_layout"]) == (3, "custom")
    assert got["widgets"][0]["layout_sm"] == {"x": 1, "y": 0, "w": 2, "h": 2, "hidden": True}


def test_set_layout_breakpoint_sm_leaves_desktop_alone_and_validates(sp, page):
    w = sp.add_widget(page["id"], "clock")["widget"]
    out = sp.set_layout(page["id"], [{"id": w["id"], "x": 0, "y": 0, "w": 4, "h": 2}], breakpoint="sm")
    assert "phone layout" in out
    got = sp.get_page(page["id"])["widgets"][0]
    assert (got["x"], got["y"], got["w"]) == (w["x"], w["y"], w["w"])
    assert got["layout_sm"]["w"] == 4
    with pytest.raises(ToolError, match="HTTP 422"):
        sp.set_layout(page["id"], [{"id": w["id"], "x": 2, "y": 0, "w": 4, "h": 2}], breakpoint="sm")  # 2 + 4 > 4 columns
    with pytest.raises(ToolError, match="breakpoint"):
        sp.set_layout(page["id"], [], breakpoint="md")
    sp.set_layout(page["id"], [{"id": w["id"], "x": 5, "y": 5, "w": 2, "h": 2}])  # default stays the desktop grid
    got = sp.get_page(page["id"])["widgets"][0]
    assert (got["x"], got["y"]) == (5, 5) and got["layout_sm"]["w"] == 4


def test_update_page_phone_settings_merge_and_validate(sp, page):
    sp.update_page(page["id"], settings={"gap": 7})
    sp.update_page(page["id"], phone_layout="custom")
    settings = sp.get_page(page["id"])["settings"]
    assert settings["gap"] == 7 and settings["phone_layout"] == "custom"
    with pytest.raises(ToolError, match="columns_sm"):
        sp.update_page(page["id"], columns_sm=13)
    with pytest.raises(ToolError, match="phone_layout"):
        sp.update_page(page["id"], phone_layout="tablet")
    with pytest.raises(ToolError, match="Nothing to change"):
        sp.update_page(page["id"])


def test_server_instructions_mention_phone_layout(sp):
    assert "layout_sm" in sp.INSTRUCTIONS and 'breakpoint="sm"' in sp.INSTRUCTIONS
