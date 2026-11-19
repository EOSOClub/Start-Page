"""The installable app: manifest, service worker and icon files served from the site root."""
import pytest
from fastapi.testclient import TestClient

from app import pwa
from app.main import app
from conftest import HEADERS

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
PW = "correct horse battery"


@pytest.fixture(name="static")
def static_fixture(tmp_path, monkeypatch):
    """A fake build root, so these tests never depend on whether `npm run build` ran."""
    for name in pwa.ROOT_FILES:
        (tmp_path / name).write_bytes(PNG)
    (tmp_path / "sw.js").write_text("self.addEventListener('fetch', () => {});", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("do not serve", encoding="utf-8")
    monkeypatch.setattr(pwa, "STATIC_DIR", tmp_path)
    monkeypatch.delenv("STARTPAGE_DISABLE_SW", raising=False)
    return tmp_path


def anon() -> TestClient:
    return TestClient(app)


# ---------- manifest ----------
def test_manifest_uses_the_global_settings(client):
    client.patch("/api/settings", json={"title": "Homelab", "accent": "#ff8800", "background_color": "#102030"}, headers=HEADERS)
    res = client.get("/manifest.webmanifest")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/manifest+json")
    assert "no-cache" in res.headers["cache-control"]
    m = res.json()
    assert (m["name"], m["short_name"]) == ("Homelab", "Homelab")
    assert (m["theme_color"], m["background_color"]) == ("#ff8800", "#102030")
    assert (m["display"], m["start_url"], m["scope"]) == ("standalone", "/", "/")
    icons = {(i["src"], i["sizes"], i["purpose"]) for i in m["icons"]}
    assert ("/icon-192.png", "192x192", "any") in icons
    assert ("/icon-512.png", "512x512", "any") in icons
    assert ("/icon-maskable-512.png", "512x512", "maskable") in icons


def test_manifest_defaults_and_bad_values(client):
    m = client.get("/manifest.webmanifest").json()
    assert (m["name"], m["theme_color"], m["background_color"]) == ("Start Page", "#6c8cff", "#0f1115")
    client.patch("/api/settings", json={"theme": "light", "title": "A very long start page title"}, headers=HEADERS)
    m = client.get("/manifest.webmanifest").json()
    assert m["background_color"] == "#f3f4f7"
    assert m["name"] == "A very long start page title" and len(m["short_name"]) <= 12
    client.patch("/api/settings", json={"accent": "red;}</style>", "background_color": "url(x)"}, headers=HEADERS)
    m = client.get("/manifest.webmanifest").json()
    assert (m["theme_color"], m["background_color"]) == ("#6c8cff", "#f3f4f7")  # not a hex colour: the default


def test_manifest_is_generic_for_anonymous_callers_when_guests_are_refused(client):
    client.patch("/api/settings", json={"title": "Private Lab", "accent": "#ff0000"}, headers=HEADERS)
    client.post("/api/auth/password", json={"new": PW}, headers=HEADERS)
    res = anon().get("/manifest.webmanifest")
    assert res.status_code == 200  # reachable without logging in
    m = res.json()
    assert (m["name"], m["short_name"], m["theme_color"]) == ("Start Page", "Start Page", "#6c8cff")
    assert "Private Lab" not in res.text and "#ff0000" not in res.text
    assert res.headers["cache-control"].startswith("private")
    assert client.get("/manifest.webmanifest").json()["name"] == "Private Lab"  # the signed-in browser keeps its own


def test_manifest_shows_the_title_to_read_only_guests(client):
    client.patch("/api/settings", json={"title": "Public Lab"}, headers=HEADERS)
    client.post("/api/auth/password", json={"new": PW}, headers=HEADERS)
    assert client.patch("/api/auth/settings", json={"guest_access": "read"}, headers=HEADERS).status_code == 200
    assert anon().get("/manifest.webmanifest").json()["name"] == "Public Lab"


def test_manifest_is_not_under_api_and_not_the_spa_shell(client):
    res = client.get("/manifest.webmanifest")
    assert not res.headers["content-type"].startswith("text/html")


# ---------- service worker ----------
def test_service_worker_mime_and_headers(client, static):
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/javascript")
    cache = res.headers["cache-control"]
    assert "no-cache" in cache and "immutable" not in cache and "max-age=31536000" not in cache
    assert "fetch" in res.text


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_disable_sw_makes_sw_js_a_404(client, static, monkeypatch, value):
    monkeypatch.setenv("STARTPAGE_DISABLE_SW", value)
    res = client.get("/sw.js")
    assert res.status_code == 404
    assert "fetch" not in res.text


def test_disable_sw_off_values_keep_it_served(client, static, monkeypatch):
    monkeypatch.setenv("STARTPAGE_DISABLE_SW", "0")
    assert client.get("/sw.js").status_code == 200


def test_missing_sw_file_is_a_404_not_the_shell(client, static):
    (static / "sw.js").unlink()
    res = client.get("/sw.js")
    assert res.status_code == 404
    assert not res.headers["content-type"].startswith("text/html")


# ---------- icons and the root allowlist ----------
@pytest.mark.parametrize("name", sorted(pwa.ROOT_FILES))
def test_icons_are_png_with_a_short_cache(client, static, name):
    res = client.get(f"/{name}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content == PNG
    assert "immutable" not in res.headers["cache-control"]
    assert "max-age=" in res.headers["cache-control"]


def test_missing_icon_is_a_404(client, static):
    (static / "icon-192.png").unlink()
    assert client.get("/icon-192.png").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/startpage.db",
        "/secret.txt",  # exists in the fake root but is not on the allowlist
        "/../startpage.db",
        "/%2e%2e/startpage.db",
        "/..%2fdata/startpage.db",
        "/icon-192.png/../secret.txt",
        "/ICON-192.PNG",  # routes are case-sensitive, so a case-insensitive file system never matters
        "/sw.js.map",
        "/data/startpage.db",
    ],
)
def test_root_allowlist_serves_nothing_else(client, static, path):
    res = client.get(path)
    assert res.status_code in (404, 307, 308)
    assert b"do not serve" not in res.content
    assert not res.content.startswith(b"SQLite format")


def test_unknown_api_paths_still_404_json(client, static):
    res = client.get("/api/manifest.webmanifest")
    assert res.status_code == 404
    assert res.json() == {"detail": "Not found"}


def test_assets_404_is_not_cached_as_immutable(client):
    from pathlib import Path
    from app import main as main_mod
    res = client.get("/assets/does-not-exist.js")
    assert res.status_code == 404
    assert "immutable" not in res.headers.get("cache-control", "")
    assets = Path(main_mod.STATIC_DIR) / "assets"
    found = next((p for p in assets.glob("*.js")), None) if assets.is_dir() else None
    if found:  # a real, hashed file keeps the long-lived header
        ok = client.get(f"/assets/{found.name}")
        assert ok.status_code == 200 and "immutable" in ok.headers["cache-control"]
