"""MCP server hardening: path traversal through tool arguments, secrets and size in answers, error handling.

Needs the `mcp` package (see test_mcp_server.py for the uv command); skips without it.
"""

import importlib.util
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp.server.fastmcp")
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from conftest import HEADERS  # noqa: E402

_PATH = Path(__file__).resolve().parents[2] / "mcp" / "startpage_mcp.py"
_spec = importlib.util.spec_from_file_location("startpage_mcp_hardening", _PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

EVIL = ["../config/export", "..", "../backups/x.json", "a/../../config/export", "%2e%2e/config/export", "..%2fconfig%2fexport",
        "a/b", "a?x=1", "a#b", "a\\b", "", " ", "x" * 300, "a b", "a%00", "..\\config"]


class Spy:
    """Wraps the test client and records every path that is requested."""

    def __init__(self, inner):
        self.inner, self.paths = inner, []

    def request(self, method, url, **kw):
        self.paths.append((method, url))
        return self.inner.request(method, url, **kw)


@pytest.fixture(name="sp")
def sp_fixture(client, monkeypatch):
    spy = Spy(client)
    monkeypatch.setattr(server, "client", spy)
    monkeypatch.delenv("STARTPAGE_TOKEN", raising=False)
    server.spy = spy
    return server


@pytest.fixture(name="page")
def page_fixture(client):
    return client.post("/api/dashboards", json={"name": "Home"}, headers=HEADERS).json()


TOOLS = [
    ("get_page", lambda sp, bad: sp.get_page(bad)),
    ("add_widget", lambda sp, bad: sp.add_widget(bad, "clock")),
    ("update_widget", lambda sp, bad: sp.update_widget(bad, x=1)),
    ("set_layout", lambda sp, bad: sp.set_layout(bad, [])),
    ("duplicate_widget", lambda sp, bad: sp.duplicate_widget(bad)),
    ("delete_widget", lambda sp, bad: sp.delete_widget(bad)),
    ("clear_phone_layout", lambda sp, bad: sp.clear_phone_layout(bad)),
    ("update_page", lambda sp, bad: sp.update_page(bad, name="x")),
    ("delete_page", lambda sp, bad: sp.delete_page(bad)),
    ("update_service", lambda sp, bad: sp.update_service(bad, {"name": "x"})),
    ("discover_services", lambda sp, bad: sp.discover_services(integration_id=bad)),
    ("update_integration", lambda sp, bad: sp.update_integration(bad, name="x")),
    ("delete_integration", lambda sp, bad: sp.delete_integration(bad)),
    ("get_integration_data", lambda sp, bad: sp.get_integration_data(bad)),
    ("integration_action", lambda sp, bad: sp.integration_action(bad, "set_blocking")),
    ("integration_action_name", lambda sp, bad: sp.integration_action("ok", bad)),
    ("restore_backup", lambda sp, bad: sp.restore_backup(bad)),
    ("apply_template", lambda sp, bad: sp.apply_template(bad)),
    ("export_page", lambda sp, bad: sp.export_page(bad)),
]


@pytest.mark.parametrize("name,call", TOOLS, ids=[t[0] for t in TOOLS])
def test_every_tool_refuses_path_tricks_before_any_request(sp, page, name, call):
    for bad in EVIL:
        if name == "discover_services" and bad == "":
            continue  # an empty integration_id means "all integrations"
        sp.spy.paths.clear()
        with pytest.raises(ToolError):
            call(sp, bad)
        sent = [p for _, p in sp.spy.paths if (bad and bad in p) or "config" in p or ".." in p]
        assert not sent, (name, bad, sent)


def test_the_traversal_from_the_review_no_longer_reaches_the_export(sp, client):
    client.post("/api/integrations", json={"name": "J", "type": "jellyfin", "config": {"url": "http://x", "api_key": "TOPSECRET"}}, headers=HEADERS)
    for call in (lambda: sp.get_page("../config/export"), lambda: sp.get_page("..%2fconfig%2fexport"),
                 lambda: sp.delete_widget("../dashboards/x")):
        with pytest.raises(ToolError) as e:
            call()
        assert "TOPSECRET" not in str(e.value)
    assert all("config" not in p for _, p in sp.spy.paths)


def test_call_itself_refuses_traversal_and_forbidden_routes(sp):
    for path in ("/../config/export", "/dashboards/../config/export", "/dashboards/%2e%2e/x", "/config/export", "/config/import",
                 "/backups/20260101.json", "//config/export"):
        with pytest.raises(ToolError, match="Refused"):
            sp._call("GET", path)
    assert sp._call("GET", "/dashboards") == []  # normal routes still work


def test_ids_that_the_app_uses_are_accepted(sp):
    for ok in ("a1b2c3d4e5f6", "home", "smart-home", "20260102-030405123456-claude.json", "my_page.2"):
        assert sp._seg(ok) == ok


def test_restore_backup_returns_a_summary_without_secrets(sp, client, session, monkeypatch, tmp_path):
    from app import backups, models
    from app.routers.config import export_config

    monkeypatch.setattr(backups, "BACKUP_DIR", tmp_path)  # never the real data dir
    monkeypatch.setattr(backups, "_export_bundle", lambda: export_config(session).model_dump())

    session.add(models.Integration(id="i1", name="J", type="jellyfin", config={"url": "http://x", "api_key": "TOPSECRET"}))
    session.commit()
    client.post("/api/settings", json={}, headers=HEADERS)
    client.patch("/api/settings", json={"notify_type": "webhook", "notify_url": "https://hooks.example/NOTIFYSECRET"}, headers=HEADERS)
    name = sp.create_backup("t")["name"]
    out = sp.restore_backup(name)
    assert set(out) == {"counts", "warnings"} and out["counts"]["integrations"] == 1
    assert "TOPSECRET" not in repr(out) and "NOTIFYSECRET" not in repr(out)
    # and the HTTP route answers with the same summary, not the bundle
    res = client.post(f"/api/backups/{name}/restore", headers=HEADERS)
    assert res.status_code == 200 and set(res.json()) == {"counts", "warnings"}
    assert "TOPSECRET" not in res.text and "NOTIFYSECRET" not in res.text


def test_no_tool_returns_an_unmasked_secret(sp, client, session):
    from app import models

    session.add(models.Integration(id="i1", name="J", type="jellyfin", config={"url": "http://x", "api_key": "TOPSECRET"}))
    session.commit()
    for out in (sp.list_integrations(), sp.get_overview(), sp.update_integration("i1", name="J2")):
        assert "TOPSECRET" not in repr(out)
    assert sp._scrub({"a": {"api_key": "zzz", "notify_url": "https://x/s", "name": "n"}, "l": [{"token": "t"}]}) == {
        "a": {"api_key": "********", "notify_url": "********", "name": "n"}, "l": [{"token": "********"}]}
    tools = {t for t in dir(sp) if not t.startswith("_")}
    assert "export_config" not in tools and "get_backup" not in tools  # the whole-config export is not a tool


def test_list_backups_returns_only_names_and_sizes(sp, monkeypatch):
    monkeypatch.setattr(sp, "_call", lambda *a, **k: [{"name": "a.json", "size": 1, "modified": 2, "extra": "x"}])
    assert sp.list_backups() == [{"name": "a.json", "size": 1, "modified": 2}]


def test_clear_phone_layout_removes_one_placement(sp, client, page):
    w = sp.add_widget(page["id"], "clock", x=0, y=0, w=2, h=2)["widget"]
    client.patch(f"/api/widgets/{w['id']}", json={"layout_sm": {"x": 0, "y": 0, "w": 2, "h": 2}}, headers=HEADERS)
    assert client.get(f"/api/dashboards/{page['id']}").json()["widgets"][0]["layout_sm"]
    out = sp.clear_phone_layout(w["id"])
    assert out["layout_sm"] is None
    assert client.get(f"/api/dashboards/{page['id']}").json()["widgets"][0]["layout_sm"] is None


# ---------------------------------------------------------------- _call error handling
def _server_with(monkeypatch, handler):
    monkeypatch.setattr(server, "client", httpx.Client(base_url="http://app.test", transport=httpx.MockTransport(handler)))
    monkeypatch.delenv("STARTPAGE_TOKEN", raising=False)
    return server


def test_a_redirect_from_an_sso_proxy_is_a_clear_error(monkeypatch):
    sp = _server_with(monkeypatch, lambda r: httpx.Response(302, headers={"location": "https://sso.example.com/login?next=secret"}))
    with pytest.raises(ToolError) as e:
        sp._call("GET", "/dashboards")
    assert "redirect" in str(e.value) and "sso.example.com" in str(e.value) and "secret" not in str(e.value)


def test_long_and_odd_error_bodies_are_capped(monkeypatch):
    sp = _server_with(monkeypatch, lambda r: httpx.Response(500, text="E" * 50000))
    with pytest.raises(ToolError) as e:
        sp._call("GET", "/dashboards")
    assert len(str(e.value)) < 700
    sp = _server_with(monkeypatch, lambda r: httpx.Response(400, json=["a", {"b": 1}]))
    with pytest.raises(ToolError, match="HTTP 400"):
        sp._call("GET", "/dashboards")
    sp = _server_with(monkeypatch, lambda r: httpx.Response(422, json={"detail": {"x": "y" * 9999}}))
    with pytest.raises(ToolError) as e:
        sp._call("GET", "/dashboards")
    assert len(str(e.value)) < 700


def test_a_non_json_success_answer_is_a_clear_error(monkeypatch):
    sp = _server_with(monkeypatch, lambda r: httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"}))
    with pytest.raises(ToolError, match="not JSON"):
        sp._call("GET", "/dashboards")


def test_transport_errors_do_not_echo_userinfo_from_the_base_url(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("refused")

    sp = _server_with(monkeypatch, boom)
    monkeypatch.setattr(sp, "BASE_URL", "http://admin:hunter2@nas.lan:8471")
    with pytest.raises(ToolError) as e:
        sp._call("GET", "/dashboards")
    assert "hunter2" not in str(e.value) and "admin" not in str(e.value) and "nas.lan:8471" in str(e.value)
