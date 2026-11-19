from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db
from ..validation import field_errors, normalise_path

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


def _check(data: dict) -> dict:
    """Hold written values to the shared service rules (422, one message per field); stores the path normalised.
    Only what is written is checked: reading or editing other fields of an older, looser row never fails."""
    errors = field_errors(data)
    if errors:
        raise HTTPException(422, [{"loc": ["body", k], "msg": m} for k, m in errors.items()])
    normalise_path(data)
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


def strip_userinfo(url: str) -> str:
    """A health URL without user:password@ (the user's own monitoring credential: never shown to guests)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]))


def for_viewer(out: schemas.ServiceOut, request: Request) -> schemas.ServiceOut:
    """Read-only guests get the service without the credentials of its health URL. The frontend never
    renders `health_url` as a link; it is only displayed in the editor, which guests do not have."""
    if auth.is_guest(request) and out.health_url:
        out.health_url = strip_userinfo(out.health_url)
    return out


@router.get("", response_model=list[schemas.ServiceOut])
def list_services(request: Request, db: Session = Depends(get_db)):
    rows = db.query(models.Service).order_by(models.Service.category, models.Service.name).all()
    return [for_viewer(to_out(s), request) for s in rows]


@router.post("", response_model=schemas.ServiceOut, status_code=201)
def create_service(payload: schemas.ServiceCreate, db: Session = Depends(get_db)):
    svc = models.Service(**_to_model_kwargs(_check(payload.model_dump(exclude_none=True))))
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return to_out(svc)


@router.get("/{service_id}", response_model=schemas.ServiceOut)
def get_service(service_id: str, request: Request, db: Session = Depends(get_db)):
    return for_viewer(to_out(_get(db, service_id)), request)


@router.patch("/{service_id}", response_model=schemas.ServiceOut)
def update_service(service_id: str, payload: schemas.ServiceUpdate, db: Session = Depends(get_db)):
    svc = _get(db, service_id)
    for k, v in _to_model_kwargs(_check(schemas.patch_data(payload, nullable=("port",)))).items():
        setattr(svc, k, v)
    db.commit()
    db.refresh(svc)
    return to_out(svc)


@router.delete("/{service_id}", status_code=204)
def delete_service(service_id: str, db: Session = Depends(get_db)):
    db.delete(_get(db, service_id))
    db.commit()
