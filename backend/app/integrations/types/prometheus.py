"""Prometheus: one stat per `Label = promql [| unit]` line, from instant queries (/api/v1/query).

Each query's first sample is shown (no summing). When `range_minutes` is set, /api/v1/query_range is
also called per query and the first series becomes the stat's `series` (sparkline data).
A query that fails shows an empty bad-toned stat; the fetch only fails when every query does.
"""
import asyncio
import math
import re
import time
from typing import Any, Optional

import httpx

from ..base import Context, Spec, stat
from ..errors import IntegrationError

MAX_QUERIES = 20
RANGE_POINTS = 60
MAX_LINES = 200
MAX_LINE = 2000
_UNIT_BAD = set("|\"'{}()[]")


def _split_unit(rest: str) -> tuple[str, Optional[str]]:
    """(query, unit) for `promql | unit`: the text after the LAST `|` is the unit when it is short and
    plain (no regex: a pathological line cannot make this slow)."""
    head, sep, tail = rest.rpartition("|")
    unit = tail.strip()
    if sep and head[-1:].isspace() and unit and len(unit) <= 16 and not (_UNIT_BAD & set(unit)):
        return head.strip(), unit
    return rest, None


def parse_queries(text: str) -> list[dict]:
    """[{"key", "label", "query", "unit"}] from the multi-line field. Raises ValueError("line N: ...")."""
    out, seen = [], set()
    lines = (text or "").splitlines()
    if len(lines) > MAX_LINES:
        raise ValueError(f"at most {MAX_LINES} lines")
    for n, line in enumerate(lines, 1):
        if len(line) > MAX_LINE:
            raise ValueError(f"line {n}: longer than {MAX_LINE} characters")
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        label, sep, rest = line.partition("=")
        label, rest = label.strip(), rest.strip()
        if not sep or not label or not rest:
            raise ValueError(f"line {n}: expected `Label = promql`")
        rest, unit = _split_unit(rest)
        if not rest:
            raise ValueError(f"line {n}: the query is empty")
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or f"q{n}"
        while key in seen:
            key += "_"
        seen.add(key)
        out.append({"key": key, "label": label, "query": rest, "unit": unit})
    if len(out) > MAX_QUERIES:
        raise ValueError(f"at most {MAX_QUERIES} queries")
    return out


def _validate(cfg: dict) -> list[tuple[str, str]]:
    try:
        if not parse_queries(cfg.get("queries", "")):
            return [("queries", "add at least one `Label = promql` line")]
    except ValueError as exc:
        return [("queries", str(exc))]
    return []


def _number(raw: Any) -> Optional[float]:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, 4)


def _headers(ctx: Context) -> dict:
    token = ctx.cfg.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _auth(ctx: Context) -> Optional[httpx.BasicAuth]:
    if ctx.cfg.get("token") or not ctx.cfg.get("username"):
        return None
    return httpx.BasicAuth(ctx.cfg["username"], ctx.cfg.get("password") or "")


async def _get(ctx: Context, path: str, params: dict) -> Any:
    resp = await ctx.client.get(ctx.url(path), params=params, headers=_headers(ctx), auth=_auth(ctx) or httpx.USE_CLIENT_DEFAULT)
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "success":
        raise IntegrationError("Prometheus query failed")  # the server's message can echo the query: not shown
    return body["data"]


def _first_sample(data: dict) -> Optional[float]:
    result = data.get("result")
    if data.get("resultType") in ("scalar", "string"):
        return _number(result[1]) if result else None
    if not result:
        return None
    return _number(result[0]["value"][1])


async def _one(ctx: Context, q: dict, range_s: Optional[int]) -> dict:
    try:
        value = _first_sample(await _get(ctx, "/api/v1/query", {"query": q["query"]}))
        series = None
        if range_s:
            end = time.time()
            data = await _get(ctx, "/api/v1/query_range", {
                "query": q["query"], "start": end - range_s, "end": end, "step": max(15, range_s // RANGE_POINTS),
            })
            if data.get("result"):
                series = [v for v in (_number(p[1]) for p in data["result"][0]["values"]) if v is not None]
        return stat(q["key"], q["label"], value, q["unit"], series=series)
    except (httpx.HTTPError, IntegrationError, ValueError, KeyError, IndexError, TypeError):
        return stat(q["key"], q["label"], None, q["unit"], "bad")


async def fetch(ctx: Context) -> dict:
    queries = parse_queries(ctx.cfg.get("queries", ""))
    if not queries:
        raise IntegrationError("no queries configured")
    minutes = ctx.cfg.get("range_minutes")
    range_s = int(minutes * 60) if minutes else None
    stats = await asyncio.gather(*(_one(ctx, q, range_s) for q in queries))
    if all(s.get("tone") == "bad" for s in stats):
        raise IntegrationError("every query failed (check the URL, credentials and the PromQL)")
    return {"stats": list(stats)}


SPEC = Spec(
    type="prometheus",
    label="Prometheus",
    description="Numbers from PromQL instant queries, optionally with a sparkline from the last N minutes.",
    fetch=fetch,
    default_interval=30,
    provides=("stats",),
    validate=_validate,
    fields=[
        {"key": "url", "label": "Prometheus URL", "type": "url", "required": True, "placeholder": "http://192.168.1.10:9090"},
        {"key": "queries", "label": "Queries (one per line: Label = promql | unit)", "type": "textarea", "required": True,
         "rows": 4, "max_length": 8000, "placeholder": "Up targets = sum(up) | targets\nCPU = 100 - avg(rate(node_cpu_seconds_total{mode=\"idle\"}[5m])) * 100 | %",
         "help": "The first sample of each query is shown (no summing: aggregate in PromQL). Lines starting with # are ignored."},
        {"key": "range_minutes", "label": "Sparkline range (minutes)", "type": "number", "min": 5, "max": 10080,
         "help": "Set to also fetch query_range and draw a sparkline per stat. Empty = numbers only."},
        {"key": "username", "label": "Basic auth user", "type": "text"},
        {"key": "password", "label": "Basic auth password", "type": "secret"},
        {"key": "token", "label": "Bearer token", "type": "secret", "help": "Used instead of basic auth when set."},
    ],
)
