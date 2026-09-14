"""Site icons and titles for links, fetched by the backend and cached on disk.

Fetching server-side means LAN services work (the browser page may not be able to
read them cross-origin) and no third-party favicon service learns your bookmarks.
"""
import asyncio
import hashlib
import json
import logging
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..database import DATA_DIR

log = logging.getLogger("startpage.favicons")
router = APIRouter(prefix="/api", tags=["favicons"])

CACHE_DIR = DATA_DIR / "favicons"
ICON_TTL = 7 * 86400
MISS_TTL = 6 * 3600  # retry sites that had no usable icon after this long
MAX_HTML = 512 * 1024
MAX_ICON = 1024 * 1024
HEADERS = {
    "User-Agent": "StartPage/1.0 (self-hosted start page; favicon fetcher)",
    "Accept": "text/html,application/xhtml+xml,image/*;q=0.9,*/*;q=0.8",
}
# Unreachable LAN hosts should fail fast so the letter fallback shows quickly.
TIMEOUT = httpx.Timeout(6.0, connect=2.0)

_locks: dict[str, asyncio.Lock] = {}


def _normalize(url: str) -> tuple[str, str]:
    """Return (full url, origin) or raise 400."""
    url = url.strip()
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(400, "Not an http(s) URL")
    return url, f"{parts.scheme}://{parts.netloc}"


class _HeadParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[tuple[int, str]] = []  # (score, href)
        self.title = ""
        self._in_title = False
        self._title_done = False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "title" and not self._title_done:  # later <title>s belong to inline SVGs
            self._in_title = True
        elif tag == "link" and a.get("href"):
            rel = a.get("rel", "").lower().split()
            if "icon" in rel or "apple-touch-icon" in rel:
                self.icons.append((_icon_score(rel, a), a["href"]))

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True

    def handle_data(self, data):
        if self._in_title and len(self.title) < 200:
            self.title += data


def _icon_score(rel: list[str], attrs: dict) -> int:
    """Prefer big, crisp icons: svg > apple-touch-icon > largest declared size."""
    href = attrs["href"].lower()
    if href.endswith(".svg") or attrs.get("type") == "image/svg+xml":
        return 10_000
    best = 0
    for size in attrs.get("sizes", "").lower().split():
        w, _, _ = size.partition("x")
        if w.isdigit():
            best = max(best, int(w))
    if "apple-touch-icon" in rel:
        best = max(best, 180)
    return best or 16


async def _read_limited(resp: httpx.Response, limit: int) -> bytes:
    chunks, total = [], 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            break
    return b"".join(chunks)[:limit]


def _looks_like_image(content_type: str, body: bytes) -> bool:
    if content_type.startswith("image/"):
        return True
    head = body[:64].lstrip()
    return head.startswith((b"\x89PNG", b"\x00\x00\x01\x00", b"GIF8", b"\xff\xd8", b"<svg", b"<?xml")) or (
        head.startswith(b"RIFF") and b"WEBP" in head
    )


def _sniff_type(content_type: str, body: bytes) -> str:
    if content_type.startswith("image/"):
        return content_type.split(";")[0]
    head = body[:64].lstrip()
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"RIFF"):
        return "image/webp"
    return "image/svg+xml"


async def _fetch(url: str, origin: str) -> dict:
    """Fetch title + best icon for a site. Returns meta dict and writes the icon to disk."""
    key = hashlib.sha1(origin.encode()).hexdigest()
    meta = {"origin": origin, "title": "", "type": None, "fetched": time.time()}
    async with httpx.AsyncClient(timeout=TIMEOUT, verify=False, follow_redirects=True, headers=HEADERS) as client:
        candidates: list[str] = []
        try:
            async with client.stream("GET", url) as resp:
                base = str(resp.url)
                if "html" in resp.headers.get("content-type", ""):
                    parser = _HeadParser()
                    parser.feed((await _read_limited(resp, MAX_HTML)).decode(resp.encoding or "utf-8", "replace"))
                    meta["title"] = " ".join(parser.title.split())
                    candidates = [urljoin(base, href) for _, href in sorted(parser.icons, reverse=True)]
                candidates.append(urljoin(base, "/favicon.ico"))
        except Exception as exc:  # noqa: BLE001 - any failure just means "no metadata"
            log.info("favicon page fetch failed for %s: %s", url, exc)
            # If the host can't be reached at all, don't wait a second time for /favicon.ico.
            candidates = [] if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) else [origin + "/favicon.ico"]

        for icon_url in candidates:
            if icon_url.startswith("data:"):
                continue
            try:
                async with client.stream("GET", icon_url) as resp:
                    if resp.status_code != 200:
                        continue
                    body = await _read_limited(resp, MAX_ICON + 1)
                    ctype = resp.headers.get("content-type", "").lower()
                    if len(body) > MAX_ICON or not body or not _looks_like_image(ctype, body):
                        continue
                    meta["type"] = _sniff_type(ctype, body)
                    (CACHE_DIR / f"{key}.img").write_bytes(body)
                    break
            except (httpx.HTTPError, httpx.InvalidURL):
                continue

    (CACHE_DIR / f"{key}.json").write_text(json.dumps(meta))
    return meta


async def _meta(url: str) -> tuple[dict, str]:
    url, origin = _normalize(url)
    key = hashlib.sha1(origin.encode()).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = CACHE_DIR / f"{key}.json"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text())
            ttl = ICON_TTL if meta.get("type") else MISS_TTL
            if time.time() - meta.get("fetched", 0) < ttl:
                return meta, key
        return await _fetch(url, origin), key


@router.get("/favicon")
async def favicon(url: str = Query(...)):
    meta, key = await _meta(url)
    img = CACHE_DIR / f"{key}.img"
    if not meta.get("type") or not img.is_file():
        raise HTTPException(404, "No icon")
    headers = {"Cache-Control": "public, max-age=86400"}
    if meta["type"] == "image/svg+xml":
        # An SVG opened directly could run script; lock it down.
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(img.read_bytes(), media_type=meta["type"], headers=headers)


@router.get("/sitemeta")
async def sitemeta(url: str = Query(...)):
    meta, _ = await _meta(url)
    return {"title": meta.get("title", ""), "has_icon": bool(meta.get("type"))}
