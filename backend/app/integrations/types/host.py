"""This machine's CPU, memory and disk (psutil). No configuration, no network."""
import asyncio
import os
import socket

from ..base import Context, Spec, stat


def _tone(pct):
    return None if pct is None else "bad" if pct >= 90 else "warn" if pct >= 75 else "ok"


def _read_system(psutil):
    """Sync helper: runs in a thread via asyncio.to_thread."""
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(os.path.abspath(os.sep))
    cpu = psutil.cpu_percent(None)
    return {
        "hostname": socket.gethostname(),
        "cpu_percent": cpu,
        "mem_percent": vm.percent, "mem_used": vm.used, "mem_total": vm.total,
        "disk_percent": du.percent, "disk_used": du.used, "disk_total": du.total,
        "stats": [
            stat("cpu", "CPU", cpu, "%", _tone(cpu)),
            stat("mem", "RAM", vm.percent, "%", _tone(vm.percent)),
            stat("disk", "Disk", du.percent, "%", _tone(du.percent)),
        ],
    }


async def fetch(ctx: Context):
    import psutil  # lazy: missing dep = error tile, not backend outage

    return await asyncio.to_thread(_read_system, psutil)


SPEC = Spec(
    type="host",
    label="System",
    description="CPU, memory and disk of the machine this backend runs on; no configuration.",
    fetch=fetch,
    uses_http=False,
    provides=("stats",),
)
