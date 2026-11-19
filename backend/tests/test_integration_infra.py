"""Fixture tests for the infrastructure integration types: proxmox, truenas, portainer, unraid."""
import json

import pytest

from app.integrations import SPECS, IntegrationError, safe_error
from integration_utils import Harness

GiB = 1024 ** 3
TiB = 1024 ** 4
SECRET = "SUPERSECRETVALUE"


def _fail(h: Harness) -> str:
    """The safe message shown for a fetch that fails."""
    with pytest.raises(Exception) as ei:
        h.fetch(check=False)
    return safe_error(ei.value, h.ctx.cfg)


def _stats(data: dict) -> dict:
    return {s["key"]: s for s in data["stats"]}


def _spec_fields(type_: str) -> dict:
    return {f["key"]: f for f in SPECS[type_].fields}


# ================================================================ proxmox
PVE_CFG = {"url": "https://pve.lan:8006", "token_id": "root@pam!sp", "token_secret": SECRET}
PVE_RES = r"/api2/json/cluster/resources$"


def _pve_rows():
    return [
        {"type": "node", "node": "pve1", "status": "online", "cpu": 0.10, "maxcpu": 8, "mem": 8 * GiB, "maxmem": 16 * GiB},
        {"type": "node", "node": "pve2", "status": "online", "cpu": 0.30, "maxcpu": 4, "mem": 4 * GiB, "maxmem": 16 * GiB},
        {"type": "node", "node": "pve3", "status": "offline"},
        {"type": "qemu", "vmid": 101, "name": "db", "node": "pve1", "status": "stopped", "cpu": 0, "maxmem": 8 * GiB},
        {"type": "qemu", "vmid": 100, "name": "web", "node": "pve1", "status": "running", "cpu": 0.05, "mem": 2 * GiB, "maxmem": 4 * GiB},
        {"type": "qemu", "vmid": 9000, "name": "tmpl", "node": "pve1", "status": "stopped", "template": 1},
        {"type": "lxc", "vmid": 200, "name": "dns", "node": "pve2", "status": "running", "cpu": 0.5, "mem": 512 * 1024 ** 2, "maxmem": GiB},
        {"type": "lxc", "vmid": 201, "name": "paused", "node": "pve2", "status": "paused"},
        {"type": "storage", "id": "storage/pve1/local", "storage": "local", "node": "pve1", "status": "available", "disk": 50 * GiB, "maxdisk": 100 * GiB},
        {"type": "storage", "id": "storage/pve2/local", "storage": "local", "node": "pve2", "status": "available", "disk": 30 * GiB, "maxdisk": 100 * GiB},
        {"type": "storage", "id": "storage/pve1/nfs", "storage": "nfs", "node": "pve1", "status": "available", "shared": 1, "disk": 400 * GiB, "maxdisk": 1000 * GiB},
        {"type": "storage", "id": "storage/pve2/nfs", "storage": "nfs", "node": "pve2", "status": "available", "shared": 1, "disk": 400 * GiB, "maxdisk": 1000 * GiB},
        {"type": "sdn", "id": "sdn/pve1/localnetwork", "node": "pve1", "status": "ok"},
    ]


def _pve(rows, **cfg):
    return Harness("proxmox", {**PVE_CFG, **cfg}, {("GET", PVE_RES): {"data": rows}})


def test_proxmox_spec_fields():
    f = _spec_fields("proxmox")
    assert f["url"]["required"] and f["token_id"]["required"] and f["token_secret"]["required"]
    assert f["token_secret"]["secret"] is True and not f["token_id"].get("secret")
    assert "VM.Audit" in f["token_id"]["help"] and "Sys.Audit" in f["token_id"]["help"]
    assert f["list"]["default"] == "guests" and "node" in f and "verify_tls" in f
    assert SPECS["proxmox"].default_interval == 30 and set(SPECS["proxmox"].provides) == {"stats", "items"}
    assert set(SPECS["proxmox"].actions) == {"guest_power"}


def test_proxmox_populated_stats_and_guest_items():
    seen = []
    h = Harness("proxmox", PVE_CFG, {("GET", PVE_RES): lambda r: seen.append(r.headers["Authorization"]) or {"data": _pve_rows()}})
    data = h.fetch()
    assert seen == [f"PVEAPIToken=root@pam!sp={SECRET}"]
    assert h.calls == [("GET", "https://pve.lan:8006/api2/json/cluster/resources")]
    s = _stats(data)
    assert list(s) == ["nodes", "vms", "lxcs", "cpu", "memory", "storage"]
    assert (s["nodes"]["value"], s["nodes"]["tone"]) == ("2/3", "bad")
    assert s["vms"]["value"] == "1/2" and s["lxcs"]["value"] == "1/2"  # template excluded
    assert (s["cpu"]["value"], s["cpu"]["unit"], s["cpu"]["tone"]) == (16.7, "%", "ok")
    assert s["memory"]["value"] == 37.5
    assert s["storage"]["value"] == 40.0  # shared nfs counted once
    assert [i["title"] for i in data["items"]] == ["web", "db", "dns", "paused"]
    assert data["items"][0] == {"title": "web", "subtitle": "pve1 · VM 100", "status": "online", "progress": 5.0,
                                "meta": "2.0 GiB / 4.0 GiB", "ref": {"node": "pve1", "vmid": "100", "kind": "qemu"}}
    assert data["items"][2]["ref"] == {"node": "pve2", "vmid": "200", "kind": "lxc"}
    assert [i["status"] for i in data["items"]] == ["online", "offline", "online", "maintenance"]
    assert data["items"][1]["progress"] is None
    h.close()


def test_proxmox_list_variants_and_node_filter():
    h = _pve(_pve_rows(), list="nodes")
    items = h.fetch()["items"]
    assert items[0] == {"title": "pve1", "subtitle": "8 cores", "status": "online", "progress": 10.0, "meta": "8.0 GiB / 16.0 GiB"}
    assert [(i["title"], i["status"]) for i in items] == [("pve1", "online"), ("pve2", "online"), ("pve3", "offline")]
    h.close()
    h = _pve(_pve_rows(), list="storage", node="pve1")
    items = h.fetch()["items"]
    assert [i["title"] for i in items] == ["local", "nfs"]
    assert items[0] == {"title": "local", "subtitle": "pve1", "status": "online", "progress": 50.0, "meta": "50.0 GiB / 100.0 GiB"}
    h.close()
    h = _pve(_pve_rows(), node="pve2")
    assert [i["title"] for i in h.fetch()["items"]] == ["dns", "paused"]
    h.close()


def test_proxmox_empty_cluster_is_valid():
    h = _pve([])
    data = h.fetch()
    s = _stats(data)
    assert (s["nodes"]["value"], s["vms"]["value"], s["lxcs"]["value"]) == ("0/0", "0/0", "0/0")
    assert s["cpu"]["value"] is None and s["memory"]["value"] is None and s["storage"]["value"] is None
    assert data["items"] == []
    h.close()
    h = _pve([{"type": "node", "node": "a", "status": "online", "cpu": None, "maxcpu": None, "mem": None, "maxmem": 0}])
    assert _stats(h.fetch())["cpu"]["value"] is None
    h.close()


@pytest.mark.parametrize("status", [401, 403])
def test_proxmox_auth_failure_is_safe(status):
    h = Harness("proxmox", PVE_CFG, {("GET", PVE_RES): (status, {"data": None})})
    msg = _fail(h)
    assert str(status) in msg and SECRET not in msg and "/api2" not in msg
    h.close()


@pytest.mark.parametrize("pct,tone", [(79.9, "ok"), (80, "warn"), (89.9, "warn"), (90, "bad")])
def test_proxmox_tone_thresholds(pct, tone):
    rows = [{"type": "node", "node": "a", "status": "online", "cpu": pct / 100, "maxcpu": 1, "mem": pct, "maxmem": 100},
            {"type": "storage", "storage": "s", "node": "a", "status": "available", "disk": pct, "maxdisk": 100}]
    h = _pve(rows)
    s = _stats(h.fetch())
    assert s["cpu"]["tone"] == s["memory"]["tone"] == s["storage"]["tone"] == tone
    h.close()


def test_proxmox_guest_power_posts_to_the_right_path():
    h = _pve([])
    h.routes[("POST", r"/api2/json/nodes/pve-1/lxc/200/status/reboot$")] = {"data": "UPID:x"}
    h.action("guest_power", {"node": "pve-1", "vmid": "200", "kind": "lxc", "action": "reboot"})
    h.action("guest_power", {"node": "pve-1", "vmid": 200, "kind": "lxc", "action": "reboot"})
    assert h.calls == [("POST", "https://pve.lan:8006/api2/json/nodes/pve-1/lxc/200/status/reboot")] * 2
    h.close()


@pytest.mark.parametrize("payload", [
    {"node": "pve", "vmid": "100/../../access", "kind": "qemu", "action": "start"},
    {"node": "pve", "vmid": "100?x=1", "kind": "qemu", "action": "start"},
    {"node": "pve", "vmid": "", "kind": "qemu", "action": "start"},
    {"node": "pve", "vmid": "1e3", "kind": "qemu", "action": "start"},
    {"node": "pve", "vmid": True, "kind": "qemu", "action": "start"},
    {"node": "pve", "vmid": 1.5, "kind": "qemu", "action": "start"},
    {"node": "pve/../x", "vmid": "100", "kind": "qemu", "action": "start"},
    {"node": "pve x", "vmid": "100", "kind": "qemu", "action": "start"},
    {"node": "-pve", "vmid": "100", "kind": "qemu", "action": "start"},
    {"node": "", "vmid": "100", "kind": "qemu", "action": "start"},
    {"node": "pve", "vmid": "100", "kind": "qemu/../x", "action": "start"},
    {"node": "pve", "vmid": "100", "kind": "vm", "action": "start"},
    {"node": "pve", "vmid": "100", "kind": "qemu", "action": "start/../../x"},
    {"node": "pve", "vmid": "100", "kind": "qemu", "action": "destroy"},
    {"vmid": "100", "kind": "qemu", "action": "start"},
])
def test_proxmox_guest_power_rejects_bad_input_without_a_request(payload):
    h = _pve([])
    with pytest.raises(IntegrationError):
        h.action("guest_power", payload)
    assert h.calls == []
    h.close()


def test_proxmox_guest_power_payload_is_validated_by_the_framework_too():
    from app.integrations.base import check_values
    fields = SPECS["proxmox"].actions["guest_power"].payload
    _, errs = check_values(fields, {"node": "pve/../x", "vmid": "100/../../access", "kind": "qemu", "action": "start"})
    assert {k for k, _ in errs} == {"node", "vmid"}
    _, errs = check_values(fields, {"node": "pve", "vmid": "100", "kind": "qemu", "action": "start"})
    assert errs == []


# ================================================================ truenas
TN_CFG = {"url": "https://nas.lan", "api_key": SECRET}


def _tn_routes(pools=None, alerts=None, info=None):
    return {
        ("GET", r"/api/v2\.0/pool$"): pools if pools is not None else [
            {"name": "tank", "status": "ONLINE", "healthy": True, "size": 10 * TiB, "allocated": 4 * TiB, "free": 6 * TiB},
            {"name": "backup", "status": "DEGRADED", "healthy": False, "size": 2 * TiB, "allocated": int(1.9 * TiB), "free": 0},
        ],
        ("GET", r"/api/v2\.0/alert/list$"): alerts if alerts is not None else [
            {"level": "WARNING", "formatted": "Pool backup is degraded", "klass": "VolumeStatus", "dismissed": False},
            {"level": "CRITICAL", "formatted": "gone", "klass": "X", "dismissed": True},
        ],
        ("GET", r"/api/v2\.0/system/info$"): info if info is not None else {
            "version": "TrueNAS-SCALE-24.10.2", "uptime_seconds": 90000, "loadavg": [0.5, 0.4, 0.3]},
    }


def test_truenas_spec_fields():
    f = _spec_fields("truenas")
    assert f["url"]["required"] and f["api_key"]["required"] and f["api_key"]["secret"] is True
    assert f["list"]["default"] == "pools" and SPECS["truenas"].default_interval == 60
    assert not SPECS["truenas"].actions


def test_truenas_populated():
    sent = []
    routes = _tn_routes()
    inner = routes[("GET", r"/api/v2\.0/pool$")]
    routes[("GET", r"/api/v2\.0/pool$")] = lambda r: sent.append(r.headers["Authorization"]) or inner
    h = Harness("truenas", TN_CFG, routes)
    data = h.fetch()
    assert sent == [f"Bearer {SECRET}"]
    s = data["stats"]
    assert [x["key"] for x in s] == ["pools", "used", "alerts", "uptime", "version", "load"]
    st = _stats(data)
    assert (st["pools"]["value"], st["pools"]["tone"]) == ("1/2", "bad")
    assert (st["used"]["value"], st["used"]["unit"]) == (49.2, "%")
    assert (st["alerts"]["value"], st["alerts"]["tone"]) == (1, "warn")  # dismissed alert ignored
    assert st["uptime"]["value"] == "1d 1h" and st["version"]["value"] == "TrueNAS-SCALE-24.10.2"
    assert st["load"]["value"] == 0.5
    assert data["items"][0] == {"title": "tank", "subtitle": "ONLINE", "status": "online", "progress": 40.0,
                                "meta": "4.0 TiB / 10.0 TiB"}
    assert (data["items"][1]["status"], data["items"][1]["title"]) == ("degraded", "backup")
    h.close()


def test_truenas_alerts_list():
    h = Harness("truenas", {**TN_CFG, "list": "alerts"}, _tn_routes())
    data = h.fetch()
    assert data["items"] == [{"title": "Pool backup is degraded", "subtitle": "WARNING", "status": "degraded", "meta": "VolumeStatus"}]
    h.close()


def test_truenas_empty_system_and_offline_pool():
    routes = {("GET", r"/pool$"): [], ("GET", r"/alert/list$"): [], ("GET", r"/system/info$"): {}}
    h = Harness("truenas", TN_CFG, routes)
    data = h.fetch()
    st = _stats(data)
    assert st["pools"]["value"] == "0/0" and st["pools"]["tone"] == "ok"
    assert st["used"]["value"] is None and (st["alerts"]["value"], st["alerts"]["tone"]) == (0, "ok")
    assert "uptime" not in st and "version" not in st and data["items"] == []
    h.close()
    routes[("GET", r"/pool$")] = [{"name": "p", "status": "OFFLINE", "size": 0, "allocated": None}]
    h = Harness("truenas", TN_CFG, routes)
    item = h.fetch()["items"][0]
    assert item["status"] == "offline" and item["progress"] is None
    h.close()


@pytest.mark.parametrize("level,tone", [("INFO", "info"), ("NOTICE", "info"), ("WARNING", "warn"), ("ERROR", "bad"),
                                        ("CRITICAL", "bad"), ("EMERGENCY", "bad")])
def test_truenas_alert_tone_follows_the_highest_level(level, tone):
    alerts = [{"level": "INFO", "formatted": "a"}, {"level": level, "formatted": "b"}]
    h = Harness("truenas", TN_CFG, _tn_routes(alerts=alerts))
    assert _stats(h.fetch())["alerts"]["tone"] == tone
    h.close()


@pytest.mark.parametrize("pct,tone", [(79.9, "ok"), (80, "warn"), (90, "bad")])
def test_truenas_used_tone_thresholds(pct, tone):
    h = Harness("truenas", TN_CFG, _tn_routes(pools=[{"name": "p", "status": "ONLINE", "healthy": True, "size": 1000, "allocated": pct * 10}]))
    assert _stats(h.fetch())["used"]["tone"] == tone
    h.close()


@pytest.mark.parametrize("status", [401, 403])
def test_truenas_auth_failure_is_safe(status):
    h = Harness("truenas", TN_CFG, {("GET", r"/api/v2\.0/pool$"): (status, {"message": "bad"})})
    msg = _fail(h)
    assert "API key" in msg and SECRET not in msg and "nas.lan" not in msg
    h.close()


def test_truenas_without_rest_api_is_a_clear_safe_error():
    h = Harness("truenas", TN_CFG, {("GET", r"/api/v2\.0/pool$"): (404, "Not Found")})
    msg = _fail(h)
    assert "REST API not available" in msg and SECRET not in msg
    h.close()
    h = Harness("truenas", TN_CFG, {("GET", r"/api/v2\.0/pool$"): "<html>login</html>"})
    assert "REST API not available" in _fail(h)
    h.close()


def test_truenas_optional_endpoints_may_fail():
    routes = _tn_routes()
    routes[("GET", r"/api/v2\.0/alert/list$")] = (500, {})
    routes[("GET", r"/api/v2\.0/system/info$")] = (500, {})
    h = Harness("truenas", TN_CFG, routes)
    assert [s["key"] for s in h.fetch()["stats"]] == ["pools", "used"]
    h.close()


# ================================================================ portainer
PT_CFG = {"url": "http://portainer.lan:9000", "api_key": SECRET}


def _pt_routes(**over):
    routes = {
        ("GET", r"/api/endpoints$"): [
            {"Id": 1, "Name": "local", "URL": "unix:///var/run/docker.sock", "Status": 1, "Snapshots": [
                {"RunningContainerCount": 5, "StoppedContainerCount": 2, "UnhealthyContainerCount": 1, "ImageCount": 10, "VolumeCount": 4}]},
            {"Id": 2, "Name": "edge", "URL": "tcp://edge:9001", "Status": 2, "Snapshots": [{"RunningContainerCount": 3}]},
        ],
        ("GET", r"/api/stacks$"): [{"Id": 7, "Name": "media", "EndpointId": 1, "Status": 1}, {"Id": 8, "Name": "old", "EndpointId": 2, "Status": 2}],
        ("GET", r"/api/endpoints/1/docker/containers/json\?all=1$"): [
            {"Id": "abc123def456aaaa", "Names": ["/old"], "Image": "alpine", "State": "exited", "Status": "Exited (0) 2 days ago"},
            {"Id": "bbb", "Names": ["/web"], "Image": "nginx:1", "State": "running", "Status": "Up 3 hours"},
            {"Id": "ccc", "Names": ["/app"], "Image": "app:2", "State": "running", "Status": "Up 1 hour (unhealthy)"},
            {"Id": "ddd", "Names": ["/pz"], "Image": "x", "State": "paused", "Status": "Up (Paused)"},
        ],
    }
    routes.update(over)
    return routes


def test_portainer_spec_fields():
    f = _spec_fields("portainer")
    assert f["url"]["required"] and f["api_key"]["required"] and f["api_key"]["secret"] is True
    assert not f["endpoint_id"].get("required") and f["list"]["default"] == "containers"
    assert SPECS["portainer"].default_interval == 30
    assert [p["key"] for p in SPECS["portainer"].actions["container"].payload] == ["endpoint_id", "container_id", "action"]


def test_portainer_populated_containers():
    keys = []
    routes = _pt_routes()
    inner = routes[("GET", r"/api/endpoints$")]
    routes[("GET", r"/api/endpoints$")] = lambda r: keys.append(r.headers["X-API-Key"]) or inner
    h = Harness("portainer", PT_CFG, routes)
    data = h.fetch()
    assert keys == [SECRET]
    st = _stats(data)
    assert list(st) == ["endpoints", "running", "stopped", "unhealthy", "stacks", "images", "volumes"]
    assert (st["endpoints"]["value"], st["endpoints"]["tone"]) == ("1/2", "bad")
    assert (st["running"]["value"], st["stopped"]["value"]) == (5, 2)  # the down environment is not counted
    assert (st["unhealthy"]["value"], st["unhealthy"]["tone"]) == (1, "bad")
    assert (st["stacks"]["value"], st["images"]["value"], st["volumes"]["value"]) == (2, 10, 4)
    assert data["items"] == [
        {"title": "web", "subtitle": "nginx:1", "status": "online", "meta": "Up 3 hours", "ref": {"endpoint_id": 1, "container_id": "bbb"}},
        {"title": "app", "subtitle": "app:2", "status": "degraded", "meta": "Up 1 hour (unhealthy)", "ref": {"endpoint_id": 1, "container_id": "ccc"}},
        {"title": "old", "subtitle": "alpine", "status": "offline", "meta": "Exited (0) 2 days ago", "ref": {"endpoint_id": 1, "container_id": "abc123def456aaaa"}},
        {"title": "pz", "subtitle": "x", "status": "maintenance", "meta": "Up (Paused)", "ref": {"endpoint_id": 1, "container_id": "ddd"}},
    ]
    h.close()


def test_portainer_list_variants_and_endpoint_filter():
    h = Harness("portainer", {**PT_CFG, "list": "endpoints"}, _pt_routes())
    assert h.fetch()["items"] == [
        {"title": "local", "subtitle": "unix:///var/run/docker.sock", "status": "online", "meta": "5 running"},
        {"title": "edge", "subtitle": "tcp://edge:9001", "status": "offline", "meta": "3 running"},
    ]
    assert not any("docker/containers" in u for _, u in h.calls)
    h.close()
    h = Harness("portainer", {**PT_CFG, "list": "stacks"}, _pt_routes())
    assert h.fetch()["items"] == [
        {"title": "media", "subtitle": "local", "status": "online"},
        {"title": "old", "subtitle": "edge", "status": "offline"},
    ]
    h.close()
    h = Harness("portainer", {**PT_CFG, "endpoint_id": 1, "list": "stacks"}, _pt_routes())
    data = h.fetch()
    assert [i["title"] for i in data["items"]] == ["media"] and _stats(data)["endpoints"]["value"] == "1/1"
    h.close()


def test_portainer_container_list_uses_the_chosen_endpoint():
    routes = _pt_routes()
    routes[("GET", r"/api/endpoints/2/docker/containers/json\?all=1$")] = [
        {"Id": "e1", "Names": ["/edge-app"], "Image": "i", "State": "running", "Status": "Up"}]
    h = Harness("portainer", {**PT_CFG, "endpoint_id": 2}, routes)
    data = h.fetch()
    assert [i["title"] for i in data["items"]] == ["edge-app"]
    h.close()


def test_portainer_empty_install_is_valid():
    h = Harness("portainer", PT_CFG, {("GET", r"/api/endpoints$"): [], ("GET", r"/api/stacks$"): []})
    data = h.fetch()
    st = _stats(data)
    assert (st["endpoints"]["value"], st["running"]["value"], st["stacks"]["value"]) == ("0/0", 0, 0)
    assert "images" not in st and data["items"] == []
    h.close()
    h = Harness("portainer", PT_CFG, {("GET", r"/api/endpoints$"): [{"Id": 1, "Name": "n", "Status": 1, "Snapshots": []}],
                                       ("GET", r"/api/stacks$"): (403, {}),
                                       ("GET", r"/docker/containers/json"): []})
    data = h.fetch()
    assert "stacks" not in _stats(data) and data["items"] == []
    h.close()


@pytest.mark.parametrize("status", [401, 403])
def test_portainer_auth_failure_is_safe(status):
    h = Harness("portainer", PT_CFG, {("GET", r"/api/endpoints$"): (status, {"message": "x"})})
    msg = _fail(h)
    assert "API key" in msg and SECRET not in msg and "portainer.lan" not in msg
    h.close()


def test_portainer_container_action_posts_and_tolerates_304():
    h = Harness("portainer", PT_CFG, {("POST", r"/containers/web-1/restart$"): {}, ("POST", r"/containers/abc123/start$"): (304, "")})
    h.action("container", {"endpoint_id": 3, "container_id": "web-1", "action": "restart"})
    h.action("container", {"endpoint_id": "3", "container_id": "abc123", "action": "start"})
    assert h.calls == [("POST", "http://portainer.lan:9000/api/endpoints/3/docker/containers/web-1/restart"),
                       ("POST", "http://portainer.lan:9000/api/endpoints/3/docker/containers/abc123/start")]
    h.close()


@pytest.mark.parametrize("payload", [
    {"endpoint_id": 1, "container_id": "abc/../x", "action": "stop"},
    {"endpoint_id": 1, "container_id": "a/b", "action": "stop"},
    {"endpoint_id": 1, "container_id": "..", "action": "stop"},
    {"endpoint_id": 1, "container_id": "../x", "action": "stop"},
    {"endpoint_id": 1, "container_id": "a?force=1", "action": "stop"},
    {"endpoint_id": 1, "container_id": "a b", "action": "stop"},
    {"endpoint_id": 1, "container_id": "", "action": "stop"},
    {"endpoint_id": 1, "container_id": None, "action": "stop"},
    {"endpoint_id": 1, "container_id": "a" * 129, "action": "stop"},
    {"endpoint_id": "1/../2", "container_id": "abc", "action": "stop"},
    {"endpoint_id": "1", "container_id": "abc", "action": "kill"},
    {"endpoint_id": "1", "container_id": "abc", "action": "stop/../x"},
    {"endpoint_id": 0, "container_id": "abc", "action": "stop"},
    {"endpoint_id": -1, "container_id": "abc", "action": "stop"},
    {"endpoint_id": True, "container_id": "abc", "action": "stop"},
    {"endpoint_id": 1.5, "container_id": "abc", "action": "stop"},
    {"container_id": "abc", "action": "stop"},
])
def test_portainer_container_action_rejects_bad_input_without_a_request(payload):
    h = Harness("portainer", PT_CFG, {})
    with pytest.raises(IntegrationError):
        h.action("container", payload)
    assert h.calls == []
    h.close()


# ================================================================ unraid
UN_CFG = {"url": "http://tower.lan", "api_key": SECRET}


def _gql_route(array=None, parity=None, docker=None, vms=None, metrics=None, fail=()):
    """One /graphql route that answers by what the query asks for. `fail` names queries answered with errors."""
    answers = {
        "array": {"data": {"array": array}},
        "parity": {"data": {"array": {"parityCheckStatus": parity}}},
        "docker": {"data": {"docker": docker}},
        "vms": {"data": {"vms": vms}},
        "metrics": {"data": {"metrics": metrics}},
    }

    def handler(request):
        q = json.loads(request.content)["query"]
        name = ("parity" if "parityCheckStatus" in q else "array" if "capacity" in q else "docker" if "docker" in q
                else "vms" if "vms" in q else "metrics")
        if name in fail:
            return {"errors": [{"message": f"Cannot query field on {q}", "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"}}], "data": None}
        return answers[name]

    return {("POST", r"/graphql$"): handler}


def _full_array():
    return {
        "state": "STARTED",
        "capacity": {"kilobytes": {"free": "500", "used": "500", "total": "1000"}},
        "parities": [{"id": "p", "name": "parity", "device": "sdb", "status": "DISK_OK", "temp": 34, "numErrors": 0, "size": 4000000000}],
        "disks": [
            {"id": "d1", "name": "disk1", "device": "sdc", "status": "DISK_OK", "temp": 38, "numErrors": 0, "size": 4000000000,
             "fsSize": "4000000000", "fsUsed": "1000000000"},
            {"id": "d2", "name": "disk2", "device": "sdd", "status": "DISK_OK", "temp": None, "numErrors": 5, "size": 4000000000},
            {"id": "d3", "name": "disk3", "status": "DISK_NP"},
        ],
        "caches": [{"id": "c", "name": "cache", "device": "nvme0n1", "status": "DISK_OK", "temp": 41.4, "numErrors": 0}],
    }


def _full_routes(**over):
    kw = dict(
        array=_full_array(),
        parity={"status": "COMPLETED", "running": False, "progress": 100, "errors": 0},
        docker={"containers": [{"id": "1", "names": ["/plex"], "state": "RUNNING", "status": "Up 2 days", "image": "plex:latest"},
                               {"id": "2", "names": ["/old"], "state": "EXITED", "status": "Exited", "image": "x"}]},
        vms={"domains": [{"id": "v1", "name": "Win11", "state": "RUNNING"}]},
        metrics={"cpu": {"percentTotal": 12.34}, "memory": {"percentTotal": 85.0}},
    )
    kw.update(over)
    return _gql_route(**kw)


def test_unraid_spec_fields():
    f = _spec_fields("unraid")
    assert f["url"]["required"] and f["api_key"]["required"] and f["api_key"]["secret"] is True
    assert f["list"]["default"] == "disks" and SPECS["unraid"].default_interval == 60
    assert "7.2" in SPECS["unraid"].description and "7.2" in f["api_key"]["help"]
    assert not SPECS["unraid"].actions


def test_unraid_populated():
    keys = []
    routes = _full_routes()
    inner = routes[("POST", r"/graphql$")]
    routes[("POST", r"/graphql$")] = lambda r: keys.append(r.headers["x-api-key"]) or inner(r)
    h = Harness("unraid", UN_CFG, routes)
    data = h.fetch()
    assert set(keys) == {SECRET} and len(h.calls) == 5
    assert all(u == "http://tower.lan/graphql" for _, u in h.calls)
    st = _stats(data)
    assert list(st) == ["array", "array_used", "parity", "disk_errors", "containers", "vms", "cpu", "memory"]
    assert (st["array"]["value"], st["array"]["tone"]) == ("Started", "ok")
    assert (st["array_used"]["value"], st["array_used"]["tone"]) == (50.0, "ok")
    assert (st["parity"]["value"], st["parity"]["tone"]) == ("Completed", "ok")
    assert (st["disk_errors"]["value"], st["disk_errors"]["tone"]) == ("1/4", "bad")  # the NP slot is not counted
    assert (st["containers"]["value"], st["vms"]["value"]) == ("1/2", "1/1")
    assert (st["cpu"]["value"], st["cpu"]["tone"]) == (12.3, "ok")
    assert (st["memory"]["value"], st["memory"]["tone"]) == (85.0, "warn")
    assert data["items"][0] == {"title": "parity", "subtitle": "Parity · sdb", "status": "online", "progress": None,
                                "meta": "34 °C · 3.7 TiB"}
    assert [(i["title"], i["status"]) for i in data["items"]] == [
        ("parity", "online"), ("disk1", "online"), ("disk2", "degraded"), ("cache", "online")]
    assert data["items"][1]["progress"] == 25.0
    assert data["items"][2]["meta"] == "3.7 TiB"  # no temperature: spun down
    assert data["items"][3]["meta"] == "41 °C"
    h.close()


def test_unraid_list_variants():
    h = Harness("unraid", {**UN_CFG, "list": "containers"}, _full_routes())
    assert h.fetch()["items"] == [
        {"title": "plex", "subtitle": "plex:latest", "status": "online", "meta": "Up 2 days"},
        {"title": "old", "subtitle": "x", "status": "offline", "meta": "Exited"},
    ]
    h.close()
    h = Harness("unraid", {**UN_CFG, "list": "vms"}, _full_routes(vms={"domains": [
        {"id": "a", "name": "Zed", "state": "SHUTOFF"}, {"id": "b", "name": "Win11", "state": "RUNNING"},
        {"id": "c", "name": "Susp", "state": "PAUSED"}, {"id": "d", "name": "Boom", "state": "CRASHED"}]}))
    assert [(i["title"], i["status"]) for i in h.fetch()["items"]] == [
        ("Win11", "online"), ("Boom", "degraded"), ("Susp", "maintenance"), ("Zed", "offline")]
    h.close()


def test_unraid_empty_system_is_valid():
    h = Harness("unraid", UN_CFG, _full_routes(
        array={"state": "STOPPED", "capacity": None, "disks": [], "parities": [], "caches": []},
        parity=None, docker={"containers": []}, vms={"domains": []}, metrics=None))
    data = h.fetch()
    st = _stats(data)
    assert (st["array"]["value"], st["array"]["tone"]) == ("Stopped", "warn")
    assert st["array_used"]["value"] is None and st["disk_errors"]["value"] == "0/0"
    assert (st["containers"]["value"], st["vms"]["value"]) == ("0/0", "0/0")
    assert "parity" not in st and "cpu" not in st and data["items"] == []
    h.close()
    h = Harness("unraid", UN_CFG, _full_routes(array={}, parity=None, docker=None, vms=None, metrics=None))
    st = _stats(h.fetch())
    assert st["array"]["value"] == "Unknown" and st["array"]["tone"] == "bad"
    h.close()


def test_unraid_missing_optional_fields():
    arr = {"state": "STARTED", "disks": [{"name": "disk1", "status": "DISK_OK"}, {"device": "sdx"}]}
    h = Harness("unraid", UN_CFG, _full_routes(array=arr, parity={"running": True, "progress": 42.4}, metrics={"cpu": {}}))
    data = h.fetch()
    st = _stats(data)
    assert st["array_used"]["value"] is None
    assert (st["parity"]["value"], st["parity"]["tone"]) == ("Running 42%", "info")
    assert "cpu" not in st and "memory" not in st
    assert data["items"][0] == {"title": "disk1", "subtitle": "Data", "status": "online", "progress": None, "meta": None}
    assert data["items"][1]["status"] == "unknown"
    h.close()


def test_unraid_parity_errors_are_bad():
    h = Harness("unraid", UN_CFG, _full_routes(parity={"status": "COMPLETED", "running": False, "errors": 3}))
    assert _stats(h.fetch())["parity"]["tone"] == "bad"
    h.close()


@pytest.mark.parametrize("pct,tone", [(79.9, "ok"), (80, "warn"), (90, "bad")])
def test_unraid_array_used_tone_thresholds(pct, tone):
    arr = {"state": "STARTED", "capacity": {"kilobytes": {"used": pct * 10, "total": 1000}}}
    h = Harness("unraid", UN_CFG, _full_routes(array=arr))
    assert _stats(h.fetch())["array_used"]["tone"] == tone
    h.close()


def test_unraid_array_state_tones():
    for state, tone in [("STARTED", "ok"), ("STOPPED", "warn"), ("DISABLE_DISK", "bad")]:
        h = Harness("unraid", UN_CFG, _full_routes(array={"state": state}))
        assert _stats(h.fetch())["array"]["tone"] == tone
        h.close()


def test_unraid_graphql_errors_in_a_200_are_a_safe_error():
    h = Harness("unraid", UN_CFG, _full_routes(fail=("array",)))
    msg = _fail(h)
    assert "Unraid API returned an error" in msg
    assert "Cannot query" not in msg and "capacity" not in msg and SECRET not in msg
    h.close()


def test_unraid_optional_query_errors_only_drop_their_stats():
    h = Harness("unraid", UN_CFG, _full_routes(fail=("parity", "docker", "vms", "metrics")))
    data = h.fetch()
    assert list(_stats(data)) == ["array", "array_used", "disk_errors"]
    h.close()


def test_unraid_chosen_list_must_be_available():
    h = Harness("unraid", {**UN_CFG, "list": "containers"}, _full_routes(fail=("docker",)))
    msg = _fail(h)
    assert "could not list containers" in msg and "Cannot query" not in msg
    h.close()


@pytest.mark.parametrize("status", [401, 403])
def test_unraid_auth_failure_is_safe(status):
    h = Harness("unraid", UN_CFG, {("POST", r"/graphql$"): (status, {"errors": [{"message": "no"}]})})
    msg = _fail(h)
    assert "API key" in msg and SECRET not in msg and "tower.lan" not in msg
    h.close()


def test_unraid_unauthenticated_graphql_error_is_a_login_failure():
    h = Harness("unraid", UN_CFG, {("POST", r"/graphql$"): {"errors": [{"message": "x", "extensions": {"code": "UNAUTHENTICATED"}}]}})
    msg = _fail(h)
    assert "login failed" in msg and SECRET not in msg
    h.close()


def test_unraid_not_a_graphql_server_is_a_safe_error():
    h = Harness("unraid", UN_CFG, {("POST", r"/graphql$"): "<html>nope</html>"})
    msg = _fail(h)
    assert SECRET not in msg and "<html>" not in msg
    h.close()


def test_proxmox_ref_only_on_guests_and_only_when_safe():
    rows = _pve_rows() + [{"type": "qemu", "vmid": 300, "name": "odd", "node": "pve/../x", "status": "running"},
                          {"type": "lxc", "vmid": "12", "name": "str", "node": "pve1", "status": "running"}]
    for which in ("nodes", "storage"):
        h = _pve(rows, list=which)
        assert not any("ref" in i for i in h.fetch()["items"])
        h.close()
    h = _pve(rows)
    items = {i["title"]: i for i in h.fetch()["items"]}
    assert "ref" not in items["odd"] and items["str"]["ref"] == {"node": "pve1", "vmid": "12", "kind": "lxc"}
    h.close()


def test_proxmox_item_ref_is_accepted_by_the_action_as_is():
    h = _pve(_pve_rows())
    ref = h.fetch()["items"][0]["ref"]
    from app.integrations.base import check_values  # the API validates the payload before the action runs
    assert check_values(SPECS["proxmox"].actions["guest_power"].payload, {**ref, "action": "shutdown"})[1] == []
    h.routes[("POST", r"/api2/json/nodes/pve1/qemu/100/status/shutdown$")] = {"data": "UPID:x"}
    h.action("guest_power", {**ref, "action": "shutdown"})
    assert h.calls[-1] == ("POST", "https://pve.lan:8006/api2/json/nodes/pve1/qemu/100/status/shutdown")
    h.close()


def test_portainer_ref_names_the_environment_actually_listed_without_endpoint_id_configured():
    h = Harness("portainer", PT_CFG, _pt_routes())  # no endpoint_id in the config: the first online one (1) is used
    ref = next(i["ref"] for i in h.fetch()["items"] if i["title"] == "web")
    assert ref == {"endpoint_id": 1, "container_id": "bbb"}
    from app.integrations.base import check_values
    assert check_values(SPECS["portainer"].actions["container"].payload, {**ref, "action": "restart"})[1] == []
    h.routes[("POST", r"/api/endpoints/1/docker/containers/bbb/restart$")] = (204, None)
    h.action("container", {**ref, "action": "restart"})
    assert h.calls[-1] == ("POST", "http://portainer.lan:9000/api/endpoints/1/docker/containers/bbb/restart")
    h.close()


def test_portainer_ref_is_left_out_for_unsafe_container_ids():
    routes = _pt_routes()
    routes[("GET", r"/api/endpoints/1/docker/containers/json\?all=1$")] = [
        {"Id": "../x", "Names": ["/bad"], "State": "running"}, {"Id": "ok1", "Names": ["/good"], "State": "running"}]
    h = Harness("portainer", PT_CFG, routes)
    items = {i["title"]: i for i in h.fetch()["items"]}
    assert "ref" not in items["bad"] and items["good"]["ref"]["container_id"] == "ok1"
    h.close()
