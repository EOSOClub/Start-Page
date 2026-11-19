"""Installable-app plumbing: the web app manifest, the service worker and the icon files.

These live at the site root (a service worker's scope is its own directory) and sit OUTSIDE /api,
so the auth guard never sees them; the manifest decides for itself what an anonymous caller may see.
Only the files named in ROOT_FILES are served from the build's root here. There is no path
parameter, so there is nothing to traverse.
"""
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from . import auth
from .database import get_db
from .routers.settings import load as load_settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# The only non-/assets files served from the build root (besides index.html through the SPA route).
ROOT_FILES = {
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png",
    "apple-touch-icon.png": "image/png",
}
ICON_CACHE = "public, max-age=86400"  # not content-hashed, so a day, not "immutable"

GENERIC_NAME = "Start Page"
DEFAULT_ACCENT = "#6c8cff"
DEFAULT_BG = {"light": "#f3f4f7"}  # every other theme (dark, system) falls back to the dark background
DARK_BG = "#0f1115"
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")

router = APIRouter(tags=["pwa"])


def sw_disabled() -> bool:
    """STARTPAGE_DISABLE_SW=1 is the kill switch: /sw.js answers 404 and clients unregister their worker."""
    return os.environ.get("STARTPAGE_DISABLE_SW", "").strip().lower() in ("1", "true", "yes", "on")


def _colour(value, default: str) -> str:
    return value.strip() if isinstance(value, str) and _HEX.fullmatch(value.strip()) else default


def build_manifest(settings: dict, personal: bool) -> dict:
    """The manifest for this caller. `personal` False gives the generic one that leaks nothing."""
    if personal:
        title = str(settings.get("title") or GENERIC_NAME).strip()[:60] or GENERIC_NAME
        accent = _colour(settings.get("accent"), DEFAULT_ACCENT)
        bg = _colour(settings.get("background_color"), DEFAULT_BG.get(settings.get("theme"), DARK_BG))
    else:
        title, accent, bg = GENERIC_NAME, DEFAULT_ACCENT, DARK_BG
    return {
        "id": "/",
        "name": title,
        "short_name": title[:12].strip() or GENERIC_NAME,
        "description": "A self-hosted start page",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": accent,
        "background_color": bg,
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest(request: Request, db: Session = Depends(get_db)):
    principal = auth.resolve(db, request)
    # Anyone may fetch it; only a caller who may read the page gets its title and colours.
    personal = not principal.enabled or principal.authenticated or principal.guest_access == "read"
    return JSONResponse(
        build_manifest(load_settings(db), personal),
        media_type="application/manifest+json",
        headers={"Cache-Control": "private, no-cache", "Vary": "Cookie, Authorization"},
    )


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    path = STATIC_DIR / "sw.js"
    if sw_disabled() or not path.is_file():
        return JSONResponse({"detail": "Not found"}, status_code=404, headers={"Cache-Control": "no-store"})
    # Browsers re-check the worker script on every start; it must never sit in an HTTP cache.
    return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


def _root_file(name: str):
    def handler() -> Response:
        path = STATIC_DIR / name
        if not path.is_file():
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(path, media_type=ROOT_FILES[name], headers={"Cache-Control": ICON_CACHE})

    return handler


for _name in ROOT_FILES:
    router.add_api_route(f"/{_name}", _root_file(_name), methods=["GET"], include_in_schema=False)
