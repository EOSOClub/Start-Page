from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from .widgets import check_config, check_widget_layout

router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])


def _get(db: Session, dashboard_id: str) -> models.Dashboard:
    dash = db.get(models.Dashboard, dashboard_id)
    if not dash:
        raise HTTPException(404, "Dashboard not found")
    return dash


@router.get("", response_model=list[schemas.DashboardOut])
def list_dashboards(db: Session = Depends(get_db)):
    return db.query(models.Dashboard).order_by(models.Dashboard.position, models.Dashboard.name).all()


@router.post("", response_model=schemas.DashboardOut, status_code=201)
def create_dashboard(payload: schemas.DashboardCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude_none=True)
    if "position" not in payload.model_fields_set:
        data["position"] = db.query(models.Dashboard).count()
    dash = models.Dashboard(**data)
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return dash


@router.put("/order", response_model=list[schemas.DashboardOut])
def reorder_dashboards(ids: list[str], db: Session = Depends(get_db)):
    """Set every listed page's position to its index, in one transaction (all or nothing)."""
    by_id = {d.id: d for d in db.query(models.Dashboard).all()}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise HTTPException(404, f"Dashboard not found: {missing}")
    for position, dashboard_id in enumerate(ids):
        by_id[dashboard_id].position = position
    db.commit()
    return list_dashboards(db)


@router.get("/{dashboard_id}", response_model=schemas.DashboardDetail)
def get_dashboard(dashboard_id: str, db: Session = Depends(get_db)):
    return _get(db, dashboard_id)


@router.patch("/{dashboard_id}", response_model=schemas.DashboardOut)
def update_dashboard(dashboard_id: str, payload: schemas.DashboardUpdate, db: Session = Depends(get_db)):
    dash = _get(db, dashboard_id)
    for k, v in schemas.patch_data(payload).items():
        setattr(dash, k, v)
    db.commit()
    db.refresh(dash)
    return dash


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: str, db: Session = Depends(get_db)):
    dash = _get(db, dashboard_id)
    db.delete(dash)
    db.commit()


@router.put("/{dashboard_id}/layout", response_model=list[schemas.WidgetOut])
def save_layout(
    dashboard_id: str,
    items: list[schemas.LayoutItem],
    breakpoint: Literal["lg", "sm"] = Query("lg", description="lg = desktop grid (default), sm = phone grid"),
    db: Session = Depends(get_db),
):
    """Bulk-save positions/sizes after a drag or resize.

    `breakpoint=sm` writes each widget's phone placement (`layout_sm`) instead; x + w must fit the
    page's `columns_sm`, and `hidden` (when sent) sets "hide on phone". Without the parameter this is
    the desktop call and `hidden` is ignored."""
    dash = _get(db, dashboard_id)
    by_id = {w.id: w for w in dash.widgets}
    if breakpoint == "sm":
        cols = schemas.phone_columns(dash.settings)
        problems = []
        for n, item in enumerate(items):  # validate everything before the first write
            why = schemas.fit_error(item.x, item.w, cols)
            if why:
                problems.append({"loc": ["body", n], "msg": f"Widget {item.id[:40]}: {why}"})
        if problems:
            raise HTTPException(422, problems)
    for item in items:
        widget = by_id.get(item.id)
        if not widget:
            continue
        if breakpoint == "sm":
            hidden = item.hidden if item.hidden is not None else bool((widget.layout_sm or {}).get("hidden"))
            widget.layout_sm = {"x": item.x, "y": item.y, "w": item.w, "h": item.h, "hidden": hidden}
        else:
            widget.x, widget.y, widget.w, widget.h = item.x, item.y, item.w, item.h
    db.commit()
    db.refresh(dash)
    return dash.widgets


@router.delete("/{dashboard_id}/layout", status_code=204)
def clear_phone_layout(
    dashboard_id: str,
    breakpoint: Literal["lg", "sm"] = Query(..., description="Only sm can be cleared"),
    db: Session = Depends(get_db),
):
    """Remove every widget's phone placement on this page (they are appended automatically again)."""
    if breakpoint != "sm":
        raise HTTPException(422, "Only the phone layout (breakpoint=sm) can be cleared")
    dash = _get(db, dashboard_id)
    for widget in dash.widgets:
        widget.layout_sm = None
    db.commit()


@router.post("/{dashboard_id}/widgets", response_model=schemas.WidgetOut, status_code=201)
def create_widget(dashboard_id: str, payload: schemas.WidgetCreate, db: Session = Depends(get_db)):
    dash = _get(db, dashboard_id)
    check_config(payload.type, payload.config)
    check_widget_layout(dash, payload.x, payload.w, payload.layout_sm, "x" in payload.model_fields_set or "w" in payload.model_fields_set)
    widget = models.Widget(dashboard_id=dashboard_id, **payload.model_dump(exclude_none=True))
    db.add(widget)
    db.commit()
    db.refresh(widget)
    return widget
