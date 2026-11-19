from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import health, models, schemas
from ..database import get_db

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=dict[str, schemas.HealthStatus])
def all_status():
    return health.get_all()


@router.get("/history", response_model=list[schemas.HealthHistory])
def get_history(hours: int = 24, buckets: int = 48, db: Session = Depends(get_db)):
    return health.history(db, hours, buckets)


@router.get("/events", response_model=list[schemas.HealthEventOut])
def get_events(limit: int = 50, service_id: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(models.HealthEvent, models.Service.name).join(
        models.Service, models.Service.id == models.HealthEvent.service_id
    )
    if service_id:
        q = q.filter(models.HealthEvent.service_id == service_id)
    rows = q.order_by(models.HealthEvent.at.desc(), models.HealthEvent.id.desc()).limit(max(1, min(limit, 500))).all()
    return [
        schemas.HealthEventOut(
            id=e.id,
            service_id=e.service_id,
            service_name=name,
            at=health._to_ts(e.at),
            from_status=e.from_status,
            to_status=e.to_status,
            error=e.error,
        )
        for e, name in rows
    ]


@router.post("/{service_id}/check", response_model=schemas.HealthStatus)
async def check(service_id: str):
    result = await health.check_now(service_id)
    if result is None:
        raise HTTPException(404, "Service not found")
    return result
