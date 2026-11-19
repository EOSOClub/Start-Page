"""Unraid via the official Unraid API (GraphQL at /graphql, header `x-api-key`): Unraid 7.2+ or the
Unraid Connect plugin on older 7.x.

Five small queries run in parallel so one unsupported field only drops its own stats:
  array   {array {state capacity{kilobytes{free used total}} disks{...} parities{...} caches{...}}}  (required)
  parity  {array {parityCheckStatus {status running progress errors}}}                              (optional)
  docker  {docker {containers {id names state status image}}}                                       (optional)
  vms     {vms {domains {id name state}}}                                                           (optional)
  metrics {metrics {cpu {percentTotal} memory {percentTotal}}}                                      (optional)
A GraphQL `errors` array in a 200 answer counts as a failed query; its text (which can echo the query)
is never shown. The list the user picked must have its query succeed, otherwise the fetch fails.
Sizes (disk size, fsSize, fsUsed, capacity.kilobytes) are treated as KiB; they may arrive as strings.
"""
import asyncio
from typing import Any, Optional

import httpx

from ..base import Context, Spec, stat
from ..errors import AuthFailed, IntegrationError

MAX_ITEMS = 50

Q_ARRAY = ("{ array { state capacity { kilobytes { free used total } } "
           "disks { id name device status temp numErrors size fsSize fsUsed type } "
           "parities { id name device status temp numErrors size type } "
           "caches { id name device status temp numErrors size fsSize fsUsed type } } }")
Q_PARITY = "{ array { parityCheckStatus { status running progress errors } } }"
Q_DOCKER = "{ docker { containers { id names state status image } } }"
Q_VMS = "{ vms { domains { id name state } } }"
Q_METRICS = "{ metrics { cpu { percentTotal } memory { percentTotal } } }"


class _QueryFailed(Exception):
    pass


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _pct(used: Any, total: Any) -> Optional[float]:
    u, t = _num(used), _num(total)
    return round(u / t * 100, 1) if u is not None and t and t > 0 else None


def _tone(p: Optional[float]) -> Optional[str]:
    if p is None:
        return None
    return "bad" if p >= 90 else "warn" if p >= 80 else "ok"


def _kib(n: Any) -> str:
    v = _num(n)
    if v is None:
        return "?"
    v *= 1024
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if v < 1024 or unit == "PiB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return "?"


async def _gql(ctx: Context, query: str) -> dict:
    resp = await ctx.client.post(ctx.url("/graphql"), json={"query": query},
                                 headers={"x-api-key": str(ctx.cfg.get("api_key", ""))})
    if resp.status_code in (401, 403):
        raise AuthFailed("login failed: check the Unraid API key")
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise _QueryFailed()
    errors = body.get("errors")
    if errors:
        codes = {str(((e or {}).get("extensions") or {}).get("code", "")).upper() for e in errors if isinstance(e, dict)}
        if codes & {"UNAUTHENTICATED", "UNAUTHORIZED", "FORBIDDEN"}:
            raise IntegrationError("login failed: check the Unraid API key and its permissions")
        raise _QueryFailed()
    data = body.get("data")
    if not isinstance(data, dict):
        raise _QueryFailed()
    return data


def _disk_status(d: dict) -> Optional[str]:
    s = str(d.get("status") or "").upper()
    if "NP" in s.split("_") and "DSBL" not in s:
        return None  # empty slot: not shown
    if s in ("", "UNKNOWN"):
        return "unknown"
    if s.endswith("_OK") or s == "OK":
        return "degraded" if (_num(d.get("numErrors")) or 0) > 0 else "online"
    if "NEW" in s and "DSBL" not in s:
        return "degraded"
    return "offline"


def _disks(arr: dict) -> list[tuple[str, dict, str]]:
    out = []
    for group, label in (("parities", "Parity"), ("disks", "Data"), ("caches", "Cache")):
        for d in arr.get(group) or []:
            if isinstance(d, dict):
                st = _disk_status(d)
                if st is not None:
                    out.append((label, d, st))
    return out


def _vm_status(state: str) -> str:
    s = state.lower()
    return "online" if s in ("running", "idle") else "maintenance" if s in ("paused", "pmsuspended") else "degraded" if s == "crashed" else "offline"


def _container_status(state: str) -> str:
    s = state.lower()
    return "online" if s == "running" else "maintenance" if s == "paused" else "degraded" if s == "restarting" else "offline"


async def fetch(ctx: Context) -> dict:
    queries = {"array": Q_ARRAY, "parity": Q_PARITY, "docker": Q_DOCKER, "vms": Q_VMS, "metrics": Q_METRICS}
    results = await asyncio.gather(*(_gql(ctx, q) for q in queries.values()), return_exceptions=True)
    got: dict[str, Optional[dict]] = {}
    for name, res in zip(queries, results):
        if isinstance(res, IntegrationError):
            raise res  # bad key: surface it whichever query noticed
        if isinstance(res, httpx.HTTPError) and name == "array":
            raise res  # status errors and connection failures: the framework words them safely
        if isinstance(res, BaseException) and not isinstance(res, _QueryFailed) and name == "array":
            raise IntegrationError("invalid response (is this the Unraid API? needs Unraid 7.2+ or Unraid Connect)")
        got[name] = None if isinstance(res, BaseException) else res
    if got["array"] is None:
        raise IntegrationError("Unraid API returned an error (check the API key permissions and the Unraid version)")
    which = ctx.cfg.get("list") or "disks"
    if which in ("containers", "vms") and got["docker" if which == "containers" else "vms"] is None:
        raise IntegrationError(f"Unraid API could not list {which} (is that service enabled? needs Unraid 7.2+)")

    arr = got["array"].get("array") or {}
    state = str(arr.get("state") or "").upper()
    disks = _disks(arr)
    kb = (arr.get("capacity") or {}).get("kilobytes") or {}
    used = _pct(kb.get("used"), kb.get("total"))
    bad_disks = sum(1 for _, d, st in disks if st != "online" or (_num(d.get("numErrors")) or 0) > 0)
    stats = [
        stat("array", "Array", state.replace("_", " ").capitalize() or "Unknown",
             tone="ok" if state == "STARTED" else "warn" if state in ("STOPPED", "NEW_ARRAY") else "bad"),
        stat("array_used", "Array used", used, "%", _tone(used)),
    ]
    pc = (((got["parity"] or {}).get("array") or {}).get("parityCheckStatus")) if got["parity"] else None
    if isinstance(pc, dict):
        pst = str(pc.get("status") or "").upper()
        errs = _num(pc.get("errors")) or 0
        if pc.get("running"):
            value, tone = f"Running {round(_num(pc.get('progress')) or 0)}%", "info"
        else:
            value, tone = pst.replace("_", " ").capitalize() or "Idle", "ok"
        if errs > 0 or pst in ("FAILED", "ERROR"):
            tone = "bad"
        stats.append(stat("parity", "Parity", value, tone=tone))
    stats.append(stat("disk_errors", "Disks with errors", f"{bad_disks}/{len(disks)}", tone="bad" if bad_disks else "ok"))

    containers = ((got["docker"] or {}).get("docker") or {}).get("containers") if got["docker"] else None
    containers = [c for c in containers if isinstance(c, dict)] if isinstance(containers, list) else None
    if containers is not None:
        run = sum(1 for c in containers if str(c.get("state") or "").lower() == "running")
        stats.append(stat("containers", "Containers running", f"{run}/{len(containers)}"))
    domains = ((got["vms"] or {}).get("vms") or {}).get("domains") if got["vms"] else None
    domains = [v for v in domains if isinstance(v, dict)] if isinstance(domains, list) else None
    if domains is not None:
        run = sum(1 for v in domains if _vm_status(str(v.get("state") or "")) == "online")
        stats.append(stat("vms", "VMs running", f"{run}/{len(domains)}"))
    m = (got["metrics"] or {}).get("metrics") if got["metrics"] else None
    if isinstance(m, dict):
        cpu = _num((m.get("cpu") or {}).get("percentTotal"))
        mem = _num((m.get("memory") or {}).get("percentTotal"))
        if cpu is not None:
            stats.append(stat("cpu", "CPU", round(cpu, 1), "%", _tone(cpu)))
        if mem is not None:
            stats.append(stat("memory", "Memory", round(mem, 1), "%", _tone(mem)))

    items: list[dict] = []
    if which == "containers":
        for c in containers or []:
            names = c.get("names") if isinstance(c.get("names"), list) else []
            items.append({
                "title": str(names[0]).lstrip("/") if names else str(c.get("id") or "")[:12],
                "subtitle": str(c.get("image") or "") or None,
                "status": _container_status(str(c.get("state") or "")),
                "meta": str(c.get("status") or "") or None,
            })
        items.sort(key=lambda i: (i["status"] != "online", i["title"].lower()))
    elif which == "vms":
        for v in domains or []:
            state_v = str(v.get("state") or "")
            items.append({"title": str(v.get("name") or v.get("id") or "VM"), "subtitle": state_v.capitalize() or None,
                          "status": _vm_status(state_v)})
        items.sort(key=lambda i: (i["status"] != "online", i["title"].lower()))
    else:
        for label, d, st in disks:
            temp = _num(d.get("temp"))
            meta = [f"{round(temp)} °C"] if temp is not None else []
            if _num(d.get("size")):
                meta.append(_kib(d.get("size")))
            fs = _pct(d.get("fsUsed"), d.get("fsSize"))
            items.append({
                "title": str(d.get("name") or d.get("device") or "disk"),
                "subtitle": f"{label} · {d['device']}" if d.get("device") else label,
                "status": st,
                "progress": min(fs, 100.0) if fs is not None else None,
                "meta": " · ".join(meta) or None,
            })
    return {"stats": stats, "items": items[:MAX_ITEMS]}


SPEC = Spec(
    type="unraid",
    label="Unraid",
    description="Unraid array, disks, Docker and VMs via the Unraid GraphQL API (Unraid 7.2+ or Unraid Connect).",
    fetch=fetch,
    default_interval=60,
    provides=("stats", "items"),
    fields=[
        {"key": "url", "label": "Unraid URL", "type": "url", "required": True, "placeholder": "http://tower.local"},
        {"key": "api_key", "label": "API key", "type": "secret", "required": True,
         "help": "Settings -> Management Access -> API Keys (Unraid 7.2+, or the Unraid Connect plugin on older 7.x). A read-only key (Viewer role) is enough."},
        {"key": "list", "label": "List shows", "type": "select", "default": "disks",
         "options": [{"value": "disks", "label": "Disks"}, {"value": "containers", "label": "Docker containers"},
                     {"value": "vms", "label": "Virtual machines"}]},
    ],
)
