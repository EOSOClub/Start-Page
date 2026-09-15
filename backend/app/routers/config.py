"""Whole-configuration import/export as a single JSON bundle."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from .services import to_out as service_out
from .settings import load as load_settings, save as save_settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/export", response_model=schemas.ExportBundle)
def export_config(db: Session = Depends(get_db)):
    services = [service_out(s) for s in db.query(models.Service).all()]
    dashboards = db.query(models.Dashboard).order_by(models.Dashboard.position).all()
    integrations = db.query(models.Integration).all()
    return schemas.ExportBundle(
        services=services, dashboards=dashboards, integrations=integrations, settings=load_settings(db)
    )


@router.post("/import", response_model=schemas.ExportBundle)
def import_config(
    bundle: schemas.ExportBundle,
    db: Session = Depends(get_db),
    replace: bool = Query(False, description="Wipe existing config before importing"),
):
    if replace:
        db.query(models.Widget).delete()
        db.query(models.Dashboard).delete()
        db.query(models.Service).delete()
        db.query(models.Integration).delete()
        db.flush()

    for s in bundle.services:
        data = s.model_dump(exclude={"url"})
        data["metadata_"] = data.pop("metadata", {})
        existing = db.get(models.Service, s.id)
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            db.add(models.Service(**data))
    db.flush()

    for d in bundle.dashboards:
        data = d.model_dump(exclude={"widgets"})
        dash = db.get(models.Dashboard, d.id)
        if dash:
            for k, v in data.items():
                setattr(dash, k, v)
            for w in list(dash.widgets):
                db.delete(w)
        else:
            dash = models.Dashboard(**data)
            db.add(dash)
        db.flush()
        for w in d.widgets:
            wd = w.model_dump()
            # Widget.id is a leaf key (nothing FKs it) and the frontend refetches
            # everything after import, so re-inserting a moved widget with a fresh
            # id is invisible to the user — and makes the same-id collision with a
            # live widget impossible by construction (imported widget ids regenerate;
            # every other entity keeps the documented "same id = updated" contract).
            wd.pop("id", None)
            wd["dashboard_id"] = dash.id
            if wd.get("service_id") and not db.get(models.Service, wd["service_id"]):
                wd["service_id"] = None
            db.add(models.Widget(**wd))
    for i in bundle.integrations:
        data = i.model_dump()
        existing = db.get(models.Integration, i.id)
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            db.add(models.Integration(**data))
    if bundle.settings:
        save_settings(db, bundle.settings)
    db.commit()
    return export_config(db)
