"""Fixture tests for every integration type: recorded-style upstream responses through an
httpx.MockTransport. The five ported types are compared with their original payloads exactly
(only `stats` / `items` were added alongside)."""
import httpx
import pytest

from app.integrations import IntegrationError, safe_error
from app.integrations.types import host as host_mod
from app.integrations.types import prometheus as prom_mod
from integration_utils import Harness, run_fetch, without


# ---------------------------------------------------------------- docker
DOCKER_CONTAINERS = [
    {"Id": "b" * 64, "Names": ["/web"], "Image": "nginx:1.27", "State": "running", "Status": "Up 2 hours",
     "Ports": [{"PrivatePort": 80, "PublicPort": 8080, "Type": "tcp"}, {"PrivatePort": 80, "PublicPort": 8080, "Type": "tcp"}],
     "Labels": {"com.docker.compose.project": "site"}},
    {"Id": "a" * 64, "Names": ["/db"], "Image": "postgres:16", "State": "exited", "Status": "Exited (0) 3 days ago",
     "Ports": [{"PrivatePort": 5432}], "Labels": {}},
]
DOCKER_INFO = {"Name": "nas", "ServerVersion": "27.3.1", "ContainersRunning": 1, "ContainersStopped": 1,
               "ContainersPaused": 0, "Containers": 2, "NCPU": 4, "MemTotal": 8_000_000_000}


def test_docker_payload_is_unchanged():
    data = run_fetch("docker", {"url": "http://dock:2375"}, {
        ("GET", r"/containers/json\?all=1"): DOCKER_CONTAINERS,
        ("GET", r"/info$"): DOCKER_INFO,
    })
    assert without(data, "stats") == {
        "host": "nas", "version": "27.3.1", "running": 1, "stopped": 1, "paused": 0, "total": 2, "cpus": 4,
        "mem_total": 8_000_000_000,
        "containers": [  # sorted by (project, name): "" before "site"
            {"id": "a" * 12, "name": "db", "image": "postgres:16", "state": "exited", "status": "Exited (0) 3 days ago",
             "ports": [], "project": ""},
            {"id": "b" * 12, "name": "web", "image": "nginx:1.27", "state": "running", "status": "Up 2 hours",
             "ports": ["8080->80"], "project": "site"},
        ],
    }
    assert [s["key"] for s in data["stats"]] == ["running", "stopped", "total"]


def test_docker_hides_stopped_when_asked_and_sends_the_container_action():
    h = Harness("docker", {"url": "http://dock:2375", "show_all": False}, {
        ("GET", r"/containers/json"): [], ("GET", r"/info"): DOCKER_INFO,
        ("POST", r"/containers/web/restart$"): httpx.Response(204),
    })
    h.fetch()
    assert h.calls[0][1].endswith("/containers/json?all=0")
    h.action("container", {"id": "web", "action": "restart"})
    assert h.calls[-1] == ("POST", "http://dock:2375/containers/web/restart")
    h.close()


# ---------------------------------------------------------------- uptime kuma
KUMA_PAGE = {"config": {"title": "Homelab"}, "publicGroupList": [
    {"name": "Core", "monitorList": [{"id": 1, "name": "Router"}, {"id": 2, "name": "NAS"}]},
    {"name": "Apps", "monitorList": [{"id": 3, "name": "Wiki"}]},
]}
KUMA_BEATS = {"heartbeatList": {
    "1": [{"status": 1, "ping": 3}, {"status": 1, "ping": 4}],
    "2": [{"status": 1, "ping": 9}, {"status": 0, "ping": None}],
}, "uptimeList": {"1_24": 1, "2_24": 0.5}}


def test_uptime_kuma_payload_is_unchanged():
    data = run_fetch("uptime_kuma", {"url": "http://kuma:3001/", "slug": "lab"}, {
        ("GET", r"/api/status-page/lab$"): KUMA_PAGE,
        ("GET", r"/api/status-page/heartbeat/lab$"): KUMA_BEATS,
    })
    assert without(data, "stats", "items") == {
        "title": "Homelab", "up": 1, "total": 3,
        "monitors": [
            {"id": 1, "name": "Router", "group": "Core", "status": "online", "ping": 4, "uptime_24h": 1, "history": [1, 1]},
            {"id": 2, "name": "NAS", "group": "Core", "status": "offline", "ping": None, "uptime_24h": 0.5, "history": [1, 0]},
            {"id": 3, "name": "Wiki", "group": "Apps", "status": "unknown", "ping": None, "uptime_24h": None, "history": []},
        ],
    }
    assert {s["key"]: s["value"] for s in data["stats"]} == {"up": 1, "down": 1, "total": 3}
    assert data["items"][1] == {"title": "NAS", "subtitle": "Core", "status": "offline"}
    assert data["items"][0]["meta"] == "4 ms"


# ---------------------------------------------------------------- json
def test_json_returns_the_response_untouched_and_sends_headers():
    seen = {}

    def respond(request):
        seen["auth"] = request.headers.get("authorization")
        return {"online": True, "players": {"online": 3}}

    data = run_fetch("json", {"url": "http://game:8080/status.json", "headers": {"Authorization": "Bearer k"}},
                     {("GET", r"/status\.json$"): respond})
    assert data == {"online": True, "players": {"online": 3}}
    assert seen["auth"] == "Bearer k"


def test_json_wraps_a_non_json_body_and_keeps_a_trailing_slash():
    h = Harness("json", {"url": "http://game:8080/status/", "method": "POST"}, {("POST", r"/status/$"): "plain text"})
    assert h.fetch() == {"text": "plain text"}
    assert h.calls == [("POST", "http://game:8080/status/")]
    h.close()


def test_json_http_error_is_reported_safely():
    h = Harness("json", {"url": "http://game:8080/status.json?key=SECRET"}, {("GET", r"status"): (403, {"error": "no"})})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        h.fetch()
    assert safe_error(exc.value, h.ctx.cfg) == "HTTP 403 Forbidden (game:8080)"
    h.close()


# ---------------------------------------------------------------- host
class _Mem:
    percent, used, total = 50.0, 4_000, 8_000


class _Disk:
    percent, used, total = 91.0, 910, 1_000


class _FakePsutil:
    @staticmethod
    def virtual_memory():
        return _Mem

    @staticmethod
    def disk_usage(path):
        return _Disk

    @staticmethod
    def cpu_percent(interval):
        return 12.5


def test_host_payload_is_unchanged():
    data = host_mod._read_system(_FakePsutil)
    assert without(data, "stats", "hostname") == {
        "cpu_percent": 12.5, "mem_percent": 50.0, "mem_used": 4_000, "mem_total": 8_000,
        "disk_percent": 91.0, "disk_used": 910, "disk_total": 1_000,
    }
    assert [(s["key"], s["tone"]) for s in data["stats"]] == [("cpu", "ok"), ("mem", "ok"), ("disk", "bad")]


def test_host_fetch_runs_for_real():
    data = run_fetch("host", {}, {})
    assert {"hostname", "cpu_percent", "mem_percent", "disk_total"} <= set(data)


# ---------------------------------------------------------------- rss
RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example Feed</title>
<item><title>First post</title><link>https://example.com/1</link><description>Hello &amp; welcome</description>
<pubDate>Wed, 02 Oct 2024 08:00:00 GMT</pubDate></item>
<item><title>Second</title><link>javascript:alert(1)</link></item>
</channel></rss>"""


def test_rss_payload_is_unchanged():
    pytest.importorskip("feedparser")
    data = run_fetch("rss", {"url": "https://example.com/feed.xml"}, {("GET", r"feed\.xml$"): RSS_XML})
    assert without(data, "items") == {
        "title": "Example Feed",
        "entries": [
            {"title": "First post", "link": "https://example.com/1", "published_parsed": 1727856000, "summary": "Hello & welcome"},
            {"title": "Second", "link": "javascript:alert(1)", "published_parsed": None, "summary": ""},
        ],
    }
    # Only http(s) links become item urls.
    assert data["items"] == [{"title": "First post", "url": "https://example.com/1"}, {"title": "Second"}]


# ---------------------------------------------------------------- pi-hole
V6_SUMMARY = {
    "queries": {"total": 12345, "blocked": 2345, "percent_blocked": 18.99, "unique_domains": 800, "forwarded": 7000,
                "cached": 3000, "frequency": 0.5},
    "clients": {"active": 7, "total": 12},
    "gravity": {"domains_being_blocked": 98765, "last_update": 1730000000},
    "took": 0.001,
}
V6_BLOCKING = {"blocking": "enabled", "timer": None, "took": 0.0001}
V5_SUMMARY = {
    "domains_being_blocked": 98765, "dns_queries_today": 12345, "ads_blocked_today": 2345, "ads_percentage_today": 18.99,
    "unique_domains": 800, "queries_forwarded": 7000, "queries_cached": 3000, "clients_ever_seen": 12,
    "unique_clients": 7, "status": "enabled",
}


def _stats(data):
    return {s["key"]: s for s in data["stats"]}


def _pihole_v6_routes(auth_calls):
    def auth(request):
        auth_calls.append(request.content)
        return {"session": {"valid": True, "totp": False, "sid": f"sid{len(auth_calls)}", "csrf": "c", "validity": 300}}

    def need_sid(body):
        return lambda request: body if request.headers.get("x-ftl-sid") == f"sid{len(auth_calls)}" else (401, {"error": {"key": "unauthorized"}})

    return {
        ("GET", r"/api/auth$"): (401, {"session": {"valid": False}}),
        ("POST", r"/api/auth$"): auth,
        ("GET", r"/api/stats/summary$"): need_sid(V6_SUMMARY),
        ("GET", r"/api/dns/blocking$"): need_sid(V6_BLOCKING),
    }


def test_pihole_v6_detects_the_version_and_reports_stats():
    auth_calls = []
    h = Harness("pihole", {"url": "pi.hole/", "password": "pw"}, _pihole_v6_routes(auth_calls))
    data = h.fetch()
    assert data["version"] == 6 and data["blocking"] == {"enabled": True, "timer": None}
    s = _stats(data)
    assert (s["queries"]["value"], s["blocked"]["value"], s["percent_blocked"]["value"], s["domains"]["value"]) == (12345, 2345, 19.0, 98765)
    assert s["status"]["value"] == "enabled" and s["status"]["tone"] == "ok"
    assert h.calls[0] == ("GET", "http://pi.hole/api/auth")  # missing scheme and trailing slash tolerated
    assert b'"password":"pw"' in auth_calls[0].replace(b" ", b"")
    h.close()


def test_pihole_v6_logs_in_again_when_the_session_expires():
    auth_calls = []
    h = Harness("pihole", {"url": "http://pi.hole", "version": "6", "password": "pw"}, _pihole_v6_routes(auth_calls))
    h.fetch()
    assert len(auth_calls) == 1
    h.fetch()  # same session: no second login
    assert len(auth_calls) == 1
    h.ctx.state["sid"] = "expired"  # the server no longer knows this session: it answers 401
    data = h.fetch()
    assert len(auth_calls) == 2 and h.ctx.state["sid"] == "sid2"
    assert _stats(data)["queries"]["value"] == 12345
    h.close()


def test_pihole_v6_wrong_password_is_a_safe_error():
    h = Harness("pihole", {"url": "http://pi.hole", "version": "6", "password": "nope"},
                {("POST", r"/api/auth$"): (401, {"session": {"valid": False}})})
    with pytest.raises(IntegrationError, match="check the Pi-hole password"):
        h.fetch()
    h.close()


def test_pihole_v5_uses_summary_raw_with_the_token():
    h = Harness("pihole", {"url": "http://pi.hole:8080", "api_token": "TOK"}, {
        ("GET", r"/api/auth$"): (404, "<html>not found</html>"),
        ("GET", r"/admin/api\.php\?summaryRaw=&auth=TOK$"): V5_SUMMARY,
    })
    data = h.fetch()
    assert data["version"] == 5 and data["blocking"] == {"enabled": True, "timer": None}
    s = _stats(data)
    assert (s["queries"]["value"], s["blocked"]["value"], s["domains"]["value"], s["clients"]["value"]) == (12345, 2345, 98765, 7)
    assert h.ctx.state["version"] == 5
    h.close()


def test_pihole_v5_without_a_token_still_reads_the_public_summary():
    h = Harness("pihole", {"url": "http://pi.hole", "version": "5"}, {("GET", r"/admin/api\.php\?summaryRaw=$"): V5_SUMMARY})
    assert _stats(h.fetch())["status"]["value"] == "enabled"
    h.close()


def test_pihole_set_blocking_v6_and_v5():
    sent = {}
    auth_calls = []
    routes = _pihole_v6_routes(auth_calls)
    routes[("POST", r"/api/dns/blocking$")] = lambda r: (sent.update(body=r.content), {"blocking": "disabled", "timer": 300})[1]
    h = Harness("pihole", {"url": "http://pi.hole", "version": "6", "password": "pw"}, routes)
    h.action("set_blocking", {"enabled": False, "seconds": 300})
    assert sent["body"].replace(b" ", b"") == b'{"blocking":false,"timer":300}'
    h.action("set_blocking", {"enabled": True})
    assert sent["body"].replace(b" ", b"") == b'{"blocking":true,"timer":null}'
    h.close()

    h = Harness("pihole", {"url": "http://pi.hole", "version": "5", "api_token": "TOK"}, {
        ("GET", r"/admin/api\.php\?disable=300&auth=TOK$"): {"status": "disabled"},
        ("GET", r"/admin/api\.php\?enable=&auth=TOK$"): {"status": "enabled"},
    })
    h.action("set_blocking", {"enabled": False, "seconds": 300})
    h.action("set_blocking", {"enabled": True})
    h.close()

    h = Harness("pihole", {"url": "http://pi.hole", "version": "5"}, {})
    with pytest.raises(IntegrationError, match="API token is required"):
        h.action("set_blocking", {"enabled": False})
    h.close()


# ---------------------------------------------------------------- prometheus
def _vector(value):
    return {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {"job": "node"}, "value": [1730000000.1, value]}]}}


def by_query(table):
    """Route /api/v1/query by its `query` parameter."""
    def respond(request):
        value = table[request.url.params["query"]]
        return value(request) if callable(value) else value
    return respond


def test_prometheus_instant_queries_give_one_stat_each():
    queries = 'Up = sum(up) | targets\nCPU = 100 - avg(rate(x{mode="idle"}[5m])) * 100 | %\n# note\nNone = nothing'
    h = Harness("prometheus", {"url": "http://prom:9090", "queries": queries}, {
        ("GET", r"/api/v1/query\?"): by_query({
            "sum(up)": _vector("14"),
            '100 - avg(rate(x{mode="idle"}[5m])) * 100': _vector("37.123456"),
            "nothing": {"status": "success", "data": {"resultType": "vector", "result": []}},
        }),
    })
    data = h.fetch()
    assert data["stats"] == [
        {"key": "up", "label": "Up", "value": 14.0, "unit": "targets"},
        {"key": "cpu", "label": "CPU", "value": 37.1235, "unit": "%"},
        {"key": "none", "label": "None", "value": None},
    ]
    assert not any("query_range" in url for _, url in h.calls)
    h.close()


def test_prometheus_range_mode_adds_a_series_and_auth_is_sent():
    seen = []

    def vector(request):
        seen.append(request.headers.get("authorization"))
        return _vector("5")

    def matrix(request):
        seen.append(request.url.params["step"])
        return {"status": "success", "data": {"resultType": "matrix", "result": [
            {"metric": {}, "values": [[1, "1"], [2, "2.5"], [3, "NaN"], [4, "4"]]}]}}

    h = Harness("prometheus", {"url": "http://prom:9090", "queries": "Load = node_load1", "range_minutes": 60, "token": "T0K"}, {
        ("GET", r"/api/v1/query_range"): matrix, ("GET", r"/api/v1/query\?"): vector,
    })
    data = h.fetch()
    assert data["stats"] == [{"key": "load", "label": "Load", "value": 5.0, "series": [1.0, 2.5, 4.0]}]
    assert seen == ["Bearer T0K", "60"]  # 60 minutes -> 60 points -> 60 s step
    h.close()


def test_prometheus_basic_auth_and_failing_queries():
    h = Harness("prometheus", {"url": "http://prom:9090", "username": "u", "password": "p", "queries": "A = good\nB = bad("}, {
        ("GET", r"/api/v1/query\?"): by_query({
            "good": lambda r: _vector("1") if r.headers["authorization"].startswith("Basic ") else (401, {}),
            "bad(": (400, {"status": "error", "errorType": "bad_data", "error": "parse error near bad("}),
        }),
    })
    data = h.fetch()
    assert data["stats"][0]["value"] == 1.0
    assert data["stats"][1] == {"key": "b", "label": "B", "value": None, "tone": "bad"}  # one bad line does not fail the rest
    h.close()


def test_prometheus_fails_when_every_query_fails():
    h = Harness("prometheus", {"url": "http://prom:9090", "queries": "A = x"}, {("GET", r"/api/v1/query"): (500, {})})
    with pytest.raises(IntegrationError, match="every query failed"):
        h.fetch()
    h.close()


def test_prometheus_query_parsing():
    parsed = prom_mod.parse_queries('Errors = sum(rate(http{code=~"5..|4.."}[5m]))\nMem = a | b | GB\n\nCPU = x | %')
    assert parsed[0]["unit"] is None and parsed[0]["query"].startswith("sum(rate(http{code=~")  # a regex "|" is not a unit
    assert (parsed[1]["query"], parsed[1]["unit"]) == ("a | b", "GB")
    assert [p["key"] for p in parsed] == ["errors", "mem", "cpu"]
    for bad in ("no equals sign", " = q", "x = "):
        with pytest.raises(ValueError, match="line 1"):
            prom_mod.parse_queries(bad)
