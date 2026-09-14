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
    return backups.create_backup(safe)


@router.get("/{name}", response_model=schemas.ExportBundle)
def get_backup(name: str):
    try:
        return backups.read_backup(name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")


@router.post("/{name}/restore", response_model=schemas.ExportBundle)
def restore_backup(name: str, replace: bool = Query(True), db: Session = Depends(get_db)):
    try:
        bundle = schemas.ExportBundle(**backups.read_backup(name))
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
    backups.create_backup("pre-restore")
    return import_config(bundle, db, replace)


@router.delete("/{name}", status_code=204)
def delete_backup(name: str):
    try:
        backups.delete_backup(name)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found")
