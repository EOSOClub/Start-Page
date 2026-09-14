from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/api/widgets", tags=["widgets"])


def _get(db: Session, widget_id: str) -> models.Widget:
    widget = db.get(models.Widget, widget_id)
    if not widget:
        raise HTTPException(404, "Widget not found")
    return widget


@router.patch("/{widget_id}", response_model=schemas.WidgetOut)
def update_widget(widget_id: str, payload: schemas.WidgetUpdate, db: Session = Depends(get_db)):
    widget = _get(db, widget_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("dashboard_id") and not db.get(models.Dashboard, data["dashboard_id"]):
        raise HTTPException(404, "Dashboard not found")
    if "dashboard_id" in data and not data["dashboard_id"]:
        data.pop("dashboard_id")
    for k, v in data.items():
        setattr(widget, k, v)
    db.commit()
    db.refresh(widget)
    return widget


@router.post("/{widget_id}/duplicate", response_model=schemas.WidgetOut, status_code=201)
def duplicate_widget(widget_id: str, db: Session = Depends(get_db)):
    src = _get(db, widget_id)
    clone = models.Widget(
        dashboard_id=src.dashboard_id,
        type=src.type,
        x=src.x + 1,
        y=src.y + 1,
        w=src.w,
        h=src.h,
        service_id=src.service_id,
        config=dict(src.config or {}),
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return clone


@router.delete("/{widget_id}", status_code=204)
def delete_widget(widget_id: str, db: Session = Depends(get_db)):
    db.delete(_get(db, widget_id))
    db.commit()
