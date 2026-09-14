from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

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


@router.get("/{dashboard_id}", response_model=schemas.DashboardDetail)
def get_dashboard(dashboard_id: str, db: Session = Depends(get_db)):
    return _get(db, dashboard_id)


@router.patch("/{dashboard_id}", response_model=schemas.DashboardOut)
def update_dashboard(dashboard_id: str, payload: schemas.DashboardUpdate, db: Session = Depends(get_db)):
    dash = _get(db, dashboard_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
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
def save_layout(dashboard_id: str, items: list[schemas.LayoutItem], db: Session = Depends(get_db)):
    """Bulk-save positions/sizes after a drag or resize."""
    dash = _get(db, dashboard_id)
    by_id = {w.id: w for w in dash.widgets}
    for item in items:
        widget = by_id.get(item.id)
        if widget:
            widget.x, widget.y, widget.w, widget.h = item.x, item.y, item.w, item.h
    db.commit()
    db.refresh(dash)
    return dash.widgets


@router.post("/{dashboard_id}/widgets", response_model=schemas.WidgetOut, status_code=201)
def create_widget(dashboard_id: str, payload: schemas.WidgetCreate, db: Session = Depends(get_db)):
    _get(db, dashboard_id)
    widget = models.Widget(dashboard_id=dashboard_id, **payload.model_dump(exclude_none=True))
    db.add(widget)
    db.commit()
    db.refresh(widget)
    return widget
