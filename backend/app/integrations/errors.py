"""Safe error text for integrations.

Exception messages from httpx and friends carry the full request URL, query string
(API keys such as `?apikey=...` or `&auth=...`) and sometimes userinfo. Nothing built from
`str(exc)` may reach the API. `safe_error` builds a short fixed-vocabulary message with the
host but no path, query or userinfo; `redact` makes an exception text fit for the log.
"""
import asyncio
import re
import socket
import ssl
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError


class IntegrationError(Exception):
    """A failure whose message the integration module wrote itself and which is safe to show
    (no URLs, tokens or response bodies). Everything else is reduced by `safe_error`."""


class AuthFailed(IntegrationError):
    """A login was refused (or skipped because of the backoff after a refusal); the message is safe."""


_URL = re.compile(r"""[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'"<>)\]]+""")


def _strip_url(match: re.Match, host_only: bool = False) -> str:
    text = match.group(0)
    try:
        u = urlsplit(text)
        host = u.netloc.rpartition("@")[2]  # drop userinfo
        return f"{u.scheme}://{host}" + ("" if host_only else u.path)  # drop query and fragment
    except ValueError:
        return text.split("?")[0].split("#")[0].rpartition("@")[2]


def redact(text: str, host_only: bool = False) -> str:
    """`text` with every URL reduced to scheme://host[:port]/path (no userinfo, query or fragment);
    with `host_only` the path goes too (types whose URL path is itself the credential)."""
    return _URL.sub(lambda m: _strip_url(m, host_only), text)


def log_text(exc: BaseException, host_only: bool = False) -> str:
    """The exception as a log line. Exceptions that can embed header values, credentials or response
    data in their message (h11 / httpx protocol errors, pydantic validation errors, unicode errors)
    are logged by class name only, along with the class names of their causes."""
    chain = list(_chain(exc))
    if any(isinstance(e, (httpx.ProtocolError, ValidationError, UnicodeError)) for e in chain):
        return " <- ".join(type(e).__name__ for e in chain)
    return f"{type(exc).__name__}: {redact(str(exc), host_only)[:500]}"


def host_of(url: Any) -> str:
    """`host[:port]` of a URL or bare `host:port` string; "" when there is none (unix sockets)."""
    if not url:
        return ""
    raw = str(url).strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        u = urlsplit(raw)
        if not u.hostname:
            return ""
        return u.hostname + (f":{u.port}" if u.port else "")
    except ValueError:
        return ""


def _chain(exc: BaseException):
    seen, stack = set(), [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        stack.extend((e.__cause__, e.__context__))


def _request_host(exc: BaseException, cfg: Optional[dict]) -> str:
    request = None
    try:
        request = getattr(exc, "request", None)
    except RuntimeError:  # httpx raises when a RequestError has no request attached
        pass
    if request is not None:
        h = request.url.host
        if h:
            return h + (f":{request.url.port}" if request.url.port else "")
    return host_of((cfg or {}).get("url"))


def _reason(phrase: str) -> str:
    """The upstream-controlled reason phrase, reduced to a short printable ASCII text."""
    return "".join(c for c in str(phrase or "")[:40] if " " <= c <= "~").strip()


def safe_error(exc: BaseException, cfg: Optional[dict] = None) -> str:
    """One short message, safe for any viewer: what went wrong and (for network errors) which host."""
    if isinstance(exc, IntegrationError):
        return str(exc)[:200] or "error"
    host = _request_host(exc, cfg)
    at = f" ({host})" if host else ""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} {_reason(exc.response.reason_phrase)}".strip() + at
    chain = list(_chain(exc))
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "timeout" + at
    if any(isinstance(e, httpx.LocalProtocolError) for e in chain):
        return "invalid characters in a credential or header" + at
    if any(isinstance(e, ssl.SSLCertVerificationError) for e in chain):
        return (f'TLS certificate not trusted{at}: paste its CA under "Trusted CA certificate" '
                'or turn off "Verify TLS certificate"')
    if any(isinstance(e, ssl.SSLError) for e in chain):
        return "TLS error" + at
    if any(isinstance(e, ConnectionRefusedError) for e in chain):
        return "connection refused" + at
    if any(isinstance(e, socket.gaierror) for e in chain):
        return "DNS lookup failed" + at
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return "invalid URL"
    if isinstance(exc, httpx.TransportError):
        return "connection failed" + at
    if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError, IndexError, ValidationError)):
        return "invalid response" + at
    if isinstance(exc, ImportError):
        return "a required Python package is missing"
    return f"unexpected error ({type(exc).__name__})"
