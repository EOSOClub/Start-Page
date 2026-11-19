"""Fixture-test helper for integration types.

    h = Harness("pihole", {"url": "http://pi.hole", "password": "pw"}, {
        ("POST", r"/api/auth$"): {"session": {"valid": True, "sid": "abc"}},
        ("GET", r"/api/stats/summary"): {...recorded upstream JSON...},
    })
    data = h.fetch()          # runs spec.fetch(ctx) on an httpx.MockTransport, checks the normalised shape
    h.calls                   # [(method, url)] the fetch made, in order
    h.ctx.state               # survives between h.fetch() calls, like in production
    h.close()

Routes map `(METHOD, url-regex)` to a response: a dict/list (200 JSON), `(status, body)`,
an httpx.Response, or a callable `request -> any of those`. The regex is searched in the full
URL including the query. The first matching route wins; an unmatched request fails the test.
"""
import asyncio
import re
from typing import Any, Callable

import httpx

from app.integrations import SPECS, Context, check_shape


def _response(value: Any, request: httpx.Request) -> httpx.Response:
    if callable(value):
        value = value(request)
    if isinstance(value, httpx.Response):
        return value
    if isinstance(value, tuple):
        status, body = value
        return httpx.Response(status, json=body, request=request) if not isinstance(body, (str, bytes)) else httpx.Response(status, content=body, request=request)
    if isinstance(value, (str, bytes)):
        return httpx.Response(200, content=value, request=request)
    return httpx.Response(200, json=value, request=request)


class Harness:
    def __init__(self, type_: str, config: dict, routes: dict[tuple[str, str], Any]):
        self.spec = SPECS[type_]
        self.routes = dict(routes)
        self.calls: list[tuple[str, str]] = []
        self.ctx = Context(self.spec, config, integration_id="test")
        self.ctx._client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle), timeout=5.0)
        self.loop = asyncio.new_event_loop()

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url))
        for (method, pattern), value in self.routes.items():
            if method == request.method and re.search(pattern, url):
                return _response(value, request)
        raise AssertionError(f"no fixture for {request.method} {url}")

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def fetch(self, *, check: bool = True) -> Any:
        data = self.run(self.spec.fetch(self.ctx))
        if check:
            assert_normalised(self.spec, data)
        return data

    def action(self, name: str, payload: dict) -> Any:
        return self.run(self.spec.actions[name].fn(self.ctx, payload))

    def close(self) -> None:
        self.run(self.ctx.aclose())
        self.loop.close()


def run_fetch(type_: str, config: dict, routes: dict[tuple[str, str], Any]) -> Any:
    """One fetch on a fresh harness (use Harness directly when state must survive between fetches)."""
    h = Harness(type_, config, routes)
    try:
        return h.fetch()
    finally:
        h.close()


def assert_normalised(spec, data: Any) -> None:
    """The result matches the normalised shape and delivers what `spec.provides` promises."""
    check_shape(data)
    if "stats" in spec.provides:
        assert isinstance(data, dict) and data["stats"], f"{spec.type}: provides stats but returned none"
    if "items" in spec.provides:
        assert isinstance(data, dict) and isinstance(data.get("items"), list), f"{spec.type}: provides items"


def without(data: dict, *keys: str) -> dict:
    """`data` minus the normalised keys, to compare a ported type's original payload exactly."""
    return {k: v for k, v in data.items() if k not in keys}
