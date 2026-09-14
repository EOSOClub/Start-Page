from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import integrations, models, schemas
from ..database import get_db

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def _get(db: Session, integration_id: str) -> models.Integration:
    integ = db.get(models.Integration, integration_id)
    if not integ:
        raise HTTPException(404, "Integration not found")
    return integ


@router.get("/types")
def list_types():
    return integrations.TYPES


@router.get("", response_model=list[schemas.IntegrationOut])
def list_integrations(db: Session = Depends(get_db)):
    return db.query(models.Integration).order_by(models.Integration.name).all()


@router.post("", response_model=schemas.IntegrationOut, status_code=201)
async def create_integration(payload: schemas.IntegrationCreate, db: Session = Depends(get_db)):
    if payload.type not in integrations.TYPES:
        raise HTTPException(400, f"type must be one of {integrations.TYPES}")
    integ = models.Integration(**payload.model_dump(exclude_none=True))
    db.add(integ)
    db.commit()
    db.refresh(integ)
    await integrations.refresh(integ.id)
    return integ


@router.patch("/{integration_id}", response_model=schemas.IntegrationOut)
async def update_integration(integration_id: str, payload: schemas.IntegrationUpdate, db: Session = Depends(get_db)):
    integ = _get(db, integration_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(integ, k, v)
    db.commit()
    db.refresh(integ)
    await integrations.refresh(integ.id)
    return integ


@router.delete("/{integration_id}", status_code=204)
def delete_integration(integration_id: str, db: Session = Depends(get_db)):
    db.delete(_get(db, integration_id))
    db.commit()


@router.get("/data", response_model=dict[str, schemas.IntegrationData])
def all_data():
    return integrations.get_all()


@router.get("/{integration_id}/data", response_model=schemas.IntegrationData)
def get_data(integration_id: str, db: Session = Depends(get_db)):
    _get(db, integration_id)
    return integrations.get(integration_id) or {"integration_id": integration_id, "ok": False, "error": "Not fetched yet"}


@router.post("/{integration_id}/refresh", response_model=schemas.IntegrationData)
async def refresh(integration_id: str):
    result = await integrations.refresh(integration_id)
    if result is None:
        raise HTTPException(404, "Integration not found")
    return result


@router.post("/{integration_id}/docker/{container_id}/{action}", response_model=schemas.IntegrationData)
async def docker_action(integration_id: str, container_id: str, action: str, db: Session = Depends(get_db)):
    integ = _get(db, integration_id)
    if integ.type != "docker":
        raise HTTPException(400, "Not a docker integration")
    try:
        await integrations.docker_action(integ.config or {}, container_id, action)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Docker error: {exc}")
    return await integrations.refresh(integration_id)
