"""Docker engine: containers and engine info over the socket or the TCP API. Start/stop/restart action."""
import re

import httpx

from ..base import Action, Context, Spec, default_client, stat
from ..errors import IntegrationError

SOCKET = "unix:///var/run/docker.sock"
_ID = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"  # leading alphanumeric: `.` / `..` would be normalised out of the path
ACTIONS = ("start", "stop", "restart")


def _client(ctx: Context) -> httpx.AsyncClient:
    url = str(ctx.cfg.get("url") or SOCKET).strip()
    if url.startswith("unix://"):
        return httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=url[len("unix://"):]), timeout=8.0)
    return default_client(ctx)


def _base(ctx: Context) -> str:
    url = str(ctx.cfg.get("url") or SOCKET).strip()
    return "http://docker" if url.startswith("unix://") else ctx.base_url()


async def fetch(ctx: Context) -> dict:
    base = _base(ctx)
    all_flag = "1" if ctx.cfg.get("show_all", True) else "0"
    containers = (await ctx.client.get(f"{base}/containers/json", params={"all": all_flag})).raise_for_status().json()
    info = (await ctx.client.get(f"{base}/info")).raise_for_status().json()
    rows = []
    for c in containers:
        rows.append({
            "id": c["Id"][:12],
            "name": (c.get("Names") or ["?"])[0].lstrip("/"),
            "image": c.get("Image"),
            "state": c.get("State"),
            "status": c.get("Status"),
            "ports": sorted({f"{p.get('PublicPort')}->{p.get('PrivatePort')}" for p in (c.get("Ports") or []) if p.get("PublicPort")}),
            "project": (c.get("Labels") or {}).get("com.docker.compose.project", ""),
        })
    rows.sort(key=lambda r: (r["project"], r["name"]))
    stopped = info.get("ContainersStopped")
    return {
        "host": info.get("Name"),
        "version": info.get("ServerVersion"),
        "containers": rows,
        "running": info.get("ContainersRunning"),
        "stopped": stopped,
        "paused": info.get("ContainersPaused"),
        "total": info.get("Containers"),
        "cpus": info.get("NCPU"),
        "mem_total": info.get("MemTotal"),
        "stats": [
            stat("running", "Running", info.get("ContainersRunning"), tone="ok"),
            stat("stopped", "Stopped", stopped, tone="warn" if stopped else None),
            stat("total", "Containers", info.get("Containers")),
        ],
    }


async def list_containers(ctx: Context) -> list[dict]:
    """Every container (stopped ones too) WITH its labels and port bindings, for service discovery.
    Not part of the polled payload (`fetch` is unchanged). Row: id (12), name, image, state,
    labels {str: str}, ports [{ip, private, public, type}], network_mode."""
    raw = (await ctx.client.get(f"{_base(ctx)}/containers/json", params={"all": "1"})).raise_for_status().json()
    rows = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        labels = c.get("Labels") if isinstance(c.get("Labels"), dict) else {}
        rows.append({
            "id": str(c.get("Id") or "")[:12],
            "name": str((c.get("Names") or ["?"])[0]).lstrip("/"),
            "image": str(c.get("Image") or ""),
            "state": str(c.get("State") or ""),
            "labels": {str(k): str(v) for k, v in labels.items() if v is not None},
            "ports": [
                {"ip": p.get("IP") or "", "private": p.get("PrivatePort"), "public": p.get("PublicPort"), "type": p.get("Type") or "tcp"}
                for p in (c.get("Ports") or []) if isinstance(p, dict)
            ],
            "network_mode": str(((c.get("HostConfig") or {}).get("NetworkMode")) or ""),
        })
    return rows


async def container(ctx: Context, payload: dict) -> None:
    cid, action = str(payload.get("id") or ""), payload.get("action")
    if not re.fullmatch(_ID, cid) or action not in ACTIONS:  # validated here too: callable without run_action
        raise IntegrationError("invalid container id or action")
    url = f"{_base(ctx)}/containers/{cid}/{action}"
    (await ctx.client.post(url, timeout=30.0)).raise_for_status()


SPEC = Spec(
    type="docker",
    label="Docker",
    description="Containers and engine info from the Docker socket or TCP API, with start/stop/restart.",
    fetch=fetch,
    client_factory=_client,
    provides=("stats",),
    fields=[
        {
            "key": "url", "label": "Docker API", "type": "url", "schemes": ["http", "https", "unix"],
            "default": SOCKET,
            "placeholder": "unix:///var/run/docker.sock  or  http://192.168.1.20:2375",
            "help": "Socket path works when this backend runs on the Docker host (mount /var/run/docker.sock into the container). Otherwise expose the Docker TCP API or use a socket proxy.",
        },
        {"key": "show_all", "label": "Include stopped containers", "type": "checkbox", "default": True},
    ],
    actions={
        "container": Action(
            container,
            label="Container action",
            description="Start, stop or restart one container.",
            payload=[
                {"key": "id", "label": "Container id", "type": "text", "required": True, "pattern": _ID},
                {"key": "action", "label": "Action", "type": "select", "required": True,
                 "options": [{"value": a, "label": a} for a in ACTIONS]},
            ],
        )
    },
)
