"""Automatic JSON backups of the whole configuration into DATA_DIR/backups."""
import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from .database import DATA_DIR, SessionLocal

log = logging.getLogger("startpage.backups")
BACKUP_DIR: Path = DATA_DIR / "backups"
_task: asyncio.Task | None = None


def _export_bundle() -> dict:
    from .routers.config import export_config  # local import to avoid cycle

    with SessionLocal() as db:
        return export_config(db).model_dump()


def list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(BACKUP_DIR.glob("*.json"), reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "modified": st.st_mtime})
    return out


def create_backup(label: str = "auto") -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"{stamp}-{label}.json"
    path.write_text(json.dumps(_export_bundle(), indent=2), encoding="utf-8")
    log.info("backup written: %s", path.name)
    return {"name": path.name, "size": path.stat().st_size, "modified": path.stat().st_mtime}


def read_backup(name: str) -> dict:
    path = (BACKUP_DIR / name).resolve()
    if path.parent != BACKUP_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(name)
    return json.loads(path.read_text(encoding="utf-8"))


def delete_backup(name: str) -> None:
    path = (BACKUP_DIR / name).resolve()
    if path.parent != BACKUP_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(name)
    path.unlink()


def prune(keep: int) -> None:
    autos = [p for p in sorted(BACKUP_DIR.glob("*-auto.json"), reverse=True)]
    for p in autos[max(keep, 1):]:
        p.unlink(missing_ok=True)


def _settings() -> dict:
    from .routers.settings import load

    with SessionLocal() as db:
        return load(db)


async def _loop():
    # One backup shortly after startup (if the last auto backup is older than a day), then daily.
    while True:
        try:
            cfg = _settings()
            if cfg.get("backup_enabled", True):
                autos = sorted(BACKUP_DIR.glob("*-auto.json")) if BACKUP_DIR.exists() else []
                last = autos[-1].stat().st_mtime if autos else 0
                if time.time() - last > 23 * 3600:
                    create_backup("auto")
                    prune(int(cfg.get("backup_keep", 14)))
        except Exception:  # noqa: BLE001
            log.exception("backup loop error")
        await asyncio.sleep(3600)


def start():
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())


def stop():
    global _task
    if _task:
        _task.cancel()
        _task = None
