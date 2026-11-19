from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..validation import check_urls

router = APIRouter(prefix="/api/widgets", tags=["widgets"])


def _get(db: Session, widget_id: str) -> models.Widget:
    widget = db.get(models.Widget, widget_id)
    if not widget:
        raise HTTPException(404, "Widget not found")
    return widget


def check_config(widget_type: str, config: dict) -> None:
    """422 when a written config holds a dangerous URL (javascript:, vbscript:, non-image data:).
    App schemes such as obsidian:// or ssh:// are fine here; page files are stricter (see pages.py)."""
    try:
        check_urls(config, "config", widget_type, strict=False)
    except ValueError as e:
        raise HTTPException(422, [{"loc": ["body", "config"], "msg": str(e)}]) from None


def check_widget_layout(dash: models.Dashboard, x, w, layout_sm, check_desktop: bool = True) -> None:
    """422 (per-field messages) when a written placement does not fit the page: the desktop x + w within
    `columns` (only checked when x or w is being written, so an old overflowing row stays editable) and
    the phone x + w within `columns_sm`. Finite numbers and the caps are checked by the schema."""
    problems = []
    if check_desktop and x is not None and w is not None:
        why = schemas.fit_error(x, w, dash.columns or 24, "page")
        if why:
            problems.append({"loc": ["body", "x"], "msg": why})
    if layout_sm is not None:
        why = schemas.fit_error(layout_sm.x, layout_sm.w, schemas.phone_columns(dash.settings))
        if why:
            problems.append({"loc": ["body", "layout_sm"], "msg": why})
    if problems:
        raise HTTPException(422, problems)


@router.patch("/{widget_id}", response_model=schemas.WidgetOut)
def update_widget(widget_id: str, payload: schemas.WidgetUpdate, db: Session = Depends(get_db)):
    widget = _get(db, widget_id)
    data = schemas.patch_data(payload, nullable=("service_id", "layout_sm"))
    if data.get("config") is not None:
        check_config(data.get("type") or widget.type, data["config"])
    target = db.get(models.Dashboard, data["dashboard_id"]) if data.get("dashboard_id") else widget.dashboard
    if data.get("dashboard_id") and not target:
        raise HTTPException(404, "Dashboard not found")
    touched = {"x", "w", "dashboard_id"} & set(data)
    check_widget_layout(
        target, data.get("x", widget.x), data.get("w", widget.w), payload.layout_sm if data.get("layout_sm") else None,
        check_desktop=bool({"x", "w"} & set(data)),
    )
    if "dashboard_id" in data and not data["dashboard_id"]:
        data.pop("dashboard_id")
    if "service_id" in data and not data["service_id"]:
        data["service_id"] = None  # "" unlinks too, instead of failing the foreign key
    for k, v in data.items():
        setattr(widget, k, v)
    db.commit()
    db.refresh(widget)
    return widget


def _duplicate_phone(layout_sm) -> dict | None:
    """The copy's phone placement: below the original. A stored placement that is not valid is not copied."""
    safe = schemas.sanitize_layout_sm(layout_sm)
    if not safe:
        return None
    return {**safe, "y": min(safe["y"] + safe["h"], schemas.Y_MAX)}


@router.post("/{widget_id}/duplicate", response_model=schemas.WidgetOut, status_code=201)
def duplicate_widget(widget_id: str, db: Session = Depends(get_db)):
    src = _get(db, widget_id)
    clone = models.Widget(
        dashboard_id=src.dashboard_id,
        type=src.type,
        x=min((src.x if isinstance(src.x, (int, float)) and src.x == src.x else 0) + 1, schemas.X_MAX),
        y=min((src.y if isinstance(src.y, (int, float)) and src.y == src.y else 0) + 1, schemas.Y_MAX),
        w=src.w if isinstance(src.w, (int, float)) and src.w == src.w and src.w > 0 else 4,
        h=src.h if isinstance(src.h, (int, float)) and src.h == src.h and src.h > 0 else 3,
        service_id=src.service_id,
        config=dict(src.config or {}),
        # Same size on the phone, directly below the original (the desktop copy is offset one cell).
        layout_sm=_duplicate_phone(src.layout_sm),
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return clone


@router.delete("/{widget_id}", status_code=204)
def delete_widget(widget_id: str, db: Session = Depends(get_db)):
    db.delete(_get(db, widget_id))
    db.commit()
