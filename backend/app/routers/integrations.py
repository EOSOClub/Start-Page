from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import auth, integrations, models, schemas
from ..database import get_db
from ..integrations.base import Spec, public_config, validate_config

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

GUEST_ERROR = "Unavailable"  # guests never see why an integration failed


def _get(db: Session, integration_id: str) -> models.Integration:
    integ = db.get(models.Integration, integration_id)
    if not integ:
        raise HTTPException(404, "Integration not found")
    return integ


def _spec(type_: str) -> Spec:
    spec = integrations.SPECS.get(type_)
    if spec is None:
        raise HTTPException(400, f"type must be one of {sorted(integrations.SPECS)}")
    return spec


def _config(spec: Spec, incoming: dict, stored: Optional[dict]) -> dict:
    """The config to save: secrets resolved against `stored`, then validated (422 per field)."""
    clean, errors = validate_config(spec, incoming, stored)
    if errors:
        raise HTTPException(422, [{"loc": ["body", "config", key], "msg": msg, "type": "value_error"} for key, msg in errors])
    return clean


def _out(integ: models.Integration, guest: bool = False) -> schemas.IntegrationOut:
    """The API view of a row: secrets masked, and for guests no config at all."""
    out = schemas.IntegrationOut.model_validate(integ)
    config = {} if guest else public_config(integrations.SPECS.get(integ.type), integ.config)
    return out.model_copy(update={"config": config})


def _public_data(data: Optional[dict], request: Request) -> Optional[dict]:
    if data and data.get("error") and auth.is_guest(request):
        return {**data, "error": GUEST_ERROR}
    return data


@router.get("/types")
def list_types():
    return [integrations.SPECS[t].public() for t in sorted(integrations.SPECS)]


@router.post("/test", response_model=schemas.IntegrationTestResult)
async def test_integration(payload: schemas.IntegrationTest, db: Session = Depends(get_db)):
    """Run one fetch for a config that is not saved. With `id`, masked secrets come from that integration."""
    spec = _spec(payload.type)
    stored = None
    if payload.id:
        row = _get(db, payload.id)
        stored = row.config if row.type == payload.type else None
    config = _config(spec, payload.config, stored)
    return await integrations.test_config(spec, config)


@router.get("", response_model=list[schemas.IntegrationOut])
def list_integrations(request: Request, db: Session = Depends(get_db)):
    guest = auth.is_guest(request)  # config holds API keys and headers: guests see names and types only
    rows = db.query(models.Integration).order_by(models.Integration.name).all()
    return [_out(r, guest) for r in rows]


@router.post("", response_model=schemas.IntegrationOut, status_code=201)
async def create_integration(payload: schemas.IntegrationCreate, db: Session = Depends(get_db)):
    spec = _spec(payload.type)
    data = payload.model_dump(exclude_none=True)
    data["config"] = _config(spec, payload.config, None)
    data["interval"] = payload.interval or spec.default_interval
    integ = models.Integration(**data)
    db.add(integ)
    db.commit()
    db.refresh(integ)
    if integ.enabled:  # a disabled integration is never fetched
        await integrations.refresh_row(integ)
    return _out(integ)


@router.patch("/{integration_id}", response_model=schemas.IntegrationOut)
async def update_integration(integration_id: str, payload: schemas.IntegrationUpdate, db: Session = Depends(get_db)):
    integ = _get(db, integration_id)
    changes = schemas.patch_data(payload)
    was_disabled = not integ.enabled
    new_type = changes.get("type", integ.type)
    spec = _spec(new_type)
    if "config" in changes or new_type != integ.type:
        same = new_type == integ.type
        changes["config"] = _config(spec, changes.get("config", integ.config if same else {}), integ.config if same else None)
    for k, v in changes.items():
        setattr(integ, k, v)
    db.commit()
    db.refresh(integ)
    if not integ.enabled:
        # disabled: no fetch; drop its data and close its client (the type's close hook logs out)
        integrations.invalidate(integ.id, drop_data=True)
        await integrations.close_pending()
        return _out(integ)
    config_changed = "config" in changes or "type" in changes
    if config_changed:
        integrations.invalidate(integ.id)  # new client and state for the new config
        await integrations.close_pending()
    if config_changed or was_disabled:
        await integrations.refresh_row(integ)
    # a name- or interval-only edit does not fetch: the scheduler keeps polling on the cached context
    return _out(integ)


@router.delete("/{integration_id}", status_code=204)
async def delete_integration(integration_id: str, db: Session = Depends(get_db)):
    db.delete(_get(db, integration_id))
    db.commit()
    integrations.invalidate(integration_id, drop_data=True)
    await integrations.close_pending()


@router.get("/data", response_model=dict[str, schemas.IntegrationData])
def all_data(request: Request):
    return {k: _public_data(v, request) for k, v in integrations.get_all().items()}


@router.get("/{integration_id}/data", response_model=schemas.IntegrationData)
def get_data(integration_id: str, request: Request, db: Session = Depends(get_db)):
    _get(db, integration_id)
    data = integrations.get(integration_id) or {"integration_id": integration_id, "ok": False, "error": "Not fetched yet"}
    return _public_data(data, request)


@router.post("/{integration_id}/refresh", response_model=schemas.IntegrationData)
async def refresh(integration_id: str, db: Session = Depends(get_db)):
    return await integrations.refresh_row(_get(db, integration_id))


@router.post("/{integration_id}/action/{name}", response_model=schemas.IntegrationData)
async def run_action(integration_id: str, name: str, payload: Optional[dict[str, Any]] = Body(default=None), db: Session = Depends(get_db)):
    """Run one of the type's actions (see GET /types: `actions`). The body is the action's payload.
    Returns the integration's fresh data. 404 unknown action, 422 bad payload, 502 upstream failure."""
    integ = _get(db, integration_id)
    try:
        await integrations.run_action(integ, name, payload)
    except integrations.UnknownAction:
        raise HTTPException(404, f"Unknown action {name!r} for type {integ.type}")
    except integrations.InvalidPayload as exc:
        raise HTTPException(422, [{"loc": ["body", key], "msg": msg, "type": "value_error"} for key, msg in exc.errors])
    except integrations.ActionFailed as exc:
        raise HTTPException(502, str(exc))
    return await integrations.refresh_row(integ)


@router.post("/{integration_id}/docker/{container_id}/{action}", response_model=schemas.IntegrationData)
async def docker_action(integration_id: str, container_id: str, action: str, db: Session = Depends(get_db)):
    """Kept for the Docker widget; the same as the generic `container` action."""
    integ = _get(db, integration_id)
    if integ.type != "docker":
        raise HTTPException(400, "Not a docker integration")
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "Unsupported action")
    try:
        await integrations.run_action(integ, "container", {"id": container_id, "action": action})
    except integrations.InvalidPayload as exc:
        raise HTTPException(400, str(exc))
    except integrations.ActionFailed as exc:
        raise HTTPException(502, f"Docker error: {exc}")
    return await integrations.refresh_row(integ)
