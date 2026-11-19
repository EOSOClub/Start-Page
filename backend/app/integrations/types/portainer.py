"""Portainer: environment and container overview with an API key (header `X-API-Key`).

Stats come from GET /api/endpoints (each environment carries a Docker snapshot with running / stopped /
unhealthy container counts, images and volumes), so no per-environment Docker call is needed; stacks
come from GET /api/stacks (optional). The container list calls
/api/endpoints/{id}/docker/containers/json?all=1 for the chosen (or first online) environment.
Environments that are down are left out of the container counts (their snapshot is stale).
"""
import re
from typing import Any, Optional

import httpx

from ..base import Action, Context, Spec, stat
from ..errors import AuthFailed, IntegrationError

MAX_ITEMS = 50
_CID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ACTIONS = ("start", "stop", "restart")
_CID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _endpoint_id(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise IntegrationError("invalid environment id")
    s = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
    if not re.fullmatch(r"[0-9]{1,9}", s) or int(s) < 1:
        raise IntegrationError("invalid environment id")
    return int(s)


async def _get(ctx: Context, path: str, params: Optional[dict] = None) -> Any:
    resp = await ctx.client.get(ctx.url(path), params=params, headers={"X-API-Key": str(ctx.cfg.get("api_key", ""))})
    if resp.status_code in (401, 403):
        raise AuthFailed("login failed: check the Portainer API key")
    resp.raise_for_status()
    return resp.json()


def _snapshot(e: dict) -> dict:
    snaps = e.get("Snapshots")
    return snaps[0] if isinstance(snaps, list) and snaps and isinstance(snaps[0], dict) else {}


def _container_status(c: dict) -> str:
    state = str(c.get("State") or "").lower()
    text = str(c.get("Status") or "").lower()
    if state == "running":
        return "degraded" if "unhealthy" in text else "online"
    if state == "paused":
        return "maintenance"
    if state == "restarting":
        return "degraded"
    return "offline" if state in ("exited", "dead", "created", "removing") else "unknown"


async def fetch(ctx: Context) -> dict:
    endpoints = await _get(ctx, "/api/endpoints")
    if not isinstance(endpoints, list):
        raise IntegrationError("invalid response (is this a Portainer API?)")
    endpoints = [e for e in endpoints if isinstance(e, dict)]
    chosen = ctx.cfg.get("endpoint_id")
    if chosen:
        endpoints = [e for e in endpoints if _int(e.get("Id")) == _int(chosen)]
    up = [e for e in endpoints if _int(e.get("Status")) == 1]

    snaps = [_snapshot(e) for e in up]
    running = sum(_int(s.get("RunningContainerCount")) for s in snaps)
    stopped = sum(_int(s.get("StoppedContainerCount")) for s in snaps)
    unhealthy = sum(_int(s.get("UnhealthyContainerCount")) for s in snaps)
    stats = [
        stat("endpoints", "Environments up", f"{len(up)}/{len(endpoints)}", tone="ok" if len(up) == len(endpoints) and endpoints else "bad" if endpoints else None),
        stat("running", "Running", running),
        stat("stopped", "Stopped", stopped),
        stat("unhealthy", "Unhealthy", unhealthy, tone="bad" if unhealthy else "ok"),
    ]
    stacks: Optional[list] = None
    try:
        raw = await _get(ctx, "/api/stacks")
        if isinstance(raw, list):
            stacks = [s for s in raw if isinstance(s, dict)]
            if chosen:
                stacks = [s for s in stacks if _int(s.get("EndpointId")) == _int(chosen)]
    except (IntegrationError, httpx.HTTPError, ValueError):
        pass  # the key already worked for /api/endpoints; a role without stack access just has no stacks stat
    if stacks is not None:
        stats.append(stat("stacks", "Stacks", len(stacks)))
    if any(s for s in snaps):
        stats.append(stat("images", "Images", sum(_int(s.get("ImageCount")) for s in snaps)))
        stats.append(stat("volumes", "Volumes", sum(_int(s.get("VolumeCount")) for s in snaps)))

    which = ctx.cfg.get("list") or "containers"
    items: list[dict] = []
    if which == "endpoints":
        for e in endpoints:
            s = _snapshot(e)
            items.append({
                "title": str(e.get("Name") or f"Environment {e.get('Id')}"),
                "subtitle": str(e.get("URL") or "") or None,
                "status": "online" if _int(e.get("Status")) == 1 else "offline",
                "meta": f"{_int(s.get('RunningContainerCount'))} running" if s else None,
            })
    elif which == "stacks":
        names = {_int(e.get("Id")): str(e.get("Name") or "") for e in endpoints}
        for s in stacks or []:
            items.append({
                "title": str(s.get("Name") or f"Stack {s.get('Id')}"),
                "subtitle": names.get(_int(s.get("EndpointId"))) or None,
                "status": "online" if _int(s.get("Status")) == 1 else "offline",
            })
    elif endpoints:
        target = (up or endpoints)[0]
        raw = await _get(ctx, f"/api/endpoints/{_int(target.get('Id'))}/docker/containers/json", {"all": 1})
        if not isinstance(raw, list):
            raise IntegrationError("invalid response (container list)")
        eid = _int(target.get("Id"))
        for c in raw:
            if not isinstance(c, dict):
                continue
            names = c.get("Names") if isinstance(c.get("Names"), list) else []
            name = str(names[0]).lstrip("/") if names else str(c.get("Id") or "")[:12]
            item = {
                "title": name,
                "subtitle": str(c.get("Image") or "") or None,
                "status": _container_status(c),
                "meta": str(c.get("Status") or "") or None,
            }
            cid = str(c.get("Id") or "")
            if _CID.fullmatch(cid) and eid > 0:
                item["ref"] = {"endpoint_id": eid, "container_id": cid}  # the environment actually listed
            items.append(item)
        items.sort(key=lambda i: (i["status"] != "online", i["title"].lower()))
    return {"stats": stats, "items": items[:MAX_ITEMS]}


async def container(ctx: Context, payload: dict) -> None:
    eid = _endpoint_id(payload.get("endpoint_id"))
    limited = ctx.cfg.get("endpoint_id")
    if limited and eid != _int(limited):  # an integration limited to one environment only acts on that one
        raise IntegrationError("this integration is limited to another environment")
    cid, action = payload.get("container_id"), payload.get("action")
    if not isinstance(cid, str) or not _CID.fullmatch(cid):
        raise IntegrationError("invalid container id")
    if action not in _ACTIONS:
        raise IntegrationError("action must be start, stop or restart")
    resp = await ctx.client.post(ctx.url(f"/api/endpoints/{eid}/docker/containers/{cid}/{action}"),
                                 headers={"X-API-Key": str(ctx.cfg.get("api_key", ""))})
    if resp.status_code != 304:  # 304: already in that state
        resp.raise_for_status()


SPEC = Spec(
    type="portainer",
    label="Portainer",
    description="Portainer environments, containers and stacks, with container start/stop/restart (API key).",
    fetch=fetch,
    default_interval=30,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "Portainer URL", "type": "url", "required": True, "placeholder": "https://192.168.1.10:9443"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True,
         "help": "My account -> Access tokens."},
        {"key": "endpoint_id", "label": "Environment ID (optional)", "type": "number", "min": 1,
         "help": "Limits stats and lists to one environment (the number in its URL). Empty = all; the container list then uses the first online environment."},
        {"key": "list", "label": "List shows", "type": "select", "default": "containers",
         "options": [{"value": "containers", "label": "Containers"}, {"value": "endpoints", "label": "Environments"},
                     {"value": "stacks", "label": "Stacks"}]},
    ],
    actions={
        "container": Action(
            container,
            label="Container action",
            description="Start, stop or restart a container.",
            payload=[
                {"key": "endpoint_id", "label": "Environment ID", "type": "number", "required": True, "min": 1},
                {"key": "container_id", "label": "Container ID or name", "type": "text", "required": True, "pattern": _CID_PATTERN},
                {"key": "action", "label": "Action", "type": "select", "required": True,
                 "options": [{"value": a, "label": a.capitalize()} for a in _ACTIONS]},
            ],
        )
    },
)
