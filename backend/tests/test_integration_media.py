"""Fixture tests for the media integration types: sonarr, radarr, qbittorrent, sabnzbd, jellyfin, plex.
Upstream-style responses go through an httpx.MockTransport; no real network."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
import pytest

from app.integrations import SPECS, IntegrationError, safe_error
from app.integrations.types import jellyfin as jellyfin_mod
from app.integrations.types import plex as plex_mod
from integration_utils import Harness

TODAY = datetime.now(timezone.utc).date()


def _day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def _stats(data):
    return {s["key"]: s for s in data["stats"]}


def _values(data):
    return {s["key"]: s["value"] for s in data["stats"]}


def _paths(h):
    return [urlsplit(url).path for _, url in h.calls]


# ---------------------------------------------------------------- specs
@pytest.mark.parametrize("type_,required,secrets", [
    ("sonarr", {"url", "api_key"}, {"api_key"}),
    ("radarr", {"url", "api_key"}, {"api_key"}),
    ("qbittorrent", {"url"}, {"password"}),
    ("sabnzbd", {"url", "api_key"}, {"api_key"}),
    ("jellyfin", {"url", "api_key"}, {"api_key"}),
    ("plex", {"url", "token"}, {"token"}),
])
def test_spec_required_fields_and_secret_flags(type_, required, secrets):
    spec = SPECS[type_]
    assert {f["key"] for f in spec.fields if f.get("required")} == required
    assert {f["key"] for f in spec.fields if f.get("secret")} == secrets
    assert set(spec.provides) == {"stats", "items"}
    assert any(f["key"] == "verify_tls" for f in spec.fields)


def test_spec_list_option_and_actions():
    for t in ("sonarr", "radarr"):
        f = next(f for f in SPECS[t].fields if f["key"] == "list")
        assert f["default"] == "upcoming" and [o["value"] for o in f["options"]] == ["upcoming", "queue"]
    assert set(SPECS["qbittorrent"].actions) == {"pause_all", "resume_all"}
    assert set(SPECS["sabnzbd"].actions) == {"pause", "resume"}


# ---------------------------------------------------------------- shared failure cases
@pytest.mark.parametrize("type_,cfg,secret", [
    ("sonarr", {"url": "http://media:8989", "api_key": "SECRETKEY"}, "SECRETKEY"),
    ("radarr", {"url": "http://media:8989", "api_key": "SECRETKEY"}, "SECRETKEY"),
    ("sabnzbd", {"url": "http://media:8989", "api_key": "SECRETKEY"}, "SECRETKEY"),
    ("jellyfin", {"url": "http://media:8989", "api_key": "SECRETKEY"}, "SECRETKEY"),
    ("plex", {"url": "http://media:8989", "token": "SECRETKEY"}, "SECRETKEY"),
])
@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_a_safe_error(type_, cfg, secret, status):
    h = Harness(type_, cfg, {("GET", r"."): (status, {"message": "nope SECRETKEY"})})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        h.fetch()
    msg = safe_error(exc.value, h.ctx.cfg)
    assert msg.startswith(f"HTTP {status}") and "media:8989" in msg
    assert secret not in msg and "apikey" not in msg.lower()
    h.close()


# ---------------------------------------------------------------- sonarr
def _sonarr_routes(seen=None, over=None):
    def keyed(body):
        def respond(request):
            if seen is not None:
                seen.append(request.headers.get("x-api-key"))
            return body
        return respond

    routes = {
        ("GET", r"/api/v3/queue"): keyed({"page": 1, "pageSize": 25, "totalRecords": 7, "records": [
            {"title": "Show.S01E02.1080p", "status": "downloading", "size": 1000, "sizeleft": 250, "timeleft": "00:10:00",
             "series": {"title": "Show"}, "episode": {"seasonNumber": 1, "episodeNumber": 2, "title": "Pilot"}},
            {"title": "Other.S02E01", "status": "warning", "size": 0, "sizeleft": 0},
        ]}),
        ("GET", r"/api/v3/wanted/missing"): keyed({"page": 1, "pageSize": 1, "totalRecords": 4, "records": []}),
        ("GET", r"/api/v3/calendar"): keyed([
            {"series": {"title": "Later"}, "seasonNumber": 2, "episodeNumber": 10, "title": "Finale",
             "airDateUtc": f"{_day(3)}T20:00:00Z", "hasFile": True},
            {"series": {"title": "Show"}, "seasonNumber": 1, "episodeNumber": 3, "title": "Third",
             "airDateUtc": f"{_day(1)}T20:00:00Z", "hasFile": False},
        ]),
        ("GET", r"/api/v3/series"): keyed([{"id": 1}, {"id": 2}, {"id": 3}]),
    }
    routes.update(over or {})
    return routes


def test_sonarr_upcoming_is_the_default_list():
    seen = []
    h = Harness("sonarr", {"url": "http://sonarr:8989/", "api_key": "K"}, _sonarr_routes(seen))
    data = h.fetch()
    assert data["stats"] == [
        {"key": "queue", "label": "Queue", "value": 7},
        {"key": "missing", "label": "Missing", "value": 4, "tone": "warn"},
        {"key": "upcoming", "label": "Next 7 days", "value": 2},
        {"key": "series", "label": "Series", "value": 3},
    ]
    assert data["items"] == [  # sorted by air date
        {"title": "Show", "status": "unknown", "subtitle": "S01E03 Third", "meta": _day(1)},
        {"title": "Later", "status": "online", "subtitle": "S02E10 Finale", "meta": _day(3)},
    ]
    assert _paths(h) == ["/api/v3/queue", "/api/v3/wanted/missing", "/api/v3/calendar", "/api/v3/series"]
    assert f"start={_day(0)}" in h.calls[2][1] and f"end={_day(7)}" in h.calls[2][1]
    assert set(seen) == {"K"}
    h.close()


def test_sonarr_queue_list_shows_progress():
    h = Harness("sonarr", {"url": "http://sonarr:8989", "api_key": "K", "list": "queue"}, _sonarr_routes())
    data = h.fetch()
    assert data["items"] == [
        {"title": "Show", "status": "online", "subtitle": "S01E02 Pilot", "progress": 75.0, "meta": "00:10:00"},
        {"title": "Other.S02E01", "status": "degraded"},
    ]
    assert _values(data)["queue"] == 7  # same stats whichever list is shown
    h.close()


def test_sonarr_idle_server_is_valid():
    h = Harness("sonarr", {"url": "http://sonarr:8989", "api_key": "K"}, _sonarr_routes(over={
        ("GET", r"/api/v3/queue"): {"totalRecords": 0, "records": []},
        ("GET", r"/api/v3/wanted/missing"): {"totalRecords": 0, "records": []},
        ("GET", r"/api/v3/calendar"): [],
        ("GET", r"/api/v3/series"): [],
    }))
    data = h.fetch()
    assert [s["value"] for s in data["stats"]] == [0, 0, 0, 0] and data["items"] == []
    h.close()


# ---------------------------------------------------------------- radarr
def _radarr_routes(over=None):
    routes = {
        ("GET", r"/api/v3/queue"): {"totalRecords": 1, "records": [
            {"title": "Dune.2021.2160p", "status": "downloading", "size": 2000, "sizeleft": 1500, "timeleft": "01:00:00",
             "movie": {"title": "Dune", "year": 2021}}]},
        ("GET", r"/api/v3/wanted/missing"): {"totalRecords": 0, "records": []},
        ("GET", r"/api/v3/calendar"): [
            {"title": "Alien", "year": 1979, "inCinemas": f"{_day(4)}T00:00:00Z", "hasFile": True},
            {"title": "Dune", "year": 2021, "inCinemas": "2020-01-01T00:00:00Z", "digitalRelease": f"{_day(2)}T00:00:00Z", "hasFile": False},
            {"title": "Undated"},
        ],
        ("GET", r"/api/v3/movie"): [{"id": i} for i in range(5)],
    }
    routes.update(over or {})
    return routes


def test_radarr_upcoming_prefers_the_release_inside_the_window():
    h = Harness("radarr", {"url": "http://radarr:7878", "api_key": "K"}, _radarr_routes())
    data = h.fetch()
    assert data["stats"] == [
        {"key": "queue", "label": "Queue", "value": 1},
        {"key": "missing", "label": "Missing", "value": 0},
        {"key": "movies", "label": "Movies", "value": 5},
        {"key": "upcoming", "label": "Next 7 days", "value": 3},
    ]
    assert data["items"] == [
        {"title": "Dune (2021)", "status": "unknown", "subtitle": "Digital release", "meta": _day(2)},
        {"title": "Alien (1979)", "status": "online", "subtitle": "Cinema release", "meta": _day(4)},
        {"title": "Undated", "status": "unknown"},
    ]
    assert _paths(h) == ["/api/v3/queue", "/api/v3/wanted/missing", "/api/v3/calendar", "/api/v3/movie"]
    h.close()


def test_radarr_queue_list_and_idle_server():
    h = Harness("radarr", {"url": "http://radarr:7878", "api_key": "K", "list": "queue"}, _radarr_routes())
    assert h.fetch()["items"] == [
        {"title": "Dune (2021)", "status": "online", "progress": 25.0, "meta": "01:00:00"}]
    h.close()
    h = Harness("radarr", {"url": "http://radarr:7878", "api_key": "K"}, _radarr_routes(over={
        ("GET", r"/api/v3/queue"): {"records": []}, ("GET", r"/api/v3/calendar"): [], ("GET", r"/api/v3/movie"): [],
    }))
    data = h.fetch()
    assert [s["value"] for s in data["stats"]] == [0, 0, 0, 0] and data["items"] == []
    h.close()


# ---------------------------------------------------------------- qbittorrent
MB = 1024 * 1024
QB_INFO = {"dl_info_speed": 2 * MB, "up_info_speed": 512 * 1024, "connection_status": "connected"}
QB_TORRENTS = [
    {"name": "Movie", "state": "uploading", "progress": 1, "dlspeed": 0, "upspeed": 512 * 1024, "size": 2 * 1024 ** 3, "eta": 8640000},
    {"name": "Ubuntu ISO", "state": "downloading", "progress": 0.5, "dlspeed": 2 * MB, "upspeed": 0, "size": 4 * 1024 ** 3, "eta": 600},
    {"name": "Old", "state": "stalledUP", "progress": 1, "dlspeed": 0, "upspeed": 0, "size": 100, "eta": 8640000},
    {"name": "Parked", "state": "pausedDL", "progress": 0.1, "dlspeed": 0, "upspeed": 0, "size": 100, "eta": 8640000},
]


def _qb_server(valid, logins, info=None, torrents=None):
    """Routes for a qBittorrent that accepts the cookie `SID=<valid[0]>` and issues sid1, sid2, ..."""
    def login(request):
        logins.append(request.content)
        valid[0] = f"sid{len(logins)}"
        return httpx.Response(200, text="Ok.", headers={"set-cookie": f"SID={valid[0]}; HttpOnly; path=/"}, request=request)

    def guarded(body):
        return lambda request: body if request.headers.get("cookie") == f"SID={valid[0]}" else (403, "Forbidden")

    return {
        ("POST", r"/api/v2/auth/login$"): login,
        ("GET", r"/api/v2/transfer/info$"): guarded(QB_INFO if info is None else info),
        ("GET", r"/api/v2/torrents/info$"): guarded(QB_TORRENTS if torrents is None else torrents),
    }


def test_qbittorrent_populated_stats_and_items():
    logins = []
    h = Harness("qbittorrent", {"url": "http://qb:8080", "username": "admin", "password": "pw"}, _qb_server([""], logins))
    data = h.fetch()
    assert data["stats"] == [
        {"key": "download", "label": "Download", "value": 2.0, "unit": "MB/s", "tone": "info"},
        {"key": "upload", "label": "Upload", "value": 512.0, "unit": "KB/s", "tone": "info"},
        {"key": "active", "label": "Active", "value": 2},
        {"key": "downloading", "label": "Downloading", "value": 1},
        {"key": "seeding", "label": "Seeding", "value": 2},
        {"key": "total", "label": "Torrents", "value": 4},
    ]
    assert data["speeds"] == {"download": 2 * MB, "upload": 512 * 1024}
    assert data["items"] == [
        {"title": "Ubuntu ISO", "status": "online", "subtitle": "4.0 GB", "progress": 50.0, "meta": "down 2.0 MB/s | ETA 10m"},
        {"title": "Movie", "status": "online", "subtitle": "2.0 GB", "progress": 100.0, "meta": "up 512.0 KB/s"},
    ]
    assert [(m, urlsplit(u).path) for m, u in h.calls] == [
        ("POST", "/api/v2/auth/login"), ("GET", "/api/v2/transfer/info"), ("GET", "/api/v2/torrents/info")]
    assert h.ctx.state["sid"] == "sid1" and b"username=admin" in logins[0] and b"password=pw" in logins[0]
    h.fetch()
    assert len(logins) == 1  # the cookie is reused
    h.close()


def test_qbittorrent_logs_in_again_once_when_the_cookie_expired():
    logins = []
    h = Harness("qbittorrent", {"url": "http://qb:8080", "username": "admin", "password": "pw"}, _qb_server([""], logins))
    h.fetch()
    h.ctx.state["sid"] = "expired"
    h.calls.clear()
    data = h.fetch()
    assert len(logins) == 2 and h.ctx.state["sid"] == "sid2"
    assert data["stats"][-1]["value"] == 4
    assert [(m, urlsplit(u).path) for m, u in h.calls] == [
        ("GET", "/api/v2/transfer/info"), ("POST", "/api/v2/auth/login"),
        ("GET", "/api/v2/transfer/info"), ("GET", "/api/v2/torrents/info")]
    h.close()


def test_qbittorrent_idle_server_is_valid():
    h = Harness("qbittorrent", {"url": "http://qb:8080", "username": "admin", "password": "pw"},
                _qb_server([""], [], info={"dl_info_speed": 0, "up_info_speed": 0}, torrents=[]))
    data = h.fetch()
    assert _values(data) == {"download": 0.0, "upload": 0.0, "active": 0, "downloading": 0, "seeding": 0, "total": 0}
    assert data["items"] == [] and data["stats"][0]["unit"] == "B/s"
    h.close()


def test_qbittorrent_without_credentials_never_logs_in():
    h = Harness("qbittorrent", {"url": "http://qb:8080"}, {
        ("GET", r"/api/v2/transfer/info$"): QB_INFO, ("GET", r"/api/v2/torrents/info$"): QB_TORRENTS})
    assert _values(h.fetch())["total"] == 4
    assert [m for m, _ in h.calls] == ["GET", "GET"]
    h.close()


def test_qbittorrent_without_credentials_and_403_asks_for_them():
    h = Harness("qbittorrent", {"url": "http://qb:8080"}, {("GET", r"/api/v2/"): (403, "Forbidden")})
    with pytest.raises(IntegrationError, match="authentication required"):
        h.fetch()
    assert len(h.calls) == 1
    h.close()


def test_qbittorrent_wrong_password_is_a_safe_error():
    h = Harness("qbittorrent", {"url": "http://qb:8080", "username": "admin", "password": "SECRETPW"},
                {("POST", r"/api/v2/auth/login$"): "Fails."})
    with pytest.raises(IntegrationError, match="login failed") as exc:
        h.fetch()
    assert "SECRETPW" not in str(exc.value)
    h.close()
    # Logged in, but every request is still refused (banned IP, bad Referer): one retry, then a clear error.
    logins = []
    h = Harness("qbittorrent", {"url": "http://qb:8080", "username": "admin", "password": "SECRETPW"}, {
        ("POST", r"/api/v2/auth/login$"): lambda r: (logins.append(1), httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=x; path=/"}, request=r))[1],
        ("GET", r"/api/v2/"): (403, "Forbidden"),
    })
    with pytest.raises(IntegrationError, match="login failed"):
        h.fetch()
    assert len(logins) == 2
    h.close()


def test_qbittorrent_login_403_is_a_safe_error():
    h = Harness("qbittorrent", {"url": "http://qb:8080", "username": "a", "password": "SECRETPW"},
                {("POST", r"/api/v2/auth/login$"): (403, "banned")})
    with pytest.raises(IntegrationError, match="login failed") as exc:
        h.fetch()
    assert "SECRETPW" not in str(exc.value)
    h.close()


def test_qbittorrent_pause_resume_use_the_v5_names():
    sent = []
    h = Harness("qbittorrent", {"url": "http://qb:8080"}, {
        ("POST", r"/api/v2/torrents/(stop|start)$"): lambda r: (sent.append((r.url.path, r.content)), httpx.Response(200, request=r))[1]})
    h.action("pause_all", {})
    h.action("resume_all", {})
    assert sent == [("/api/v2/torrents/stop", b"hashes=all"), ("/api/v2/torrents/start", b"hashes=all")]
    h.close()


def test_qbittorrent_pause_resume_fall_back_to_the_v4_names_on_404():
    h = Harness("qbittorrent", {"url": "http://qb:8080"}, {
        ("POST", r"/api/v2/torrents/(stop|start)$"): (404, "Not Found"),
        ("POST", r"/api/v2/torrents/(pause|resume)$"): httpx.Response(200),
    })
    h.action("pause_all", {})
    h.action("resume_all", {})
    assert [(m, urlsplit(u).path) for m, u in h.calls] == [
        ("POST", "/api/v2/torrents/stop"), ("POST", "/api/v2/torrents/pause"),
        ("POST", "/api/v2/torrents/start"), ("POST", "/api/v2/torrents/resume")]
    h.close()


def test_qbittorrent_action_with_an_expired_cookie_logs_in_again():
    logins = []
    valid = [""]
    routes = _qb_server(valid, logins)
    routes[("POST", r"/api/v2/torrents/stop$")] = lambda r: httpx.Response(200, request=r) if r.headers.get("cookie") == f"SID={valid[0]}" else (403, "Forbidden")
    h = Harness("qbittorrent", {"url": "http://qb:8080", "username": "a", "password": "p"}, routes)
    h.ctx.state["sid"] = "stale"
    valid[0] = "other"
    h.action("pause_all", {})
    assert len(logins) == 1 and h.ctx.state["sid"] == "sid1"
    h.close()


# ---------------------------------------------------------------- sabnzbd
SAB_QUEUE = {"queue": {
    "status": "Downloading", "paused": False, "kbpersec": "2048.0", "size": "3.0 GB", "sizeleft": "1.5 GB",
    "timeleft": "0:12:30", "noofslots_total": 2, "noofslots": 2,
    "slots": [
        {"filename": "Show.S01E01", "percentage": "50", "status": "Downloading", "timeleft": "0:12:30", "cat": "tv",
         "size": "2.0 GB", "mb": "2048", "mbleft": "1024"},
        {"filename": "Other", "percentage": "0", "status": "Queued", "timeleft": "0:00:00", "cat": "*", "size": "1.0 GB"},
    ],
}}


def test_sabnzbd_populated_queue():
    h = Harness("sabnzbd", {"url": "http://sab:8080/sabnzbd/", "api_key": "SECRETKEY"}, {("GET", r"/sabnzbd/api\?"): SAB_QUEUE})
    data = h.fetch()
    assert data["stats"] == [
        {"key": "speed", "label": "Speed", "value": 2.0, "unit": "MB/s", "tone": "info"},
        {"key": "left", "label": "Remaining", "value": "1.5 GB"},
        {"key": "timeleft", "label": "Time left", "value": "0:12:30"},
        {"key": "queue", "label": "Queue", "value": 2},
        {"key": "paused", "label": "Paused", "value": False},
    ]
    assert data["items"] == [
        {"title": "Show.S01E01", "status": "online", "subtitle": "tv | 2.0 GB", "progress": 50.0, "meta": "0:12:30"},
        {"title": "Other", "status": "unknown", "subtitle": "1.0 GB", "progress": 0.0, "meta": "0:00:00"},
    ]
    assert data["speed_bps"] == 2048 * 1024
    url = h.calls[0][1]
    assert url.startswith("http://sab:8080/sabnzbd/api?") and "mode=queue" in url and "output=json" in url and "apikey=SECRETKEY" in url
    h.close()


def test_sabnzbd_idle_and_paused():
    idle = {"queue": {"status": "Idle", "paused": False, "kbpersec": "0.00", "sizeleft": "0 B", "timeleft": "0:00:00",
                      "noofslots": 0, "slots": []}}
    h = Harness("sabnzbd", {"url": "http://sab:8080", "api_key": "K"}, {("GET", r"/api\?"): idle})
    data = h.fetch()
    assert _values(data) == {"speed": 0.0, "left": "0 B", "timeleft": "0:00:00", "queue": 0, "paused": False}
    assert data["items"] == []
    h.close()
    paused = {"queue": {**idle["queue"], "paused": True}}
    h = Harness("sabnzbd", {"url": "http://sab:8080", "api_key": "K"}, {("GET", r"/api\?"): paused})
    assert _stats(h.fetch())["paused"] == {"key": "paused", "label": "Paused", "value": True, "tone": "warn"}
    h.close()


def test_sabnzbd_key_never_leaks_into_errors():
    h = Harness("sabnzbd", {"url": "http://sab:8080", "api_key": "SECRETKEY"}, {("GET", r"/api\?"): (500, {"error": "SECRETKEY"})})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        h.fetch()
    assert "SECRETKEY" in str(exc.value)  # raw httpx text carries the URL: that is why safe_error exists
    msg = safe_error(exc.value, h.ctx.cfg)
    assert msg == "HTTP 500 Internal Server Error (sab:8080)" and "SECRETKEY" not in msg
    h.close()

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    h = Harness("sabnzbd", {"url": "http://sab:8080", "api_key": "SECRETKEY"}, {("GET", r"/api\?"): boom})
    with pytest.raises(httpx.ConnectError) as exc:
        h.fetch()
    msg = safe_error(exc.value, h.ctx.cfg)
    assert "SECRETKEY" not in msg and "apikey" not in msg and msg.startswith("connection failed")
    h.close()


def test_sabnzbd_wrong_key_answered_with_200_is_a_fixed_message():
    h = Harness("sabnzbd", {"url": "http://sab:8080", "api_key": "SECRETKEY"},
                {("GET", r"/api\?"): {"status": False, "error": "API Key Incorrect SECRETKEY"}})
    with pytest.raises(IntegrationError, match="check the API key") as exc:
        h.fetch()
    assert "SECRETKEY" not in str(exc.value) and "Incorrect" not in str(exc.value)
    h.close()


def test_sabnzbd_pause_and_resume():
    h = Harness("sabnzbd", {"url": "http://sab:8080", "api_key": "K"}, {("GET", r"mode=(pause|resume)"): {"status": True}})
    h.action("pause", {})
    h.action("resume", {})
    assert "mode=pause" in h.calls[0][1] and "mode=resume" in h.calls[1][1]
    h.close()
    h = Harness("sabnzbd", {"url": "http://sab:8080", "api_key": "K"}, {("GET", r"mode=pause"): {"status": False}})
    with pytest.raises(IntegrationError, match="rejected"):
        h.action("pause", {})
    h.close()


# ---------------------------------------------------------------- jellyfin
HOUR = 36_000_000_000  # ticks (100 ns) in an hour


def _jf_routes(sessions, counts, seen=None):
    def sess(request):
        if seen is not None:
            seen.append(request.headers.get("x-emby-token"))
        return sessions

    return {("GET", r"/Sessions\?"): sess, ("GET", r"/Items/Counts$"): counts}


JF_SESSIONS = [
    {"UserName": "alice", "DeviceName": "Living room TV", "Client": "Jellyfin Android TV",
     "NowPlayingItem": {"Name": "Pilot", "SeriesName": "Show", "Type": "Episode", "ParentIndexNumber": 1, "IndexNumber": 2, "RunTimeTicks": HOUR},
     "PlayState": {"PositionTicks": HOUR // 4, "IsPaused": False, "PlayMethod": "Transcode"}},
    {"UserName": "bob", "DeviceName": "Phone"},  # connected, not playing
    {"UserName": "carol", "Client": "Jellyfin Web",
     "NowPlayingItem": {"Name": "Heat", "Type": "Movie"},
     "PlayState": {"PositionTicks": 123, "IsPaused": True, "PlayMethod": "DirectPlay"}},
]


def test_jellyfin_populated_sessions_and_counts():
    seen = []
    h = Harness("jellyfin", {"url": "http://jf:8096", "api_key": "K"},
                _jf_routes(JF_SESSIONS, {"MovieCount": 120, "SeriesCount": 30, "EpisodeCount": 900, "SongCount": 5}, seen))
    data = h.fetch()
    assert data["stats"] == [
        {"key": "streams", "label": "Streams", "value": 2, "tone": "info"},
        {"key": "transcodes", "label": "Transcoding", "value": 1, "tone": "warn"},
        {"key": "movies", "label": "Movies", "value": 120},
        {"key": "series", "label": "Series", "value": 30},
        {"key": "episodes", "label": "Episodes", "value": 900},
    ]
    assert data["items"] == [
        {"title": "Show - S01E02 - Pilot", "status": "online", "subtitle": "alice", "progress": 25.0, "meta": "Living room TV | Transcode"},
        {"title": "Heat", "status": "maintenance", "subtitle": "carol", "meta": "Jellyfin Web | DirectPlay | paused"},
    ]
    assert _paths(h) == ["/Sessions", "/Items/Counts"] and "ActiveWithinSeconds=960" in h.calls[0][1]
    assert set(seen) == {"K"}
    h.close()


def test_jellyfin_idle_server_is_valid():
    h = Harness("jellyfin", {"url": "http://jf:8096", "api_key": "K"}, _jf_routes([], {}))
    data = h.fetch()
    assert _values(data) == {"streams": 0, "transcodes": 0, "movies": None, "series": None, "episodes": None}
    assert data["items"] == []
    h.close()


def test_jellyfin_progress_maths():
    p = jellyfin_mod._progress
    item = {"RunTimeTicks": 200}
    assert p({"PlayState": {"PositionTicks": 50}}, item) == 25.0
    assert p({"PlayState": {"PositionTicks": 500}}, item) == 100.0  # clamped
    assert p({"PlayState": {"PositionTicks": 50}}, {}) is None  # runtime missing
    assert p({"PlayState": {"PositionTicks": 50}}, {"RunTimeTicks": 0}) is None
    assert p({"PlayState": {}}, item) is None  # position missing
    assert p({}, item) is None


# ---------------------------------------------------------------- plex
PLEX_SESSIONS = {"MediaContainer": {"size": 2, "Metadata": [
    {"type": "episode", "title": "Pilot", "grandparentTitle": "Show", "parentIndex": 1, "index": 2,
     "viewOffset": 600000, "duration": 2400000, "User": {"title": "alice"},
     "Player": {"state": "playing", "title": "Living room"},
     "TranscodeSession": {"videoDecision": "transcode", "audioDecision": "copy"}},
    {"type": "movie", "title": "Heat", "year": 1995, "viewOffset": 5000, "User": {"title": "bob"},
     "Player": {"state": "paused", "product": "Plex Web"}},
]}}


def _plex_routes(seen=None, over=None):
    def sessions(request):
        if seen is not None:
            seen.append((request.headers.get("x-plex-token"), request.headers.get("accept")))
        return PLEX_SESSIONS

    routes = {
        ("GET", r"/status/sessions$"): sessions,
        ("GET", r"/library/sections$"): {"MediaContainer": {"Directory": [
            {"key": "1", "type": "movie"}, {"key": "2", "type": "show"}, {"key": "3", "type": "artist"}, {"key": "4", "type": "movie"}]}},
        ("GET", r"/library/sections/1/all"): {"MediaContainer": {"size": 0, "totalSize": 100}},
        ("GET", r"/library/sections/4/all"): {"MediaContainer": {"size": 0, "totalSize": 20}},
        ("GET", r"/library/sections/2/all"): {"MediaContainer": {"size": 0, "totalSize": 30}},
    }
    routes.update(over or {})
    return routes


def test_plex_populated_sessions_and_library_counts():
    seen = []
    h = Harness("plex", {"url": "http://plex:32400", "token": "K"}, _plex_routes(seen))
    data = h.fetch()
    assert data["stats"] == [
        {"key": "streams", "label": "Streams", "value": 2, "tone": "info"},
        {"key": "transcodes", "label": "Transcoding", "value": 1, "tone": "warn"},
        {"key": "movies", "label": "Movies", "value": 120},
        {"key": "shows", "label": "Shows", "value": 30},
    ]
    assert data["items"] == [
        {"title": "Show - S01E02 - Pilot", "status": "online", "subtitle": "alice", "progress": 25.0, "meta": "Living room | Transcode"},
        {"title": "Heat (1995)", "status": "maintenance", "subtitle": "bob", "meta": "Plex Web | Direct play"},
    ]
    assert seen == [("K", "application/json")]
    assert _paths(h)[:2] == ["/status/sessions", "/library/sections"]
    assert "X-Plex-Container-Size=0" in next(u for _, u in h.calls if u.endswith("sections/1/all?X-Plex-Container-Start=0&X-Plex-Container-Size=0"))
    h.close()


def test_plex_idle_server_without_library_counts():
    h = Harness("plex", {"url": "http://plex:32400", "token": "K", "library_counts": False},
                {("GET", r"/status/sessions$"): {"MediaContainer": {"size": 0}}})
    data = h.fetch()
    assert data["stats"] == [{"key": "streams", "label": "Streams", "value": 0}, {"key": "transcodes", "label": "Transcoding", "value": 0}]
    assert data["items"] == [] and len(h.calls) == 1
    h.close()


def test_plex_a_failing_library_count_is_skipped():
    h = Harness("plex", {"url": "http://plex:32400", "token": "K"}, _plex_routes(over={
        ("GET", r"/library/sections/2/all"): (500, {}), ("GET", r"/library/sections/4/all"): (500, {})}))
    assert _values(h.fetch()) == {"streams": 2, "transcodes": 1, "movies": 100}
    h.close()


def test_plex_progress_maths_and_transcode_detection():
    p = plex_mod._progress
    assert p({"viewOffset": 500, "duration": 2000}) == 25.0
    assert p({"viewOffset": 5000, "duration": 2000}) == 100.0
    assert p({"viewOffset": 500}) is None  # duration missing
    assert p({"viewOffset": 500, "duration": 0}) is None
    assert p({"duration": 2000}) == 0.0  # just started: no offset yet
    assert plex_mod._transcoding({"TranscodeSession": {"videoDecision": "copy", "audioDecision": "copy"}}) is False
    assert plex_mod._transcoding({"TranscodeSession": {"audioDecision": "transcode"}}) is True
    assert plex_mod._transcoding({}) is False
