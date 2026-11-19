"""Fixture tests for the adguard, unifi, speedtest_tracker, home_assistant and ics integration types.
No network: every request goes through an httpx.MockTransport (see integration_utils.Harness)."""
import json
import time
from datetime import datetime, timezone

import httpx
import pytest

from app.integrations import SPECS, IntegrationError
from app.integrations.base import validate_config
from app.integrations.types import home_assistant as ha_mod
from app.integrations.types import ics as ics_mod
from integration_utils import Harness


def field(type_: str, key: str) -> dict:
    return next(f for f in SPECS[type_].fields if f["key"] == key)


def required(type_: str) -> set:
    return {f["key"] for f in SPECS[type_].fields if f.get("required")}


def stats_by_key(data: dict) -> dict:
    return {s["key"]: s for s in data["stats"]}


def body_of(request: httpx.Request):
    return json.loads(request.content) if request.content else None


# ---------------------------------------------------------------- specs
def test_spec_declarations():
    ag, un, sp, ha, ic = (SPECS[t] for t in ("adguard", "unifi", "speedtest_tracker", "home_assistant", "ics"))
    assert required("adguard") == {"url"} and ag.secret_keys == ["password"]
    assert ag.default_interval == 15 and ag.provides == ("stats", "items")
    assert list(ag.actions) == ["set_protection"]
    assert [p["key"] for p in ag.actions["set_protection"].payload] == ["enabled", "seconds"]

    assert required("unifi") == {"url"} and set(un.secret_keys) == {"password", "api_key"}
    assert field("unifi", "site")["default"] == "default" and field("unifi", "variant")["default"] == "auto"
    assert un.actions == {} and un.provides == ("stats", "items")

    assert required("speedtest_tracker") == {"url"} and sp.secret_keys == ["token"]
    assert sp.default_interval == 300 and sp.provides == ("stats",)

    assert required("home_assistant") == {"url", "token", "entities"} and ha.secret_keys == ["token"]
    assert field("home_assistant", "entities")["type"] == "textarea"
    assert set(ha.actions) == {"toggle", "turn_on", "turn_off", "press"}

    assert required("ics") == {"url"} and ic.secret_keys == ["password"]
    assert ic.default_interval == 900 and field("ics", "days_ahead")["default"] == 14
    assert field("ics", "max_events")["default"] == 20 and field("ics", "timezone")["default"] == "UTC"
    for s in (ag, un, sp, ha, ic):
        assert any(f["key"] == "verify_tls" for f in s.fields)


# ---------------------------------------------------------------- adguard
AG_STATS = {
    "num_dns_queries": 12000, "num_blocked_filtering": 3000, "avg_processing_time": 0.0123,
    "dns_queries": [100, 200, 300], "blocked_filtering": [10, 20, 30],
    "top_blocked_domains": [{"ads.example.com": 500}, {"track.example.net": 300}],
}
AG_STATUS = {"protection_enabled": True, "running": True, "version": "v0.107.52", "protection_disabled_duration": 0}


def test_adguard_populated():
    seen = []

    def stats(req):
        seen.append(req.headers.get("authorization"))
        return AG_STATS

    h = Harness("adguard", {"url": "http://ag:3000", "username": "admin", "password": "pw"},
                {("GET", r"/control/stats$"): stats, ("GET", r"/control/status$"): AG_STATUS})
    data = h.fetch()
    h.close()
    assert seen[0].startswith("Basic ")
    s = stats_by_key(data)
    assert [x["key"] for x in data["stats"]] == ["queries", "blocked", "percent_blocked", "avg_time", "status"]
    assert (s["queries"]["value"], s["queries"]["series"]) == (12000, [100.0, 200.0, 300.0])
    assert (s["blocked"]["value"], s["blocked"]["series"]) == (3000, [10.0, 20.0, 30.0])
    assert (s["percent_blocked"]["value"], s["percent_blocked"]["unit"]) == (25.0, "%")
    assert (s["avg_time"]["value"], s["avg_time"]["unit"]) == (12.3, "ms")
    assert (s["status"]["value"], s["status"]["tone"]) == ("enabled", "ok")
    assert data["blocking"] == {"enabled": True, "timer": None}
    assert data["items"][0] == {"title": "ads.example.com", "subtitle": "blocked", "status": "offline", "meta": "500"}
    assert len(data["items"]) == 2


def test_adguard_empty_and_paused():
    empty = {"num_dns_queries": 0, "num_blocked_filtering": 0, "dns_queries": [], "blocked_filtering": [],
             "top_blocked_domains": []}
    paused = {"protection_enabled": False, "protection_disabled_duration": 60000}
    h = Harness("adguard", {"url": "http://ag"}, {("GET", r"/control/stats$"): empty, ("GET", r"/control/status$"): paused})
    data = h.fetch()
    h.close()
    s = stats_by_key(data)
    assert (s["queries"]["value"], s["percent_blocked"]["value"], s["avg_time"]["value"]) == (0, 0.0, None)
    assert "series" not in s["queries"]
    assert (s["status"]["value"], s["status"]["tone"]) == ("disabled", "warn")
    assert data["blocking"] == {"enabled": False, "timer": 60} and data["items"] == []

    h = Harness("adguard", {"url": "http://ag"}, {("GET", r"/control/stats$"): {}, ("GET", r"/control/status$"): {}})
    data = h.fetch()
    h.close()
    s = stats_by_key(data)
    assert (s["queries"]["value"], s["percent_blocked"]["value"]) == (None, None)
    assert s["status"]["value"] == "unknown" and data["items"] == []


def test_adguard_auth_failure_is_safe():
    h = Harness("adguard", {"url": "http://ag", "username": "admin", "password": "s3cret-pw"},
                {("GET", r"/control/"): (401, {})})
    with pytest.raises(IntegrationError) as ei:
        h.fetch()
    h.close()
    assert "login failed" in str(ei.value) and "s3cret-pw" not in str(ei.value) and "http" not in str(ei.value)


def test_adguard_set_protection_untimed_and_timed():
    sent = []

    def protection(req):
        sent.append(body_of(req))
        return httpx.Response(200)

    h = Harness("adguard", {"url": "http://ag", "username": "a", "password": "b"}, {("POST", r"/control/protection$"): protection})
    assert h.action("set_protection", {"enabled": False, "seconds": 300}) == {"enabled": False, "seconds": 300, "route": "protection"}
    assert h.action("set_protection", {"enabled": False}) == {"enabled": False, "seconds": None, "route": "protection"}
    h.action("set_protection", {"enabled": True, "seconds": 99})  # a timer makes no sense when enabling
    h.close()
    assert sent == [{"enabled": False, "duration": 300000}, {"enabled": False}, {"enabled": True}]


def test_adguard_set_protection_falls_back_to_dns_config_on_old_versions():
    sent = []

    def dns_config(req):
        sent.append(body_of(req))
        return httpx.Response(200)

    h = Harness("adguard", {"url": "http://ag"}, {("POST", r"/control/protection$"): (404, {}),
                                                   ("POST", r"/control/dns_config$"): dns_config})
    assert h.action("set_protection", {"enabled": False}) == {"enabled": False, "seconds": None, "route": "dns_config"}
    assert sent == [{"protection_enabled": False}]
    n = len(h.calls)
    with pytest.raises(IntegrationError, match="cannot pause"):  # no timer on the fallback: refused, not silently permanent
        h.action("set_protection", {"enabled": False, "seconds": 60})
    assert [c for c in h.calls[n:] if "dns_config" in c[1]] == []
    h.close()


# ---------------------------------------------------------------- unifi
UNIFI_HEALTH = {"meta": {"rc": "ok"}, "data": [
    {"subsystem": "wan", "status": "ok", "rx_bytes-r": 12_500_000, "tx_bytes-r": 1_250_000, "num_gw": 1,
     "num_adopted": 1, "num_disconnected": 0},
    {"subsystem": "www", "status": "ok", "latency": 9, "rx_bytes-r": 12_500_000, "tx_bytes-r": 1_250_000},
    {"subsystem": "wlan", "status": "ok", "num_user": 2, "num_ap": 2, "num_adopted": 2, "num_disconnected": 1},
    {"subsystem": "lan", "status": "ok", "num_user": 1, "num_sw": 1, "num_adopted": 1, "num_disconnected": 0},
]}
UNIFI_STA = {"meta": {"rc": "ok"}, "data": [
    {"mac": "aa:aa", "hostname": "tv"},
    {"mac": "bb:bb", "name": "Laptop", "ip": "192.168.1.20", "is_wired": True, "tx_bytes": 2_000_000_000, "rx_bytes": 500_000_000},
    {"mac": "cc:cc", "hostname": "pixel", "ip": "192.168.1.31", "is_wired": False, "tx_bytes": 100_000_000, "rx_bytes": 300_000_000},
]}
UNIFI_DEVICES = {"meta": {"rc": "ok"}, "data": [
    {"name": "Switch 8", "model": "US8P60", "state": 1, "uptime": 90061},
    {"name": "AP Lounge", "model": "U6-LR", "state": 0, "uptime": 0},
]}


def unifi_os_routes(**extra):
    return {
        ("POST", r"/api/auth/login$"): httpx.Response(200, json={}, headers={"set-cookie": "TOKEN=jwt123; path=/; secure; httponly",
                                                                            "x-csrf-token": "csrf1"}),
        ("GET", r"/proxy/network/api/s/default/stat/health$"): UNIFI_HEALTH,
        ("GET", r"/proxy/network/api/s/default/stat/sta$"): UNIFI_STA,
        ("GET", r"/proxy/network/api/s/default/stat/device$"): UNIFI_DEVICES,
        **extra,
    }


def test_unifi_os_populated_devices_list():
    h = Harness("unifi", {"url": "https://udm", "username": "u", "password": "p"}, unifi_os_routes())
    data = h.fetch()
    assert h.ctx.state["variant"] == "os" and h.ctx.state["cookie"] == "TOKEN=jwt123" and h.ctx.state["csrf"] == "csrf1"
    h.fetch()
    assert [c for c in h.calls if c[1].endswith("/api/auth/login")] == [("POST", "https://udm/api/auth/login")]  # session reused
    h.close()
    vals = {k: v["value"] for k, v in stats_by_key(data).items()}
    assert vals == {"wan_status": "ok", "wan_down": 100.0, "wan_up": 10.0, "clients": 3, "clients_wired": 1,
                    "clients_wireless": 2, "devices_adopted": 4, "devices_offline": 1, "latency": 9}
    assert stats_by_key(data)["wan_down"]["unit"] == "Mbit/s" and stats_by_key(data)["wan_status"]["tone"] == "ok"
    assert stats_by_key(data)["devices_offline"]["tone"] == "bad"
    assert data["items"] == [  # trouble first
        {"title": "AP Lounge", "subtitle": "U6-LR", "status": "offline"},
        {"title": "Switch 8", "subtitle": "US8P60", "status": "online", "meta": "1d 1h"},
    ]


def test_unifi_clients_list_is_ranked_by_traffic_and_capped():
    many = {"meta": {"rc": "ok"}, "data": [{"mac": f"m{i}", "name": f"c{i}", "tx_bytes": i * 1000, "rx_bytes": 0} for i in range(40)]}
    routes = unifi_os_routes()
    routes[("GET", r"/proxy/network/api/s/default/stat/sta$")] = many
    h = Harness("unifi", {"url": "https://udm", "username": "u", "password": "p", "list": "clients"}, routes)
    data = h.fetch()
    assert not [c for c in h.calls if c[1].endswith("/stat/device")]
    h.close()
    assert len(data["items"]) == 25 and data["items"][0]["title"] == "c39" and data["items"][0]["meta"] == "39.0 KB"
    assert stats_by_key(data)["clients"]["value"] == 40

    h = Harness("unifi", {"url": "https://udm", "username": "u", "password": "p", "list": "clients"}, unifi_os_routes())
    data = h.fetch()
    h.close()
    assert data["items"][0] == {"title": "Laptop", "subtitle": "192.168.1.20 / wired", "status": "online", "meta": "2.5 GB"}
    assert data["items"][2] == {"title": "tv", "status": "online", "subtitle": "wifi"}


def test_unifi_legacy_autodetected_and_explicit():
    routes = {
        ("POST", r"/api/auth/login$"): (404, {}),
        ("POST", r"/api/login$"): httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []}, headers={"set-cookie": "unifises=abc; Path=/"}),
        ("GET", r"/api/s/default/stat/health$"): UNIFI_HEALTH,
        ("GET", r"/api/s/default/stat/sta$"): UNIFI_STA,
        ("GET", r"/api/s/default/stat/device$"): UNIFI_DEVICES,
    }
    h = Harness("unifi", {"url": "https://ctl:8443", "username": "u", "password": "p"}, routes)
    data = h.fetch()
    assert h.ctx.state["variant"] == "legacy" and data["variant"] == "legacy"
    assert not [c for c in h.calls if "/proxy/network" in c[1]]
    assert h.calls[-1][1] == "https://ctl:8443/api/s/default/stat/device"
    h.close()
    assert stats_by_key(data)["clients"]["value"] == 3

    h = Harness("unifi", {"url": "https://ctl:8443", "username": "u", "password": "p", "variant": "legacy", "site": "my site"}, {
        ("POST", r"/api/login$"): httpx.Response(200, json={}, headers={"set-cookie": "unifises=abc"}),
        ("GET", r"/api/s/my%20site/stat/health$"): UNIFI_HEALTH,
        ("GET", r"/api/s/my%20site/stat/sta$"): UNIFI_STA,
        ("GET", r"/api/s/my%20site/stat/device$"): UNIFI_DEVICES,
    })
    h.fetch()
    assert h.calls[0] == ("POST", "https://ctl:8443/api/login")  # no UniFi OS probe when the variant is set
    h.close()


def test_unifi_relogs_in_once_on_401():
    logins = []

    def login(req):
        logins.append(1)
        return httpx.Response(200, json={}, headers={"set-cookie": f"TOKEN=t{len(logins)}; path=/"})

    def guarded(body):
        return lambda req: body if req.headers.get("cookie") == "TOKEN=t2" else (401, {"meta": {"rc": "error"}})

    routes = {("POST", r"/api/auth/login$"): login,
              ("GET", r"/stat/health$"): guarded(UNIFI_HEALTH), ("GET", r"/stat/sta$"): guarded(UNIFI_STA),
              ("GET", r"/stat/device$"): guarded(UNIFI_DEVICES)}
    h = Harness("unifi", {"url": "https://udm", "username": "u", "password": "p"}, routes)
    data = h.fetch()
    assert len(logins) == 2 and stats_by_key(data)["clients"]["value"] == 3
    h.close()

    logins.clear()
    routes[("GET", r"/stat/health$")] = (401, {})  # still 401 after the fresh login: give up, no loop
    h = Harness("unifi", {"url": "https://udm", "username": "u", "password": "hunter2-secret"}, routes)
    with pytest.raises(IntegrationError) as ei:
        h.fetch()
    h.close()
    assert len(logins) == 2 and "login failed" in str(ei.value) and "hunter2-secret" not in str(ei.value)


def test_unifi_api_key_mode_sends_the_header_and_never_logs_in():
    seen = []

    def keyed(body):
        def route(req):
            seen.append(req.headers.get("x-api-key"))
            return body
        return route

    h = Harness("unifi", {"url": "https://udm", "api_key": "key-123"}, {
        ("GET", r"/proxy/network/api/s/default/stat/health$"): keyed(UNIFI_HEALTH),
        ("GET", r"/proxy/network/api/s/default/stat/sta$"): keyed(UNIFI_STA),
        ("GET", r"/proxy/network/api/s/default/stat/device$"): keyed(UNIFI_DEVICES),
    })
    data = h.fetch()
    assert seen == ["key-123"] * 3 and not [c for c in h.calls if "login" in c[1]]
    assert stats_by_key(data)["clients"]["value"] == 3
    h.close()


def test_unifi_auth_failure_is_safe():
    h = Harness("unifi", {"url": "https://udm", "username": "u", "password": "hunter2-secret"},
                {("POST", r"/api/auth/login$"): (401, {"code": "AUTHENTICATION_FAILED_INVALID_CREDENTIALS"})})
    with pytest.raises(IntegrationError) as ei:
        h.fetch()
    h.close()
    assert "login failed" in str(ei.value) and "hunter2-secret" not in str(ei.value) and "udm" not in str(ei.value)


def test_unifi_empty_system_and_validation():
    empty = {"meta": {"rc": "ok"}, "data": []}
    h = Harness("unifi", {"url": "https://udm", "api_key": "k"}, {
        ("GET", r"/stat/health$"): empty, ("GET", r"/stat/sta$"): empty, ("GET", r"/stat/device$"): empty})
    data = h.fetch()
    h.close()
    vals = {k: v["value"] for k, v in stats_by_key(data).items()}
    assert vals == {"wan_status": "unknown", "wan_down": None, "wan_up": None, "clients": 0, "clients_wired": 0,
                    "clients_wireless": 0, "devices_adopted": 0, "devices_offline": 0}
    assert data["items"] == []
    spec = SPECS["unifi"]
    assert spec.validate({"url": "https://udm"}) and spec.validate({"url": "x", "username": "u"})
    assert spec.validate({"url": "x", "api_key": "k"}) == [] and spec.validate({"url": "x", "username": "u", "password": "p"}) == []


# ---------------------------------------------------------------- speedtest tracker
ST_LATEST = {"data": {"id": 5, "download": 12_500_000, "upload": 2_500_000, "ping": 11.234, "download_bits": 100_000_000,
                      "upload_bits": 20_000_000, "created_at": "2025-06-04T08:00:00.000000Z", "status": "completed"}}
ST_LIST = {"data": [  # newest first, bytes/s only (no *_bits)
    {"download": 12_500_000, "upload": 2_500_000, "ping": 11.0},
    {"download": 11_250_000, "upload": 2_000_000, "ping": 12.5},
    {"download": 10_000_000, "upload": 1_500_000, "ping": None},
]}


def test_speedtest_v1_populated_with_series_and_bearer_token():
    auth = []

    def latest(req):
        auth.append(req.headers.get("authorization"))
        return ST_LATEST

    h = Harness("speedtest_tracker", {"url": "http://st:8080", "token": "tok"},
                {("GET", r"/api/v1/results/latest$"): latest, ("GET", r"/api/v1/results\?"): ST_LIST})
    data = h.fetch()
    assert h.ctx.state["api"] == "v1" and auth == ["Bearer tok"]
    assert "per_page=20" in h.calls[1][1]
    h.close()
    s = stats_by_key(data)
    assert [x["key"] for x in data["stats"]] == ["download", "upload", "ping", "last_test"]
    assert (s["download"]["value"], s["download"]["unit"]) == (100.0, "Mbit/s")  # download_bits wins over bytes
    assert s["upload"]["value"] == 20.0 and s["ping"]["value"] == 11.23 and s["ping"]["unit"] == "ms"
    assert s["last_test"]["value"] == "2025-06-04T08:00:00.000000Z"
    assert s["download"]["series"] == [80.0, 90.0, 100.0]  # oldest first, bytes/s * 8 / 1e6
    assert s["upload"]["series"] == [12.0, 16.0, 20.0] and s["ping"]["series"] == [12.5, 11.0]


def test_speedtest_v1_bytes_conversion_without_bits_fields():
    latest = {"data": {"download": 12_500_000, "upload": 1_250_000, "ping": 5, "created_at": "t"}}
    h = Harness("speedtest_tracker", {"url": "http://st"}, {("GET", r"/results/latest$"): latest, ("GET", r"/results\?"): (500, {})})
    data = h.fetch()  # a failing list endpoint only costs the sparkline
    h.close()
    s = stats_by_key(data)
    assert (s["download"]["value"], s["upload"]["value"]) == (100.0, 10.0) and "series" not in s["download"]


def test_speedtest_legacy_fallback_when_v1_is_404():
    legacy = {"data": {"download": 93.45, "upload": 11.2, "ping": 14.0, "created_at": "2024-01-01 10:00:00"}}
    h = Harness("speedtest_tracker", {"url": "http://st"}, {("GET", r"/api/v1/results/latest$"): (404, {"message": "Not Found"}),
                                                            ("GET", r"/api/speedtest/latest$"): legacy})
    data = h.fetch()
    assert h.ctx.state["api"] == "legacy" and data["api"] == "legacy"
    s = stats_by_key(data)
    assert (s["download"]["value"], s["upload"]["value"], s["ping"]["value"]) == (93.45, 11.2, 14.0)  # already Mbit/s
    assert "series" not in s["download"] and not [c for c in h.calls if "/api/v1/results?" in c[1]]
    n = len(h.calls)
    h.fetch()
    assert [c[1] for c in h.calls[n:]] == ["http://st/api/speedtest/latest"]  # remembered: no v1 probe any more
    h.close()


def test_speedtest_empty_and_failures():
    h = Harness("speedtest_tracker", {"url": "http://st"}, {("GET", r"/results/latest$"): {"data": None},
                                                            ("GET", r"/results\?"): {"data": []}})
    data = h.fetch()
    h.close()
    assert [s["value"] for s in data["stats"]] == [None, None, None, None]

    h = Harness("speedtest_tracker", {"url": "http://st"}, {("GET", r"/api/"): (404, {})})
    with pytest.raises(IntegrationError, match="not found"):
        h.fetch()
    h.close()

    h = Harness("speedtest_tracker", {"url": "http://st", "token": "tok-secret"}, {("GET", r"/api/"): (401, {"message": "Unauthenticated."})})
    with pytest.raises(IntegrationError) as ei:
        h.fetch()
    h.close()
    assert "login failed" in str(ei.value) and "tok-secret" not in str(ei.value)


# ---------------------------------------------------------------- home assistant
HA_ENTITIES = "light.kitchen\nLiving lamp = switch.living_lamp\nsensor.outside_temp\nlock.front_door\nsensor.gone\n# comment\n"


def ha_state(entity_id, state, **attrs):
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


def ha_routes():
    return {
        ("GET", r"/api/states/light\.kitchen$"): ha_state("light.kitchen", "on", friendly_name="Kitchen Light", brightness=180,
                                                          supported_color_modes=["brightness"], icon="mdi:ceiling-light"),
        ("GET", r"/api/states/switch\.living_lamp$"): ha_state("switch.living_lamp", "off", friendly_name="Lamp"),
        ("GET", r"/api/states/sensor\.outside_temp$"): ha_state("sensor.outside_temp", "18.5", friendly_name="Outside",
                                                                unit_of_measurement="°C", device_class="temperature"),
        ("GET", r"/api/states/lock\.front_door$"): ha_state("lock.front_door", "locked", friendly_name="Front door"),
        ("GET", r"/api/states/sensor\.gone$"): (404, {"message": "Entity not found."}),
    }


def test_ha_entities_parsing():
    assert ha_mod.parse_entities(" Living room = switch.lamp \n\n# c\nlight.x\nA = B = light.y\nlight.x") == [
        {"entity_id": "switch.lamp", "label": "Living room"}, {"entity_id": "light.x", "label": None},
        {"entity_id": "light.y", "label": "A = B"}]
    for bad in ("light.x/../../config", "Light.Kitchen", "=light.x", "nodomain", "light.", "a b = light.x c"):
        with pytest.raises(ValueError, match="line 1"):
            ha_mod.parse_entities(bad)
    assert SPECS["home_assistant"].validate({"entities": ""}) and SPECS["home_assistant"].validate({"entities": "light.x/.."})
    assert SPECS["home_assistant"].validate({"entities": "light.x"}) == []


def test_ha_populated_and_missing_entity_does_not_fail_the_fetch():
    h = Harness("home_assistant", {"url": "http://ha:8123", "token": "tok", "entities": HA_ENTITIES}, ha_routes())
    data = h.fetch()
    assert len(h.calls) == 5 and all("/api/states/" in c[1] for c in h.calls)
    h.close()
    assert [(s["key"], s["value"], s.get("unit")) for s in data["stats"]] == [
        ("sensor.outside_temp", 18.5, "°C"), ("active", 1, None), ("total", 5, None)]
    assert data["items"][0] == {"title": "Kitchen Light", "subtitle": "light.kitchen", "meta": "on", "status": "online"}
    assert data["items"][1] == {"title": "Living lamp", "subtitle": "switch.living_lamp", "meta": "off", "status": "offline"}
    assert data["items"][2]["meta"] == "18.5 °C" and "status" not in data["items"][2]
    assert data["items"][3]["meta"] == "locked" and "status" not in data["items"][3]
    assert data["items"][4] == {"title": "sensor.gone", "subtitle": "sensor.gone", "meta": "not found", "status": "unknown"}
    assert data["entities"][0] == {"entity_id": "light.kitchen", "label": "Kitchen Light", "domain": "light", "state": "on",
                                   "unit": None, "toggleable": True,
                                   "attributes": {"brightness": 180, "icon": "mdi:ceiling-light"}}
    assert data["entities"][2]["unit"] == "°C" and data["entities"][2]["attributes"] == {"device_class": "temperature"}
    assert data["entities"][3]["toggleable"] is False
    assert data["entities"][4] == {"entity_id": "sensor.gone", "label": "sensor.gone", "domain": "sensor", "state": "unknown",
                                   "unit": None, "toggleable": False, "attributes": {}}


def test_ha_more_than_ten_entities_use_one_bulk_call():
    ids = [f"sensor.s{i}" for i in range(1, 12)]
    states = [ha_state(i, str(n), unit_of_measurement="W", friendly_name=f"S{n}") for n, i in enumerate(ids[:10], 1)]
    states.append(ha_state("light.not_listed", "on"))
    h = Harness("home_assistant", {"url": "http://ha", "token": "tok", "entities": "\n".join(ids)}, {("GET", r"/api/states$"): states})
    data = h.fetch()
    h.close()
    assert h.calls == [("GET", "http://ha/api/states")]
    assert len(data["entities"]) == 11 and data["entities"][0]["label"] == "S1" and data["entities"][10]["state"] == "unknown"
    assert data["stats"][1] == {"key": "sensor.s2", "label": "S2", "value": 2, "unit": "W"}
    assert stats_by_key(data)["total"]["value"] == 11 and len(data["items"]) == 11


def test_ha_fresh_system_all_entities_missing_and_failures():
    h = Harness("home_assistant", {"url": "http://ha", "token": "tok", "entities": "light.a\nswitch.b"}, {("GET", r"/api/states/"): (404, {})})
    data = h.fetch()
    h.close()
    assert [i["status"] for i in data["items"]] == ["unknown", "unknown"]
    assert [s["value"] for s in data["stats"]] == [0, 2]

    h = Harness("home_assistant", {"url": "http://ha", "token": "tok", "entities": "light.a"}, {})
    with pytest.raises(AssertionError):  # sanity: a route-less harness really fails on any request
        h.fetch()
    h.close()

    h = Harness("home_assistant", {"url": "http://ha", "token": "tok", "entities": "light.a\nswitch.b"}, {("GET", r"/api/states/"): (500, {})})
    with pytest.raises(httpx.HTTPStatusError):
        h.fetch()
    h.close()

    h = Harness("home_assistant", {"url": "http://ha", "token": "tok", "entities": ""}, {})
    with pytest.raises(IntegrationError, match="no entities"):
        h.fetch()
    h.close()


def test_ha_auth_failure_is_safe():
    h = Harness("home_assistant", {"url": "http://ha", "token": "tok-secret", "entities": "light.a"}, {("GET", r"/api/states/"): (401, {})})
    with pytest.raises(IntegrationError) as ei:
        h.fetch()
    h.close()
    assert "login failed" in str(ei.value) and "tok-secret" not in str(ei.value)


HA_ACTION_ENTITIES = ("light.kitchen\nswitch.living_lamp\nbutton.doorbell\nscene.movie\nscript.goodnight\nlock.front_door\n"
                      "alarm_control_panel.home\nmedia_player.tv\ncover.garage\nclimate.hall")


def ha_action_harness():
    posted = []

    def record(req):
        posted.append((req.url.path, body_of(req), req.headers.get("authorization")))
        return []

    h = Harness("home_assistant", {"url": "http://ha", "token": "tok", "entities": HA_ACTION_ENTITIES},
                {("POST", r"/api/services/"): record})
    return h, posted


def test_ha_every_action_calls_the_right_service():
    h, posted = ha_action_harness()
    assert h.action("toggle", {"entity_id": "light.kitchen"}) == {"entity_id": "light.kitchen", "service": "homeassistant.toggle"}
    h.action("turn_on", {"entity_id": "switch.living_lamp"})
    h.action("turn_off", {"entity_id": "media_player.tv"})
    h.action("toggle", {"entity_id": "cover.garage"})
    h.action("turn_on", {"entity_id": "script.goodnight"})
    h.action("press", {"entity_id": "button.doorbell"})
    h.action("press", {"entity_id": "scene.movie"})
    h.action("press", {"entity_id": "script.goodnight"})
    h.close()
    assert posted == [
        ("/api/services/homeassistant/toggle", {"entity_id": "light.kitchen"}, "Bearer tok"),
        ("/api/services/homeassistant/turn_on", {"entity_id": "switch.living_lamp"}, "Bearer tok"),
        ("/api/services/homeassistant/turn_off", {"entity_id": "media_player.tv"}, "Bearer tok"),
        ("/api/services/homeassistant/toggle", {"entity_id": "cover.garage"}, "Bearer tok"),
        ("/api/services/homeassistant/turn_on", {"entity_id": "script.goodnight"}, "Bearer tok"),
        ("/api/services/button/press", {"entity_id": "button.doorbell"}, "Bearer tok"),
        ("/api/services/scene/turn_on", {"entity_id": "scene.movie"}, "Bearer tok"),
        ("/api/services/script/turn_on", {"entity_id": "script.goodnight"}, "Bearer tok"),
    ]


@pytest.mark.parametrize("entity_id", [
    "light.bedroom",                  # valid but not listed in this integration
    "lock.front_door",                # listed, but the domain is never allowed
    "alarm_control_panel.home",       # listed, never allowed
    "climate.hall",                   # listed, not in the allowlist
    "light.x/../../config",           # malformed (path traversal)
    "light.kitchen/../lock.front_door",
    "light.kitchen\n", " light.kitchen", "LIGHT.kitchen", "light.", ".kitchen", "light", "", None, 5, ["light.kitchen"],
])
@pytest.mark.parametrize("action", ["toggle", "turn_on", "turn_off", "press"])
def test_ha_actions_refuse_unsafe_targets_without_any_request(action, entity_id):
    h, posted = ha_action_harness()
    with pytest.raises(IntegrationError):
        h.action(action, {"entity_id": entity_id})
    assert h.calls == [] and posted == []
    h.close()


@pytest.mark.parametrize("action,entity_id", [("toggle", "scene.movie"), ("turn_on", "button.doorbell"),
                                              ("press", "light.kitchen"), ("press", "media_player.tv")])
def test_ha_actions_refuse_a_service_the_domain_does_not_have(action, entity_id):
    h, _ = ha_action_harness()
    with pytest.raises(IntegrationError):
        h.action(action, {"entity_id": entity_id})
    assert h.calls == []
    h.close()


def test_ha_action_failure_and_token_are_safe():
    h = Harness("home_assistant", {"url": "http://ha", "token": "tok-secret", "entities": "light.a"}, {("POST", r"/api/services/"): (401, {})})
    with pytest.raises(IntegrationError) as ei:
        h.action("toggle", {"entity_id": "light.a"})
    h.close()
    assert "tok-secret" not in str(ei.value)


# ---------------------------------------------------------------- ics helpers
NOW = datetime(2025, 6, 4, 10, 0, tzinfo=timezone.utc)  # Wednesday 4 June 2025, 11:00 in London (BST)
FEED = r"feed\.ics$"


def ev(*lines: str) -> str:
    return "\n".join(lines)


def cal(*events: str) -> str:
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN"]
    for e in events:
        out += ["BEGIN:VEVENT", *e.split("\n"), "END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def ics_fetch(monkeypatch, body, now=NOW, **cfg):
    monkeypatch.setattr(ics_mod, "_now", lambda: now)
    config = {"url": "https://cal.example.com/feed.ics", "timezone": "Europe/London", **cfg}
    h = Harness("ics", config, {("GET", FEED): body})
    try:
        return h.fetch()
    finally:
        h.close()


def starts(data) -> list:
    return [e["start"][:16] for e in data["events"]]


# ---------------------------------------------------------------- ics: parsing and shape
def test_ics_populated_feed(monkeypatch):
    body = cal(
        ev("UID:1", "SUMMARY:Standup", "DTSTART;TZID=Europe/London:20250604T090000", "DTEND;TZID=Europe/London:20250604T093000"),
        ev("UID:2", "SUMMARY:Dentist", r"LOCATION:High St\, Leeds", "DTSTART;TZID=Europe/London:20250604T140000",
           "DTEND;TZID=Europe/London:20250604T150000", r"DESCRIPTION:Bring forms\nand ID"),
        ev("UID:3", "SUMMARY:Bank holiday", "DTSTART;VALUE=DATE:20250605", "DTEND;VALUE=DATE:20250606"),
        ev("UID:4", "SUMMARY:Launch call", "DTSTART:20250606T130000Z", "DTEND:20250606T140000Z"),
        ev("UID:5", "SUMMARY:Gym", "DTSTART:20250607T180000", "DTEND:20250607T190000"),
        ev("UID:6", "SUMMARY:Cancelled thing", "STATUS:CANCELLED", "DTSTART:20250605T100000Z"),
        ev("UID:7", r"SUMMARY:Plan\, Q3 with fin", " ance\\; done", "DTSTART:20250610T100000Z",
           "BEGIN:VALARM", "ACTION:DISPLAY", "SUMMARY:alarm text", "TRIGGER:-PT15M", "END:VALARM"),
    )
    data = ics_fetch(monkeypatch, body)
    assert data["timezone"] == "Europe/London"
    assert [(s["key"], s["value"]) for s in data["stats"]] == [
        ("events_today", 2), ("events_week", 6), ("next_event", "Dentist"), ("next_time", "Today 14:00")]
    assert data["items"][:4] == [
        {"title": "Dentist", "subtitle": "High St, Leeds", "meta": "Today 14:00"},
        {"title": "Bank holiday", "meta": "Tomorrow"},
        {"title": "Launch call", "meta": "Fri 6 Jun 14:00"},
        {"title": "Gym", "meta": "Sat 7 Jun 18:00"},
    ]
    assert data["items"][4] == {"title": "Plan, Q3 with finance; done", "meta": "Tue 10 Jun 11:00"}
    assert len(data["items"]) == 5  # standup already finished, cancelled skipped
    assert data["events"][0] == {"title": "Dentist", "start": "2025-06-04T14:00:00+01:00", "end": "2025-06-04T15:00:00+01:00",
                                 "all_day": False, "location": "High St, Leeds", "description": "Bring forms\nand ID"}
    assert data["events"][1] == {"title": "Bank holiday", "start": "2025-06-05T00:00:00+01:00", "end": "2025-06-06T00:00:00+01:00",
                                 "all_day": True, "location": None, "description": None}
    assert data["events"][2]["start"] == "2025-06-06T14:00:00+01:00"  # UTC converted
    assert data["events"][3]["start"] == "2025-06-07T18:00:00+01:00"  # floating = configured zone
    assert [e["title"] for e in data["events"]] == [i["title"] for i in data["items"]]


def test_ics_empty_calendar_and_not_a_calendar(monkeypatch):
    data = ics_fetch(monkeypatch, "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    assert [(s["key"], s["value"]) for s in data["stats"]] == [
        ("events_today", 0), ("events_week", 0), ("next_event", None), ("next_time", None)]
    assert data["items"] == [] and data["events"] == []
    with pytest.raises(IntegrationError, match="not an iCalendar"):
        ics_fetch(monkeypatch, "<html>login page</html>")


def test_ics_bad_events_are_skipped_not_fatal(monkeypatch):
    body = cal(ev("SUMMARY:No start"), ev("SUMMARY:Bad", "DTSTART:notadate"), ev("SUMMARY:Bad day", "DTSTART:20251341T000000Z"),
               ev("SUMMARY:Fine", "DTSTART:20250605T100000Z"))
    assert [i["title"] for i in ics_fetch(monkeypatch, body)["items"]] == ["Fine"]


def test_ics_other_time_zones(monkeypatch):
    body = cal(ev("SUMMARY:NY call", "DTSTART;TZID=America/New_York:20250605T090000", "DTEND;TZID=America/New_York:20250605T100000"),
               ev("SUMMARY:Odd zone", "DTSTART;TZID=Customized Time Zone:20250605T120000"),
               ev("SUMMARY:Utc", "DTSTART:20250606T130000Z"))
    data = ics_fetch(monkeypatch, body)
    assert [(e["title"], e["start"]) for e in data["events"]] == [
        ("Odd zone", "2025-06-05T12:00:00+01:00"), ("NY call", "2025-06-05T14:00:00+01:00"), ("Utc", "2025-06-06T14:00:00+01:00")]
    data = ics_fetch(monkeypatch, body, timezone="America/New_York")  # unknown TZID reads in the configured zone
    assert [e["start"] for e in data["events"]] == ["2025-06-05T09:00:00-04:00", "2025-06-05T12:00:00-04:00", "2025-06-06T09:00:00-04:00"]
    data = ics_fetch(monkeypatch, body, timezone="UTC")
    assert data["events"][1]["start"] == "2025-06-05T13:00:00+00:00"


def test_ics_timezone_validation_and_tzdata_available(monkeypatch):
    from zoneinfo import ZoneInfo
    ZoneInfo("Europe/London")  # the environment has tz data (tzdata package on Windows)
    with pytest.raises(IntegrationError, match="time zone"):
        ics_fetch(monkeypatch, cal(), timezone="Mars/Olympus")
    assert SPECS["ics"].validate({"timezone": "Mars/Olympus"}) and SPECS["ics"].validate({"timezone": "Europe/Paris"}) == []
    assert SPECS["ics"].validate({}) == []


def test_ics_in_progress_event_and_window_boundaries(monkeypatch):
    body = cal(
        ev("SUMMARY:Running", "DTSTART;TZID=Europe/London:20250604T103000", "DTEND;TZID=Europe/London:20250604T120000"),
        ev("SUMMARY:Multi-day all-day", "DTSTART;VALUE=DATE:20250602", "DTEND;VALUE=DATE:20250606"),
        ev("SUMMARY:Started yesterday", "DTSTART;TZID=Europe/London:20250603T220000", "DTEND;TZID=Europe/London:20250605T060000"),
        ev("SUMMARY:Ended before today", "DTSTART;TZID=Europe/London:20250603T090000", "DTEND;TZID=Europe/London:20250603T100000"),
        ev("SUMMARY:Ends at midnight today", "DTSTART;TZID=Europe/London:20250603T230000", "DTEND;TZID=Europe/London:20250604T000000"),
        ev("SUMMARY:Spans the far edge", "DTSTART;TZID=Europe/London:20250617T233000", "DTEND;TZID=Europe/London:20250619T060000"),
        ev("SUMMARY:Just outside", "DTSTART;TZID=Europe/London:20250618T000000", "DTEND;TZID=Europe/London:20250618T010000"),
        ev("SUMMARY:Future range", "DTSTART;VALUE=DATE:20250606", "DTEND;VALUE=DATE:20250609"),
    )
    data = ics_fetch(monkeypatch, body)
    got = {i["title"]: i for i in data["items"]}
    assert set(got) == {"Running", "Multi-day all-day", "Started yesterday", "Spans the far edge", "Future range"}
    assert got["Running"] == {"title": "Running", "meta": "Now, until 12:00", "status": "online"}
    assert got["Multi-day all-day"] == {"title": "Multi-day all-day", "meta": "Until Tomorrow", "status": "online"}
    assert got["Started yesterday"]["meta"] == "Now, until Tomorrow 06:00"
    assert got["Future range"] == {"title": "Future range", "meta": "Fri 6 Jun - Sun 8 Jun"}
    assert "status" not in got["Spans the far edge"]
    assert data["stats"][2]["value"] == "Multi-day all-day" and data["stats"][3]["value"] == "Until Tomorrow"
    assert data["stats"][0]["value"] == 3  # Running, multi-day, started yesterday overlap today
    assert data["events"][0]["title"] == "Multi-day all-day"


def test_ics_max_events_and_days_ahead(monkeypatch):
    body = cal(ev("SUMMARY:Daily", "DTSTART;TZID=Europe/London:20250605T090000", "RRULE:FREQ=DAILY;COUNT=30"))
    data = ics_fetch(monkeypatch, body, max_events=5)
    assert len(data["items"]) == 5 and len(data["events"]) == 5 and starts(data)[0] == "2025-06-05T09:00"
    data = ics_fetch(monkeypatch, body, days_ahead=3)  # today + 2 more days: 5 and 6 June (7 June is outside)
    assert starts(data) == ["2025-06-05T09:00", "2025-06-06T09:00"]
    assert data["stats"][1]["value"] == 6  # "this week" still looks 7 days ahead (5 to 10 June here) even with days_ahead 3


def test_ics_webcal_is_rewritten_to_https_and_url_validation(monkeypatch):
    monkeypatch.setattr(ics_mod, "_now", lambda: NOW)
    h = Harness("ics", {"url": "webcal://cal.example.com/feed.ics"}, {("GET", FEED): cal()})
    h.fetch()
    h.close()
    assert h.calls == [("GET", "https://cal.example.com/feed.ics")]
    spec = SPECS["ics"]
    assert validate_config(spec, {"url": "webcal://cal.example.com/feed.ics"})[1] == []
    assert validate_config(spec, {"url": "https://x.example/a.ics"})[1] == []
    assert validate_config(spec, {"url": "ftp://x.example/a.ics"})[1]


def test_ics_oversize_body_is_refused(monkeypatch):
    monkeypatch.setattr(ics_mod, "MAX_BYTES", 1000)
    body = cal(*[ev(f"SUMMARY:Event {i}", "DTSTART:20250605T100000Z") for i in range(100)])
    assert len(body) > 1000
    with pytest.raises(IntegrationError, match="too large"):
        ics_fetch(monkeypatch, body)


def test_ics_basic_auth_and_failure_is_safe(monkeypatch):
    monkeypatch.setattr(ics_mod, "_now", lambda: NOW)
    seen = []

    def feed(req):
        seen.append(req.headers.get("authorization"))
        return cal()

    h = Harness("ics", {"url": "https://cal.example.com/feed.ics", "username": "bob", "password": "pw"}, {("GET", FEED): feed})
    h.fetch()
    h.close()
    assert seen[0].startswith("Basic ")
    h = Harness("ics", {"url": "https://cal.example.com/feed.ics", "username": "bob", "password": "s3cret-pw"}, {("GET", FEED): (401, "no")})
    with pytest.raises(IntegrationError) as ei:
        h.fetch()
    h.close()
    assert "login failed" in str(ei.value) and "s3cret-pw" not in str(ei.value) and "example" not in str(ei.value)


# ---------------------------------------------------------------- ics: recurrence
def rec(*lines: str, start="DTSTART;TZID=Europe/London:20250603T090000", end="DTEND;TZID=Europe/London:20250603T100000") -> str:
    return cal(ev("SUMMARY:R", start, end, *lines))


def test_ics_rrule_daily_count(monkeypatch):
    data = ics_fetch(monkeypatch, rec("RRULE:FREQ=DAILY;COUNT=4"))  # 3,4,5,6 June; 3rd is past, 4th (09:00-10:00) already ended
    assert starts(data) == ["2025-06-05T09:00", "2025-06-06T09:00"]
    assert data["stats"][0]["value"] == 1  # the one on 4 June, though it has finished


def test_ics_rrule_daily_interval_until(monkeypatch):
    data = ics_fetch(monkeypatch, rec("RRULE:FREQ=DAILY;INTERVAL=3;UNTIL=20250613T000000Z",
                                      start="DTSTART;TZID=Europe/London:20250601T090000", end="DTEND;TZID=Europe/London:20250601T100000"))
    assert starts(data) == ["2025-06-07T09:00", "2025-06-10T09:00"]  # the 13th is after UNTIL


def test_ics_rrule_until_as_date_is_inclusive(monkeypatch):
    data = ics_fetch(monkeypatch, rec("RRULE:FREQ=DAILY;UNTIL=20250607", start="DTSTART;TZID=Europe/London:20250605T090000",
                                      end="DTEND;TZID=Europe/London:20250605T100000"))
    assert starts(data) == ["2025-06-05T09:00", "2025-06-06T09:00", "2025-06-07T09:00"]


def test_ics_rrule_weekly_byday_count(monkeypatch):
    data = ics_fetch(monkeypatch, rec("RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=5",
                                      start="DTSTART;TZID=Europe/London:20250602T090000", end="DTEND;TZID=Europe/London:20250602T100000"))
    assert starts(data) == ["2025-06-06T09:00", "2025-06-09T09:00", "2025-06-11T09:00"]  # 2,4,6,9,11 June


def test_ics_rrule_weekly_interval_without_count_fast_forwards(monkeypatch):
    data = ics_fetch(monkeypatch, rec("RRULE:FREQ=WEEKLY;INTERVAL=2", start="DTSTART;TZID=Europe/London:20230521T140000",
                                      end="DTEND;TZID=Europe/London:20230521T150000"))
    assert starts(data) == ["2025-06-15T14:00"]  # fortnightly Sundays from 2023-05-21: 2025-06-01 is past, 06-15 is next
    data = ics_fetch(monkeypatch, rec("RRULE:FREQ=WEEKLY;INTERVAL=2", start="DTSTART;TZID=Europe/London:20250521T140000",
                                      end="DTEND;TZID=Europe/London:20250521T150000"))
    assert starts(data) == ["2025-06-04T14:00"]  # 21 May, 4 June, (18 June is outside)


def test_ics_rrule_monthly_forms(monkeypatch):
    mday = rec("RRULE:FREQ=MONTHLY", start="DTSTART;TZID=Europe/London:20250315T090000", end="DTEND;TZID=Europe/London:20250315T100000")
    assert starts(ics_fetch(monkeypatch, mday)) == ["2025-06-15T09:00"]
    second_tue = rec("RRULE:FREQ=MONTHLY;BYDAY=2TU", start="DTSTART;TZID=Europe/London:20250311T090000",
                     end="DTEND;TZID=Europe/London:20250311T100000")
    assert starts(ics_fetch(monkeypatch, second_tue)) == ["2025-06-10T09:00"]
    last_day = rec("RRULE:FREQ=MONTHLY;BYMONTHDAY=-1,15", start="DTSTART;TZID=Europe/London:20250131T090000",
                   end="DTEND;TZID=Europe/London:20250131T100000")
    assert starts(ics_fetch(monkeypatch, last_day, days_ahead=30)) == ["2025-06-15T09:00", "2025-06-30T09:00"]
    last_weekday = rec("RRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1", start="DTSTART;TZID=Europe/London:20250131T090000",
                       end="DTEND;TZID=Europe/London:20250131T100000")
    assert starts(ics_fetch(monkeypatch, last_weekday, days_ahead=30)) == ["2025-06-30T09:00"]
    thirty_first = rec("RRULE:FREQ=MONTHLY", start="DTSTART;TZID=Europe/London:20250131T090000", end="DTEND;TZID=Europe/London:20250131T100000")
    assert starts(ics_fetch(monkeypatch, thirty_first, days_ahead=30)) == []  # June has no 31st: skipped, not clamped


def test_ics_rrule_yearly(monkeypatch):
    y = rec("RRULE:FREQ=YEARLY", start="DTSTART;TZID=Europe/London:20200610T090000", end="DTEND;TZID=Europe/London:20200610T100000")
    assert starts(ics_fetch(monkeypatch, y)) == ["2025-06-10T09:00"]
    over = rec("RRULE:FREQ=YEARLY;COUNT=3", start="DTSTART;TZID=Europe/London:20200610T090000", end="DTEND;TZID=Europe/London:20200610T100000")
    assert starts(ics_fetch(monkeypatch, over)) == []  # 2020, 2021, 2022 only
    months = rec("RRULE:FREQ=YEARLY;BYMONTH=3,6", start="DTSTART;TZID=Europe/London:20240310T090000", end="DTEND;TZID=Europe/London:20240310T100000")
    assert starts(ics_fetch(monkeypatch, months)) == ["2025-06-10T09:00"]


def test_ics_rrule_exdate_forms(monkeypatch):
    start, end = "DTSTART;TZID=Europe/London:20250605T090000", "DTEND;TZID=Europe/London:20250605T100000"
    one = rec("RRULE:FREQ=DAILY;COUNT=4", "EXDATE;TZID=Europe/London:20250606T090000", start=start, end=end)
    assert starts(ics_fetch(monkeypatch, one)) == ["2025-06-05T09:00", "2025-06-07T09:00", "2025-06-08T09:00"]  # COUNT still ends on the 8th
    utc_list = rec("RRULE:FREQ=DAILY;COUNT=4", "EXDATE:20250606T080000Z,20250607T080000Z", start=start, end=end)
    assert starts(ics_fetch(monkeypatch, utc_list)) == ["2025-06-05T09:00", "2025-06-08T09:00"]
    two_lines = rec("RRULE:FREQ=DAILY;COUNT=4", "EXDATE:20250605T080000Z", "EXDATE:20250608T080000Z", start=start, end=end)
    assert starts(ics_fetch(monkeypatch, two_lines)) == ["2025-06-06T09:00", "2025-06-07T09:00"]
    all_day = rec("RRULE:FREQ=DAILY;COUNT=3", "EXDATE;VALUE=DATE:20250606", start="DTSTART;VALUE=DATE:20250605", end="DTEND;VALUE=DATE:20250606")
    data = ics_fetch(monkeypatch, all_day)
    assert starts(data) == ["2025-06-05T00:00", "2025-06-07T00:00"] and all(e["all_day"] for e in data["events"])


def test_ics_recurrence_id_override_replaces_one_occurrence(monkeypatch):
    start, end = "DTSTART;TZID=Europe/London:20250605T090000", "DTEND;TZID=Europe/London:20250605T100000"
    body = cal(ev("UID:abc", "SUMMARY:Series", start, end, "RRULE:FREQ=DAILY;COUNT=3"),
               ev("UID:abc", "SUMMARY:Moved", "RECURRENCE-ID;TZID=Europe/London:20250606T090000",
                  "DTSTART;TZID=Europe/London:20250606T150000", "DTEND;TZID=Europe/London:20250606T160000"),
               ev("UID:abc", "SUMMARY:Cancelled one", "STATUS:CANCELLED", "RECURRENCE-ID;TZID=Europe/London:20250607T090000",
                  "DTSTART;TZID=Europe/London:20250607T090000"))
    data = ics_fetch(monkeypatch, body)
    assert [(e["title"], e["start"][:16]) for e in data["events"]] == [("Series", "2025-06-05T09:00"), ("Moved", "2025-06-06T15:00")]


def test_ics_dst_keeps_the_local_hour(monkeypatch):
    body = rec("RRULE:FREQ=DAILY;COUNT=5", start="DTSTART;TZID=Europe/London:20251025T093000", end="DTEND;TZID=Europe/London:20251025T103000")
    now = datetime(2025, 10, 25, 0, 0, tzinfo=timezone.utc)
    data = ics_fetch(monkeypatch, body, now=now)  # clocks go back on 26 October 2025
    assert [e["start"] for e in data["events"]] == [
        "2025-10-25T09:30:00+01:00", "2025-10-26T09:30:00+00:00", "2025-10-27T09:30:00+00:00",
        "2025-10-28T09:30:00+00:00", "2025-10-29T09:30:00+00:00"]


def test_ics_pathological_rules_never_raise_or_loop(monkeypatch):
    # 125 years of daily steps before the window: a simple rule is fast-forwarded arithmetically, so the
    # series still shows (it used to exhaust the step cap and vanish silently) and nothing is truncated
    body = cal(ev("SUMMARY:Forever", "DTSTART:19000101T090000Z", "RRULE:FREQ=DAILY;COUNT=999999999"))
    t0 = time.time()
    data = ics_fetch(monkeypatch, body)
    assert time.time() - t0 < 5 and data["events"] and data["truncated"] is False

    for rule in ("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30;COUNT=5", "FREQ=DAILY;INTERVAL=0", "FREQ=DAILY;INTERVAL=abc;COUNT=x",
                 "FREQ=SECONDLY", "garbage", "FREQ=MONTHLY;BYDAY=99XX;BYMONTHDAY=0,99", "FREQ=WEEKLY;BYDAY=",
                 "FREQ=MONTHLY;BYSETPOS=500", "FREQ=YEARLY;INTERVAL=1000;UNTIL=nonsense", "FREQ=YEARLY;INTERVAL=1000"):
        data = ics_fetch(monkeypatch, cal(ev("SUMMARY:Odd", "DTSTART;TZID=Europe/London:20250605T090000", f"RRULE:{rule}")))
        assert isinstance(data["events"], list), rule
    # the unsupported frequency is shown once instead of being dropped
    odd = ics_fetch(monkeypatch, cal(ev("SUMMARY:Hourly", "DTSTART;TZID=Europe/London:20250605T090000", "RRULE:FREQ=HOURLY")))
    assert starts(odd) == ["2025-06-05T09:00"]

    # many such events: the shared step budget keeps the whole parse fast
    many = cal(*[ev(f"SUMMARY:E{i}", "DTSTART:19000101T090000Z", "RRULE:FREQ=DAILY;COUNT=999999999") for i in range(300)])
    t0 = time.time()
    ics_fetch(monkeypatch, many)
    assert time.time() - t0 < 10


def test_ics_caps_total_expanded_events(monkeypatch):
    monkeypatch.setattr(ics_mod, "MAX_EXPANDED", 50)
    body = cal(*[ev(f"SUMMARY:E{i}", "DTSTART;TZID=Europe/London:20250605T090000", "RRULE:FREQ=DAILY;COUNT=10") for i in range(30)])
    data = ics_fetch(monkeypatch, body, max_events=200)
    assert len(data["events"]) == 50
    monkeypatch.setattr(ics_mod, "MAX_EXPANDED", 2000)
    monkeypatch.setattr(ics_mod, "MAX_PER_EVENT", 4)
    data = ics_fetch(monkeypatch, cal(ev("SUMMARY:E", "DTSTART;TZID=Europe/London:20250605T090000", "RRULE:FREQ=DAILY")), max_events=200)
    assert len(data["events"]) == 4
