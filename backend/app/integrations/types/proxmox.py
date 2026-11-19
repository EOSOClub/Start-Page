"""Proxmox VE: cluster overview from one call, GET /api2/json/cluster/resources, plus guest power actions.

Auth is an API token: `Authorization: PVEAPIToken=USER@REALM!TOKENID=SECRET` (no CSRF token needed).
/cluster/resources returns one row per node / qemu / lxc / storage; shared storage appears once per node
and is counted once. Templates are not counted as guests. A token with only VM.Audit / Sys.Audit (and
Datastore.Audit for storage) is enough to read; power actions need VM.PowerMgmt.
"""
import re
from typing import Any, Optional

from ..base import Action, Context, Spec, stat
from ..errors import IntegrationError

MAX_ITEMS = 50
_NODE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62})$")
_VMID = re.compile(r"^[0-9]{1,9}$")
_KINDS = ("qemu", "lxc")
_POWER = ("start", "shutdown", "reboot", "stop")
_NODE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9-]{0,62}"


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f or f in (float("inf"), float("-inf")) else f


def _pct(used: float, total: float) -> Optional[float]:
    return round(used / total * 100, 1) if total > 0 else None


def _tone(p: Optional[float]) -> Optional[str]:
    if p is None:
        return None
    return "bad" if p >= 90 else "warn" if p >= 80 else "ok"


def _bytes(n: Any) -> str:
    v = _num(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if v < 1024 or unit == "TiB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return "0 B"


def _headers(ctx: Context) -> dict:
    return {"Authorization": f"PVEAPIToken={ctx.cfg.get('token_id', '')}={ctx.cfg.get('token_secret', '')}"}


async def _resources(ctx: Context) -> list[dict]:
    resp = await ctx.client.get(ctx.url("/api2/json/cluster/resources"), headers=_headers(ctx))
    resp.raise_for_status()
    data = (resp.json() or {}).get("data")
    if not isinstance(data, list):
        raise IntegrationError("invalid response (is this a Proxmox VE API?)")
    return [r for r in data if isinstance(r, dict)]


def _guest_status(r: dict) -> str:
    s = r.get("status")
    return "online" if s == "running" else "maintenance" if s == "paused" else "offline"


def _stats(rows: list[dict]) -> list[dict]:
    nodes = [r for r in rows if r.get("type") == "node"]
    guests = [r for r in rows if r.get("type") in _KINDS and not r.get("template")]
    online = [n for n in nodes if n.get("status") == "online"]
    vms = [g for g in guests if g["type"] == "qemu"]
    lxcs = [g for g in guests if g["type"] == "lxc"]
    cores = sum(_num(n.get("maxcpu")) for n in online)
    cpu = _pct(sum(_num(n.get("cpu")) * _num(n.get("maxcpu")) for n in online), cores)
    mem = _pct(sum(_num(n.get("mem")) for n in online), sum(_num(n.get("maxmem")) for n in online))
    stores, seen = [], set()
    for s in rows:
        if s.get("type") != "storage" or s.get("status") not in (None, "available"):
            continue
        if s.get("shared"):
            if s.get("storage") in seen:
                continue
            seen.add(s.get("storage"))
        stores.append(s)
    disk = _pct(sum(_num(s.get("disk")) for s in stores), sum(_num(s.get("maxdisk")) for s in stores))

    def running(g: list[dict]) -> str:
        return f"{sum(1 for x in g if x.get('status') == 'running')}/{len(g)}"

    return [
        stat("nodes", "Nodes online", f"{len(online)}/{len(nodes)}", tone="ok" if len(online) == len(nodes) and nodes else "bad" if nodes else None),
        stat("vms", "VMs running", running(vms)),
        stat("lxcs", "LXCs running", running(lxcs)),
        stat("cpu", "CPU", cpu, "%", _tone(cpu)),
        stat("memory", "Memory", mem, "%", _tone(mem)),
        stat("storage", "Storage", disk, "%", _tone(disk)),
    ]


def _items(rows: list[dict], which: str, node: Optional[str]) -> list[dict]:
    if node:
        rows = [r for r in rows if r.get("node") == node]
    out: list[dict] = []
    if which == "nodes":
        for n in sorted((r for r in rows if r.get("type") == "node"), key=lambda r: str(r.get("node"))):
            up = n.get("status") == "online"
            cpu = round(_num(n.get("cpu")) * 100, 1)
            out.append({
                "title": str(n.get("node") or n.get("id") or "node"),
                "subtitle": f"{int(_num(n.get('maxcpu')))} cores",
                "status": "online" if up else "offline",
                "progress": min(cpu, 100.0) if up else None,
                "meta": f"{_bytes(n.get('mem'))} / {_bytes(n.get('maxmem'))}" if up else None,
            })
    elif which == "storage":
        for s in sorted((r for r in rows if r.get("type") == "storage"), key=lambda r: (str(r.get("storage")), str(r.get("node")))):
            used = _pct(_num(s.get("disk")), _num(s.get("maxdisk")))
            out.append({
                "title": str(s.get("storage") or "storage"),
                "subtitle": str(s.get("node") or ""),
                "status": "online" if s.get("status") in (None, "available") else "offline",
                "progress": min(used, 100.0) if used is not None else None,
                "meta": f"{_bytes(s.get('disk'))} / {_bytes(s.get('maxdisk'))}",
            })
    else:
        guests = [r for r in rows if r.get("type") in _KINDS and not r.get("template")]
        for g in sorted(guests, key=lambda r: _num(r.get("vmid"))):
            running = g.get("status") == "running"
            cpu = round(_num(g.get("cpu")) * 100, 1)
            item = {
                "title": str(g.get("name") or f"{g['type']} {g.get('vmid')}"),
                "subtitle": f"{g.get('node') or '?'} · {'VM' if g['type'] == 'qemu' else 'LXC'} {g.get('vmid')}",
                "status": _guest_status(g),
                "progress": min(cpu, 100.0) if running else None,
                "meta": f"{_bytes(g.get('mem'))} / {_bytes(g.get('maxmem'))}" if running else None,
            }
            node, vmid = g.get("node"), g.get("vmid")
            if isinstance(node, str) and _NODE.fullmatch(node) and not isinstance(vmid, bool) and _VMID.fullmatch(str(vmid)):
                item["ref"] = {"node": node, "vmid": str(vmid), "kind": g["type"]}  # exactly what guest_power takes (vmid is a text field)
            out.append(item)
    return out[:MAX_ITEMS]


async def fetch(ctx: Context) -> dict:
    rows = await _resources(ctx)
    which = ctx.cfg.get("list") or "guests"
    return {"stats": _stats(rows), "items": _items(rows, which, ctx.cfg.get("node") or None)}


async def guest_power(ctx: Context, payload: dict) -> None:
    node, vmid = str(payload.get("node", "")), payload.get("vmid")
    if isinstance(vmid, bool) or not isinstance(vmid, (str, int)):
        raise IntegrationError("invalid VM id")
    vmid = str(vmid)
    kind, action = payload.get("kind"), payload.get("action")
    if not _NODE.fullmatch(node):
        raise IntegrationError("invalid node name")
    if not _VMID.fullmatch(vmid):
        raise IntegrationError("invalid VM id")
    if kind not in _KINDS:
        raise IntegrationError("kind must be qemu or lxc")
    if action not in _POWER:
        raise IntegrationError("action must be start, shutdown, reboot or stop")
    resp = await ctx.client.post(ctx.url(f"/api2/json/nodes/{node}/{kind}/{vmid}/status/{action}"), headers=_headers(ctx))
    resp.raise_for_status()


SPEC = Spec(
    type="proxmox",
    label="Proxmox VE",
    description="Proxmox VE cluster: nodes, VMs, containers and storage, with guest power actions (API token).",
    fetch=fetch,
    default_interval=30,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "Proxmox URL", "type": "url", "required": True, "placeholder": "https://192.168.1.10:8006"},
        {"key": "token_id", "label": "API token ID", "type": "text", "required": True, "placeholder": "root@pam!startpage",
         "help": "USER@REALM!TOKENID. A token with only VM.Audit / Sys.Audit (and Datastore.Audit) is enough for read-only use; power actions need VM.PowerMgmt."},
        {"key": "token_secret", "label": "API token secret", "type": "secret", "required": True},
        {"key": "list", "label": "List shows", "type": "select", "default": "guests",
         "options": [{"value": "guests", "label": "VMs and containers"}, {"value": "nodes", "label": "Nodes"},
                     {"value": "storage", "label": "Storage"}]},
        {"key": "node", "label": "Only node (optional)", "type": "text", "pattern": _NODE_PATTERN,
         "help": "Limits the list to one node. The stats always cover the whole cluster."},
    ],
    actions={
        "guest_power": Action(
            guest_power,
            label="Guest power",
            description="Start, shut down, reboot or stop a VM or container.",
            payload=[
                {"key": "node", "label": "Node", "type": "text", "required": True, "pattern": _NODE_PATTERN},
                {"key": "vmid", "label": "VM id", "type": "text", "required": True, "pattern": r"[0-9]{1,9}"},
                {"key": "kind", "label": "Kind", "type": "select", "required": True,
                 "options": [{"value": "qemu", "label": "VM"}, {"value": "lxc", "label": "Container"}]},
                {"key": "action", "label": "Action", "type": "select", "required": True,
                 "options": [{"value": a, "label": a.capitalize()} for a in _POWER]},
            ],
        )
    },
)
