from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/api/services", tags=["services"])


def _get(db: Session, service_id: str) -> models.Service:
    svc = db.get(models.Service, service_id)
    if not svc:
        raise HTTPException(404, "Service not found")
    return svc


def _to_model_kwargs(data: dict) -> dict:
    if "metadata" in data:
        data["metadata_"] = data.pop("metadata")
    return data


def to_out(svc: models.Service) -> schemas.ServiceOut:
    return schemas.ServiceOut(
        id=svc.id,
        name=svc.name,
        protocol=svc.protocol,
        host=svc.host,
        port=svc.port,
        path=svc.path or "",
        icon=svc.icon or "",
        color=svc.color or "",
        category=svc.category or "",
        description=svc.description or "",
        health_enabled=svc.health_enabled,
        health_type=svc.health_type or "http",
        health_url=svc.health_url or "",
        health_interval=svc.health_interval or 30,
        depends_on=svc.depends_on or [],
        metadata=svc.metadata_ or {},
        url=svc.url,
    )


@router.get("", response_model=list[schemas.ServiceOut])
def list_services(db: Session = Depends(get_db)):
    return [to_out(s) for s in db.query(models.Service).order_by(models.Service.category, models.Service.name).all()]


@router.post("", response_model=schemas.ServiceOut, status_code=201)
def create_service(payload: schemas.ServiceCreate, db: Session = Depends(get_db)):
    svc = models.Service(**_to_model_kwargs(payload.model_dump(exclude_none=True)))
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return to_out(svc)


@router.get("/{service_id}", response_model=schemas.ServiceOut)
def get_service(service_id: str, db: Session = Depends(get_db)):
    return to_out(_get(db, service_id))


@router.patch("/{service_id}", response_model=schemas.ServiceOut)
def update_service(service_id: str, payload: schemas.ServiceUpdate, db: Session = Depends(get_db)):
    svc = _get(db, service_id)
    for k, v in _to_model_kwargs(payload.model_dump(exclude_unset=True)).items():
        setattr(svc, k, v)
    db.commit()
    db.refresh(svc)
    return to_out(svc)


@router.delete("/{service_id}", status_code=204)
def delete_service(service_id: str, db: Session = Depends(get_db)):
    db.delete(_get(db, service_id))
    db.commit()
