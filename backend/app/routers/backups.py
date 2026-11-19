from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import backups, schemas
from ..database import get_db
from .config import import_config

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
def list_backups():
    return backups.list_backups()


@router.post("", status_code=201)
def create_backup(label: str = Query("manual")):
    safe = "".join(ch for ch in label if ch.isalnum() or ch in "-_")[:40] or "manual"
    if safe == "auto" or safe.endswith("-auto"):
        safe += "-manual"  # "*-auto.json" is the scheduler's namespace: pruned, and it delays the next auto backup
    return backups.create_backup(safe)


def _read_bundle(name: str) -> schemas.ExportBundle:
    try:
        return schemas.ExportBundle.model_validate(backups.read_backup(name))
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
    except ValueError:  # bad JSON, bad encoding, or not a bundle (pydantic's ValidationError is a ValueError)
        raise HTTPException(422, "Backup file is not a valid configuration bundle")


@router.get("/{name}", response_model=schemas.ExportBundle)
def get_backup(name: str):
    return _read_bundle(name)


@router.post("/{name}/restore", response_model=schemas.ExportBundle)
def restore_backup(name: str, replace: bool = Query(True), db: Session = Depends(get_db)):
    bundle = _read_bundle(name)
    backups.create_backup("pre-restore")
    return import_config(bundle, db, replace)


@router.delete("/{name}", status_code=204)
def delete_backup(name: str):
    try:
        backups.delete_backup(name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
