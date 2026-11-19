"""Shared HTTP helpers for integration types: manual redirects that never leak configured
credentials to another origin, and body reads with a strict cap on DECODED bytes."""
import zlib
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

import httpx

from .errors import IntegrationError

MAX_REDIRECTS = 5
ACCEPT_ENCODING = "gzip, deflate"  # what read_capped can decode with a bound


def origin_of(url: str) -> tuple[str, str, int]:
    u = urlsplit(str(url))
    scheme = (u.scheme or "http").lower()
    port = u.port or (443 if scheme == "https" else 80)
    return scheme, (u.hostname or "").lower(), port


@asynccontextmanager
async def stream_follow(client: httpx.AsyncClient, method: str, url: str, *, headers: Optional[dict] = None,
                        plain_headers: Optional[dict] = None, auth: Optional[httpx.Auth] = None,
                        max_redirects: int = MAX_REDIRECTS) -> AsyncIterator[httpx.Response]:
    """Stream a request, following up to `max_redirects` redirects by hand. `headers` and `auth`
    (credentials) are sent only to the origin of the first URL; `plain_headers` (Accept ...) go everywhere.
    Yields the final response; it is closed on exit."""
    first = origin_of(url)
    cur_method, cur = method, str(url)
    resp: Optional[httpx.Response] = None
    try:
        for _ in range(max_redirects + 1):
            same = origin_of(cur) == first
            h = {"Accept-Encoding": ACCEPT_ENCODING, **(plain_headers or {}), **((headers or {}) if same else {})}
            req = client.build_request(cur_method, cur, headers=h)
            use_auth = auth if (same and auth is not None) else httpx.USE_CLIENT_DEFAULT
            resp = await client.send(req, stream=True, auth=use_auth, follow_redirects=False)
            loc = resp.headers.get("location")
            if resp.status_code in (301, 302, 303, 307, 308) and loc:
                nxt = str(resp.url.join(loc))
                status = resp.status_code
                await resp.aclose()
                resp = None
                if urlsplit(nxt).scheme.lower() not in ("http", "https"):
                    raise IntegrationError("redirect to an unsupported address")
                if status == 303 and cur_method not in ("GET", "HEAD"):
                    cur_method = "GET"  # these requests carry no body, so only the method changes
                cur = nxt
                continue
            yield resp
            return
        raise IntegrationError("too many redirects")
    finally:
        if resp is not None:
            await resp.aclose()


async def read_capped(resp: httpx.Response, limit: int, *, truncate: bool = False, message: str = "response is too large") -> bytes:
    """The response body, at most `limit` DECODED bytes. Over the limit: truncate, or raise IntegrationError.
    gzip/deflate bodies are inflated here with a bound, so a small compressed body cannot expand past the cap."""
    enc = resp.headers.get("content-encoding", "identity").strip().lower()
    out: list[bytes] = []
    total = 0

    def take(data: bytes) -> bool:
        """Add data; True when the cap was hit."""
        nonlocal total
        room = limit - total
        if len(data) > room:
            if not truncate:
                raise IntegrationError(message)
            out.append(data[:room])
            total = limit
            return True
        out.append(data)
        total += len(data)
        return False

    if enc in ("", "identity") or resp.is_stream_consumed:
        async for chunk in resp.aiter_bytes():
            if take(chunk):
                break
    elif enc in ("gzip", "x-gzip", "deflate"):
        wbits = 16 + zlib.MAX_WBITS if enc != "deflate" else zlib.MAX_WBITS
        d = zlib.decompressobj(wbits)
        first = True
        stop = False
        async for raw in resp.aiter_raw():
            buf = raw
            while buf and not stop:
                try:
                    data = d.decompress(buf, 65536)
                except zlib.error:
                    if enc == "deflate" and first:
                        d = zlib.decompressobj(-zlib.MAX_WBITS)  # raw deflate without the zlib header
                        data = d.decompress(buf, 65536)
                    else:
                        raise IntegrationError("invalid response (bad compressed data)") from None
                first = False
                stop = take(data)
                buf = d.unconsumed_tail
                if d.eof:
                    break
            if stop or d.eof:
                break
    else:
        raise IntegrationError("invalid response (unsupported content encoding)")
    return b"".join(out)
