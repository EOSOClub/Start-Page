"""Whole-configuration import/export as a single JSON bundle."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..integrations import TYPES as integration_types
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
    # Validate before the first write, so a bad bundle changes nothing.
    unknown = sorted({i.type for i in bundle.integrations} - set(integration_types))
    if unknown:
        raise HTTPException(422, f"Unknown integration type(s) {unknown}; valid: {list(integration_types)}")

    widget_ids: set[str] = set()  # ids this import has already used
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
            # A widget keeps its id when that id is free, so undo steps recorded before a
            # page was deleted still address the right widget once the page is restored.
            # An id that is still live elsewhere (a widget moved between pages in the
            # bundle) or repeated inside the bundle gets a fresh one instead: Widget.id
            # is a leaf key (nothing FKs it), so that is invisible to the user and a
            # same-id collision stays impossible. This page's own old widgets were
            # deleted and flushed above, so their ids count as free.
            wid = wd.get("id")
            if not wid or wid in widget_ids or db.get(models.Widget, wid):
                wd.pop("id", None)
            else:
                widget_ids.add(wid)
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
