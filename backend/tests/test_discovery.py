"""Service discovery: derivation rules, matching, hostile labels, import, and access control.
The Docker Engine API is mocked on the integration's client factory (no real Docker)."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import discovery, models
from app.integrations.registry import SPECS
from app.main import app
from app.routers import icons

from conftest import HEADERS

PW = "correct horse battery"


# ---------------------------------------------------------------- helpers
def ctr(name, image="example/app:latest", labels=None, ports=(), mode="bridge", state="running"):
    return {
        "Id": (name.encode().hex() + "0" * 24)[:64],
        "Names": [f"/{name}"], "Image": image, "State": state, "Labels": labels or {},
        "Ports": [{"IP": ip, "PrivatePort": priv, "PublicPort": pub, "Type": typ} for (ip, priv, pub, typ) in ports],
        "HostConfig": {"NetworkMode": mode},
    }


def tcp(priv, pub, ip="0.0.0.0"):
    return (ip, priv, pub, "tcp")


@pytest.fixture(autouse=True)
def _icons_not_loaded(monkeypatch):
    monkeypatch.setitem(icons._cache, "names", [])  # never a network call, and a known starting state


@pytest.fixture(name="docker")
def docker_fixture(client, monkeypatch):
    """by_host: {hostname: [containers] | http status}; add integrations with add(url)."""
    by_host: dict = {}

    def handler(request):
        data = by_host.get(request.url.host, [])
        if isinstance(data, int):
            return httpx.Response(data)
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"Name": "nas"})
        return httpx.Response(200, json=data)

    monkeypatch.setattr(SPECS["docker"], "client_factory", lambda ctx: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    class D:
        hosts = by_host

        @staticmethod
        def add(url="http://dock:2375", name="Docker"):
            res = client.post("/api/integrations", json={"name": name, "type": "docker", "config": {"url": url}}, headers=HEADERS)
            assert res.status_code == 201, res.text
            return res.json()["id"]

    return D


def run(client, docker, containers, host="dock", **params):
    docker.hosts[host] = containers
    docker.add(f"http://{host}:2375")
    res = client.get("/api/discovery/docker", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def by_name(data, name):
    return next(c for c in data["candidates"] if c["source"]["container_name"] == name)


def add_service(client, **kw):
    body = {"name": "X", "host": "dock", "port": 8096, **kw}
    res = client.post("/api/services", json=body, headers=HEADERS)
    assert res.status_code == 201
    return res.json()


# ---------------------------------------------------------------- rule 1: labels
def test_startpage_labels_win_over_everything(client, docker):
    labels = {
        "startpage.name": "My Jelly", "startpage.url": "https://jelly.example.com/web", "startpage.icon": "jellyfin",
        "startpage.group": "Media", "startpage.description": "Films", "startpage.health": "tcp",
        "traefik.http.routers.j.rule": "Host(`other.example`)",
    }
    data = run(client, docker, [ctr("jf", "jellyfin/jellyfin", labels, [tcp(8096, 8096)])])
    c = by_name(data, "jf")
    assert (c["name"], c["protocol"], c["host"], c["port"], c["path"]) == ("My Jelly", "https", "jelly.example.com", None, "/web")
    assert c["url"] == "https://jelly.example.com/web"
    assert (c["icon"], c["category"], c["description"], c["health"]) == ("jellyfin", "Media", "Films", {"enabled": True, "type": "tcp"})
    assert c["confidence"] == "label" and c["existing_service_id"] is None and c["changes"] is None
    assert c["source"]["integration_name"] == "Docker" and c["source"]["image"] == "jellyfin/jellyfin"


def test_url_label_beats_part_labels_and_parts_work_alone(client, docker):
    both = {"startpage.url": "http://a.lan:81", "startpage.host": "b.lan", "startpage.port": "9"}
    parts = {"startpage.host": "c.lan", "startpage.port": "8123", "startpage.protocol": "https", "startpage.path": "ui", "startpage.category": "Home"}
    data = run(client, docker, [ctr("both", labels=both), ctr("parts", labels=parts)])
    assert by_name(data, "both")["url"] == "http://a.lan:81"
    assert any("wins over" in r for r in by_name(data, "both")["reasons"])
    p = by_name(data, "parts")
    assert p["url"] == "https://c.lan:8123/ui" and p["category"] == "Home" and p["confidence"] == "label"


def test_homepage_labels_are_a_fallback_and_startpage_wins(client, docker):
    hp = {"homepage.name": "Sonarr HP", "homepage.href": "http://nas:8989", "homepage.icon": "sonarr.png",
          "homepage.group": "Arr", "homepage.description": "TV"}
    mixed = dict(hp, **{"startpage.name": "Mine"})
    data = run(client, docker, [ctr("a", labels=hp), ctr("b", labels=mixed)])
    a = by_name(data, "a")
    assert (a["name"], a["url"], a["icon"], a["category"], a["description"]) == ("Sonarr HP", "http://nas:8989", "sonarr", "Arr", "TV")
    assert by_name(data, "b")["name"] == "Mine"


def test_category_label_aliases(client, docker):
    data = run(client, docker, [ctr("a", labels={"startpage.url": "http://x", "startpage.category": "C1"}),
                                ctr("b", labels={"startpage.url": "http://y", "startpage.group": "G1"})])
    assert (by_name(data, "a")["category"], by_name(data, "b")["category"]) == ("C1", "G1")


def test_enable_false_excludes_even_with_other_labels(client, docker):
    data = run(client, docker, [ctr("hidden", labels={"startpage.enable": "false", "startpage.url": "http://x"}, ports=[tcp(80, 80)]),
                                ctr("shown", ports=[tcp(80, 81)])])
    assert [c["source"]["container_name"] for c in data["candidates"]] == ["shown"]
    assert data["skipped"] == [{"container_name": "hidden", "reason": "excluded by startpage.enable=false"}]


# ---------------------------------------------------------------- rule 2: traefik
@pytest.mark.parametrize("rule,expected", [
    ("Host(`a.example`)", ("a.example", "")),
    ("Host(`a.example`) || Host(`b.example`)", ("a.example", "")),
    ("Host(`a.example`, `b.example`)", ("a.example", "")),
    ("Host(`A.Example`) && PathPrefix(`/x`)", ("a.example", "/x")),
    ("PathPrefix(`/x`) && Host(`a.example`)", ("a.example", "/x")),
    ("Host(`a.example`) && PathPrefix(`/a b`)", ("a.example", "/a%20b")),  # a space is percent-encoded, not refused
    ("!Host(`a.example`)", None),
    ("HostRegexp(`{sub:.+}.example`)", None),
    ("Host(`bad host`)", None),
    ("Host(`*.example`)", None),
    ("PathPrefix(`/only`)", None),
    ("garbage ((", None),
    ("", None),
])
def test_traefik_rule_parsing(rule, expected):
    assert discovery.parse_rule(rule) == expected


def test_traefik_candidate_is_https_with_tls_and_ignores_ports(client, docker):
    lab = {"traefik.http.routers.app.rule": "Host(`app.example.com`)", "traefik.http.routers.app.tls": "true"}
    plain = {"traefik.http.routers.app.rule": "Host(`plain.example.com`)", "traefik.http.routers.app.entrypoints": "web"}
    resolver = {"traefik.http.routers.r.rule": "Host(`r.example.com`)", "traefik.http.routers.r.tls.certresolver": "le"}
    secure_ep = {"traefik.http.routers.r.rule": "Host(`e.example.com`)", "traefik.http.routers.r.entrypoints": "websecure"}
    data = run(client, docker, [ctr("t1", labels=lab, ports=[tcp(80, 8080)]), ctr("t2", labels=plain), ctr("t3", labels=resolver), ctr("t4", labels=secure_ep)])
    assert by_name(data, "t1")["url"] == "https://app.example.com" and by_name(data, "t1")["port"] is None
    assert by_name(data, "t1")["confidence"] == "traefik"
    assert by_name(data, "t2")["url"] == "http://plain.example.com"
    assert by_name(data, "t3")["protocol"] == "https" and by_name(data, "t4")["protocol"] == "https"


def test_traefik_router_without_host_is_ignored_and_ports_are_used(client, docker):
    lab = {"traefik.http.routers.tcp.rule": "HostSNI(`*`)", "traefik.http.routers.web.rule": "PathPrefix(`/x`)"}
    c = by_name(run(client, docker, [ctr("t", labels=lab, ports=[tcp(80, 8088)])]), "t")
    assert c["port"] == 8088 and c["confidence"] == "port"
    assert any("no usable Host rule" in r for r in c["reasons"])


def test_traefik_disabled_and_labels_priority(client, docker):
    off = {"traefik.enable": "false", "traefik.http.routers.a.rule": "Host(`a.example`)"}
    with_host = {"traefik.http.routers.a.rule": "Host(`a.example`)", "startpage.host": "mine.lan", "startpage.port": "7000"}
    data = run(client, docker, [ctr("off", labels=off, ports=[tcp(80, 8081)]), ctr("lab", labels=with_host, ports=[tcp(80, 8082)])])
    assert by_name(data, "off")["port"] == 8081
    assert by_name(data, "lab")["url"] == "http://mine.lan:7000"


# ---------------------------------------------------------------- rule 3: ports and images
def test_port_choice(client, docker):
    cs = [
        ctr("multi", ports=[tcp(5432, 5432), tcp(80, 8080), tcp(9999, 9999)]),
        ctr("udp", ports=[("0.0.0.0", 53, 53, "udp"), tcp(8000, 18000)]),
        ctr("odd", ports=[tcp(7777, 7777), tcp(6666, 6666)]),
        ctr("tls", ports=[tcp(443, 9443)]),
        ctr("dup", ports=[tcp(80, 8085), tcp(80, 8085, "::")]),
    ]
    data = run(client, docker, cs)
    assert by_name(data, "multi")["port"] == 8080 and by_name(data, "multi")["confidence"] == "port"
    assert by_name(data, "udp")["port"] == 18000
    odd = by_name(data, "odd")
    assert odd["port"] == 6666 and odd["confidence"] == "guess"
    assert by_name(data, "tls")["protocol"] == "https"
    assert by_name(data, "dup")["port"] == 8085 and by_name(data, "dup")["host"] == "dock"


def test_no_ports_udp_only_and_host_network_are_skipped(client, docker):
    cs = [ctr("none"), ctr("udponly", ports=[("0.0.0.0", 53, 53, "udp")]), ctr("hostnet", mode="host"),
          ctr("unpublished", ports=[("", 80, None, "tcp")])]
    data = run(client, docker, cs)
    assert data["candidates"] == []
    assert {s["container_name"] for s in data["skipped"]} == {"none", "udponly", "hostnet", "unpublished"}
    assert all(s["reason"] for s in data["skipped"])
    assert "host network" in next(s for s in data["skipped"] if s["container_name"] == "hostnet")["reason"]


def test_host_network_uses_the_image_map_port(client, docker):
    c = by_name(run(client, docker, [ctr("jf", "lscr.io/linuxserver/jellyfin:10.9", mode="host")]), "jf")
    assert c["port"] == 8096 and c["confidence"] == "port"


@pytest.mark.parametrize("image,private,expected", [
    ("lscr.io/linuxserver/sonarr:latest", 8989, ("http", 8989)),
    ("plexinc/pms-docker", 32400, ("http", 32400)),
    ("portainer/portainer-ce:2.19", 9443, ("https", 9443)),
    ("ghcr.io/immich-app/immich-server@sha256:abc", 2283, ("http", 2283)),
    ("vaultwarden/server:1.30", 80, ("http", 80)),
])
def test_image_map_prefers_the_known_port_over_others(client, docker, image, private, expected):
    c = by_name(run(client, docker, [ctr("x", image, ports=[tcp(22, 2222), tcp(private, 4000 + private % 1000)])]), "x")
    assert (c["protocol"], c["port"]) == (expected[0], 4000 + expected[1] % 1000)
    assert any("image map" in r for r in c["reasons"])


def test_loopback_only_binding_is_noted(client, docker):
    c = by_name(run(client, docker, [ctr("lo", ports=[tcp(80, 8080, "127.0.0.1")])]), "lo")
    assert any("127.0.0.1" in r for r in c["reasons"])


def test_host_comes_from_a_tcp_docker_url(client, docker):
    docker.hosts["nas.lan"] = [ctr("a", ports=[tcp(80, 8081)])]
    docker.add("http://nas.lan:2375")
    c = client.get("/api/discovery/docker", params={"default_host": "ignored.lan"}).json()["candidates"][0]
    assert c["host"] == "nas.lan"


def test_unix_socket_host_resolution(client, monkeypatch):
    monkeypatch.setattr(SPECS["docker"], "client_factory", lambda ctx: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[ctr("u", ports=[tcp(80, 8082)])]))))
    client.post("/api/integrations", json={"name": "Sock", "type": "docker", "config": {"url": "unix:///var/run/docker.sock"}}, headers=HEADERS)
    assert client.get("/api/discovery/docker", params={"default_host": "box.lan"}).json()["candidates"][0]["host"] == "box.lan"
    assert client.get("/api/discovery/docker", headers={"host": "start.home:8471"}).json()["candidates"][0]["host"] == "start.home"
    assert client.get("/api/discovery/docker", headers={"host": "[::1]:8471"}).json()["candidates"][0]["host"] == "localhost"
    assert client.get("/api/discovery/docker", params={"default_host": "a b/c"}).status_code == 422
    assert client.get("/api/discovery/docker", params={"default_host": "http://x"}).status_code == 422


# ---------------------------------------------------------------- derived name, category, icon, denylist
def test_name_category_derivation(client, docker):
    cs = [
        ctr("proj-kuma-1", "louislam/uptime-kuma", {"com.docker.compose.service": "uptime-kuma", "com.docker.compose.project": "home-lab"}, [tcp(3001, 3001)]),
        ctr("plain_name", ports=[tcp(80, 8090)]),
        ctr("lbl", labels={"startpage.name": "  Spaced   Out  ", "startpage.url": "http://z"}),
    ]
    data = run(client, docker, cs)
    k = by_name(data, "proj-kuma-1")
    assert (k["name"], k["category"]) == ("Uptime Kuma", "Home Lab")
    assert by_name(data, "plain_name")["name"] == "Plain Name" and by_name(data, "plain_name")["category"] == ""
    assert by_name(data, "lbl")["name"] == "Spaced Out"
    assert discovery.pretty("uptime-kuma") == "Uptime Kuma" and discovery.pretty("MyApp_v2") == "MyApp V2"


def test_icon_is_unverified_when_the_list_is_not_cached_and_verified_when_it_is(client, docker, monkeypatch):
    cs = [ctr("a", "lscr.io/linuxserver/sonarr:latest", ports=[tcp(8989, 8989)]), ctr("b", "x/unknownthing", ports=[tcp(80, 81)])]
    data = run(client, docker, cs)
    a = by_name(data, "a")
    assert a["icon"] == "sonarr" and any("not verified" in r for r in a["reasons"])
    monkeypatch.setitem(icons._cache, "names", ["sonarr", "plex"])
    data = client.get("/api/discovery/docker").json()
    assert by_name(data, "a")["icon"] == "sonarr" and any("found in dashboard-icons" in r for r in by_name(data, "a")["reasons"])
    assert by_name(data, "b")["icon"] == ""


@pytest.mark.parametrize("image", ["postgres:16", "library/redis", "containrrr/watchtower", "mariadb:11", "ghcr.io/me/start-page:1", "valkey/valkey"])
def test_denylisted_images_are_skipped_with_a_reason(client, docker, image):
    data = run(client, docker, [ctr("db", image, ports=[tcp(5432, 5432)])])
    assert data["candidates"] == [] and "not a web UI" in data["skipped"][0]["reason"]


def test_a_label_overrides_the_denylist(client, docker):
    c = by_name(run(client, docker, [ctr("db", "postgres:16", {"startpage.url": "http://pgadmin.lan"}, [tcp(5432, 5432)])]), "db")
    assert c["url"] == "http://pgadmin.lan"
    c = by_name(run(client, docker, [ctr("db2", "postgres:16", {"homepage.name": "PG"}, [tcp(5432, 5433)])], host="dock2"), "db2")
    assert c["name"] == "PG"


# ---------------------------------------------------------------- matching the registry
def test_matching_by_host_port_and_by_name(client, docker):
    by_port = add_service(client, name="Movies", host="dock", port=8096)
    by_nm = add_service(client, name="uptime-kuma", host="elsewhere", port=1)
    add_service(client, name="Other", host="dock", port=7000)
    cs = [ctr("jf", "jellyfin/jellyfin", ports=[tcp(8096, 8096)]), ctr("uptime-kuma", "louislam/uptime-kuma", ports=[tcp(3001, 3001)]),
          ctr("fresh", ports=[tcp(80, 8123)])]
    data = run(client, docker, cs)
    assert by_name(data, "jf")["existing_service_id"] == by_port["id"]
    assert by_name(data, "uptime-kuma")["existing_service_id"] == by_nm["id"]
    assert by_name(data, "fresh")["existing_service_id"] is None
    assert [c["source"]["container_name"] for c in data["candidates"]][-2:] != ["fresh"]  # existing ones sort last
    assert data["candidates"][0]["source"]["container_name"] == "fresh"


def test_default_ports_match_through_the_protocol(client, docker):
    svc = add_service(client, name="Site", host="a.example", port=None, protocol="https")
    lab = {"traefik.http.routers.r.rule": "Host(`a.example`)", "traefik.http.routers.r.tls": "true"}
    c = by_name(run(client, docker, [ctr("site", labels=lab)]), "site")
    assert c["existing_service_id"] == svc["id"] and c["changes"] is None


def test_changes_only_for_evidence_and_never_blank_or_guess(client, docker):
    svc = add_service(client, name="Jelly", host="dock", port=8096, icon="my-icon", category="Mine", description="mine")
    guess = ctr("jf", "jellyfin/jellyfin", {"com.docker.compose.project": "media"}, [tcp(8096, 8096)])
    c = by_name(run(client, docker, [guess]), "jf")
    assert c["existing_service_id"] == svc["id"] and c["changes"] is None  # icon/category are guesses: untouched


def test_label_changes_are_proposed_as_current_proposed_pairs(client, docker):
    svc = add_service(client, name="Jelly", host="dock", port=8096, icon="old", category="Old", description="old")
    labels = {"startpage.icon": "jellyfin", "startpage.group": "Media", "startpage.description": "Films", "startpage.name": "Jellyfin",
              "startpage.health": "tcp"}
    c = by_name(run(client, docker, [ctr("jf", "jellyfin/jellyfin", labels, [tcp(8096, 8096)])]), "jf")
    assert c["existing_service_id"] == svc["id"]
    assert c["changes"] == {
        "name": ["Jelly", "Jellyfin"], "icon": ["old", "jellyfin"], "category": ["Old", "Media"],
        "description": ["old", "Films"], "health_type": ["http", "tcp"],
    }


def test_connection_changes_only_with_label_or_traefik_evidence(client, docker):
    svc = add_service(client, name="Wiki", host="old.lan", port=80)
    # matched by name; the port-derived address differs but is not evidence
    c = by_name(run(client, docker, [ctr("wiki", ports=[tcp(80, 9001)])]), "wiki")
    assert c["existing_service_id"] == svc["id"] and c["changes"] is None
    lab = {"startpage.url": "https://wiki.example.com"}
    docker.hosts["dock"] = [ctr("wiki", labels=lab)]
    c = by_name(client.get("/api/discovery/docker").json(), "wiki")
    assert c["changes"] == {"protocol": ["http", "https"], "host": ["old.lan", "wiki.example.com"], "port": [80, None]}


def test_a_label_that_cleans_to_nothing_never_blanks_a_field(client, docker):
    svc = add_service(client, name="Keep", host="dock", port=8096, icon="keep")
    c = by_name(run(client, docker, [ctr("keep", labels={"startpage.icon": "javascript:alert(1)", "startpage.url": "http://dock:8096"})]), "keep")
    assert c["existing_service_id"] == svc["id"] and c["changes"] is None


# ---------------------------------------------------------------- hostile labels
HOSTILE = {
    "startpage.name": "N" * 10_000, "startpage.url": "javascript:alert(1)", "startpage.protocol": "javascript",
    "startpage.host": "evil host/x", "startpage.port": "99999", "startpage.path": "/a b<script>",
    "startpage.icon": "javascript:alert(1)", "startpage.category": "C" * 10_000, "startpage.description": "D" * 10_000,
    "startpage.health": "rm -rf",
}


def test_hostile_labels_are_rejected_without_a_500(client, docker):
    data = run(client, docker, [ctr("evil", labels=HOSTILE, ports=[tcp(80, 8088)])])
    c = by_name(data, "evil")
    assert (c["protocol"], c["host"], c["port"], c["path"]) == ("http", "dock", 8088, "/a%20b%3Cscript%3E")  # encoded, so inert
    assert c["icon"] == "app"  # the slug guessed from the image, never the label
    assert len(c["name"]) <= discovery.MAX_NAME and len(c["category"]) <= discovery.MAX_CATEGORY and len(c["description"]) <= discovery.MAX_DESC
    text = " ".join(c["reasons"])
    for needle in ("startpage.url", "startpage.protocol", "startpage.host", "startpage.port", "startpage.icon", "startpage.health", "truncated"):
        assert needle in text
    assert "javascript" not in c["url"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x.lan", "http://u:p@x.lan", "http://x.lan:99999", "http://" + "a" * 300, "http://" + "a" * 5000, "//x.lan", "data:text/html,x"])
def test_bad_url_labels(client, docker, url):
    data = run(client, docker, [ctr("c", labels={"startpage.url": url}, ports=[tcp(80, 8088)])])
    c = by_name(data, "c")
    assert c["url"].startswith("http://dock:8088") and any("startpage.url label ignored" in r for r in c["reasons"])


def test_a_label_only_container_with_bad_values_is_skipped_not_a_500(client, docker):
    data = run(client, docker, [ctr("c", labels={"startpage.url": "javascript:x"})])
    assert data["candidates"] == [] and "no published TCP port" in data["skipped"][0]["reason"]


def test_malformed_docker_data_is_tolerated(client, docker):
    odd = [{"Id": "abc"}, {"Id": "def", "Names": None, "Labels": None, "Ports": [{"PublicPort": "x", "PrivatePort": None}, "junk"], "HostConfig": None}, "junk", 7]
    data = run(client, docker, odd)
    assert data["candidates"] == [] and len(data["skipped"]) == 2


def test_a_labels_never_leak_into_another_container(client, docker):
    data = run(client, docker, [ctr("a", labels={"startpage.name": "Alpha", "startpage.url": "http://alpha.lan"}), ctr("b", ports=[tcp(80, 8001)])])
    b = by_name(data, "b")
    assert b["name"] == "B" and b["host"] == "dock"


# ---------------------------------------------------------------- integrations
def test_two_integrations_one_failing(client, docker):
    docker.hosts["good"] = [ctr("a", ports=[tcp(80, 8001)])]
    docker.hosts["bad"] = 500
    good, bad = docker.add("http://good:2375", "Good"), docker.add("http://bad:2375", "Bad")
    data = client.get("/api/discovery/docker").json()
    assert [c["source"]["container_name"] for c in data["candidates"]] == ["a"]
    assert data["candidates"][0]["source"]["integration_id"] == good and data["candidates"][0]["host"] == "good"
    assert len(data["errors"]) == 1 and data["errors"][0]["integration_id"] == bad
    assert "500" in data["errors"][0]["error"] and "/containers" not in data["errors"][0]["error"]
    only = client.get("/api/discovery/docker", params={"integration_id": good}).json()
    assert only["errors"] == [] and len(only["candidates"]) == 1


def test_integration_id_errors_and_non_docker_types_are_ignored(client, docker):
    assert client.get("/api/discovery/docker", params={"integration_id": "nope"}).status_code == 404
    other = client.post("/api/integrations", json={"name": "H", "type": "host"}, headers=HEADERS).json()["id"]
    assert client.get("/api/discovery/docker", params={"integration_id": other}).status_code == 400
    assert client.get("/api/discovery/docker").json() == {"candidates": [], "skipped": [], "errors": []}


def test_discovery_writes_nothing(client, docker, session):
    run(client, docker, [ctr("a", ports=[tcp(80, 8001)])])
    assert session.query(models.Service).count() == 0


# ---------------------------------------------------------------- import
def test_import_create_update_and_mixed(client):
    svc = add_service(client, name="Old", host="h", port=1, icon="keepme", category="Keep")
    res = client.post("/api/discovery/import", headers=HEADERS, json={"items": [
        {"key": "k1", "action": "create", "fields": {"name": "New", "host": "dock", "port": 8001, "category": "Media"}},
        {"key": "k2", "action": "update", "service_id": svc["id"], "fields": {"name": "Renamed", "port": 2}},
        {"key": "k3", "action": "create", "fields": {"name": "Bad", "host": "has space"}},
        {"key": "k4", "action": "update", "service_id": "missing", "fields": {"name": "x"}},
        {"key": "k5", "action": "create", "fields": {"name": "NoHost"}},
        {"key": "k6", "action": "create", "fields": {"name": "P", "host": "h", "protocol": "javascript"}},
        {"key": "k7", "action": "create", "fields": {"name": "P", "host": "h", "port": 99999}},
        {"key": "k8", "action": "create", "fields": {"name": "P", "host": "h", "bogus": 1}},
        {"key": "k9", "action": "update", "fields": {"name": "x"}},
    ]})
    assert res.status_code == 200
    body = res.json()
    assert [c["key"] for c in body["created"]] == ["k1"] and body["created"][0]["service"]["url"] == "http://dock:8001"
    assert [u["key"] for u in body["updated"]] == ["k2"]
    assert {e["key"] for e in body["errors"]} == {"k3", "k4", "k5", "k6", "k7", "k8", "k9"}
    assert all(e["error"] for e in body["errors"])
    names = {s["name"] for s in client.get("/api/services").json()}
    assert names == {"New", "Renamed"}
    after = client.get(f"/api/services/{svc['id']}").json()
    assert (after["name"], after["port"], after["host"], after["icon"], after["category"]) == ("Renamed", 2, "h", "keepme", "Keep")


def test_import_update_touches_only_supplied_fields_and_can_clear_the_port(client):
    svc = add_service(client, name="S", host="h", port=80, description="d", health_interval=99)
    res = client.post("/api/discovery/import", headers=HEADERS, json={"items": [{"key": "k", "action": "update", "service_id": svc["id"], "fields": {"port": None, "icon": "sonarr"}}]})
    assert res.json()["errors"] == []
    after = client.get(f"/api/services/{svc['id']}").json()
    assert after["port"] is None and after["icon"] == "sonarr" and after["description"] == "d" and after["health_interval"] == 99


def test_import_rejects_the_same_service_twice_and_oversize_batches(client):
    svc = add_service(client)
    item = {"action": "update", "service_id": svc["id"], "fields": {"name": "A"}}
    body = client.post("/api/discovery/import", headers=HEADERS, json={"items": [{**item, "key": "1"}, {**item, "key": "2"}]}).json()
    assert len(body["updated"]) == 1 and body["errors"][0]["key"] == "2"
    big = {"items": [{"key": str(i), "action": "create", "fields": {"name": "n", "host": "h"}} for i in range(discovery.MAX_ITEMS + 1)]}
    assert client.post("/api/discovery/import", headers=HEADERS, json=big).status_code == 422
    assert client.post("/api/discovery/import", headers=HEADERS, json={"items": [{"key": "x", "action": "delete", "fields": {}}]}).status_code == 422


def test_import_database_failure_rolls_everything_back(client, session, monkeypatch):
    real = session.flush
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(*a, **kw)

    monkeypatch.setattr(session, "flush", flaky)
    items = [{"key": f"k{i}", "action": "create", "fields": {"name": f"S{i}", "host": "h"}} for i in range(2)]
    body = client.post("/api/discovery/import", headers=HEADERS, json={"items": items}).json()
    assert body["created"] == [] and len(body["errors"]) == 2
    monkeypatch.undo()
    assert client.get("/api/services").json() == []


def test_discovery_then_import_round_trip(client, docker):
    data = run(client, docker, [ctr("jf", "jellyfin/jellyfin", {"startpage.name": "Jellyfin", "startpage.group": "Media"}, [tcp(8096, 8096)])])
    c = data["candidates"][0]
    fields = {k: c[k] for k in ("name", "protocol", "host", "port", "path", "icon", "category", "description")}
    fields.update(health_enabled=c["health"]["enabled"], health_type=c["health"]["type"])
    res = client.post("/api/discovery/import", headers=HEADERS, json={"items": [{"key": c["key"], "action": "create", "fields": fields}]}).json()
    assert res["errors"] == [] and res["created"][0]["service"]["url"] == "http://dock:8096"
    again = client.get("/api/discovery/docker").json()["candidates"][0]
    assert again["existing_service_id"] == res["created"][0]["service"]["id"] and again["changes"] is None


# ---------------------------------------------------------------- access control
def anon() -> TestClient:
    return TestClient(app)


def test_routes_need_a_signed_in_writer(client, docker):
    docker.hosts["dock"] = [ctr("a", ports=[tcp(80, 8001)])]
    docker.add()
    assert client.get("/api/discovery/docker").status_code == 200  # auth off
    client.post("/api/auth/password", json={"new": PW}, headers=HEADERS)
    assert client.get("/api/discovery/docker").status_code == 200  # signed in
    guest = anon()
    assert guest.get("/api/discovery/docker").status_code == 401  # auth on, no session
    assert guest.post("/api/discovery/import", json={"items": []}, headers=HEADERS).status_code == 401
    client.patch("/api/auth/settings", json={"guest_access": "read"}, headers=HEADERS)
    assert guest.get("/api/services").status_code == 200  # guests do read
    assert guest.get("/api/discovery/docker").status_code == 401  # but never discovery
    assert guest.get("/api/discovery/docker?integration_id=x").status_code == 401
    assert guest.post("/api/discovery/import", json={"items": []}, headers=HEADERS).status_code == 401
    token = client.post("/api/auth/tokens", json={"name": "t"}, headers=HEADERS).json()["token"]
    bearer = anon()
    assert bearer.get("/api/discovery/docker", headers={"Authorization": f"Bearer {token}"}).status_code == 200
