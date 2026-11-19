from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])
KEY = "app"


MASK = schemas.NOTIFY_URL_MASK


def public(settings: dict) -> dict:
    """Settings as the API shows them. `notify_url` is a bearer secret (a webhook URL) and
    write-only: the mask when set, "" when not, plus `notify_url_set`. The same for everyone,
    guests included. Exports and backups use `load` and keep the real value."""
    out = dict(settings)
    is_set = bool(out.get("notify_url"))
    out["notify_url"] = MASK if is_set else ""
    out["notify_url_set"] = is_set
    return out


def load(db: Session) -> dict:
    row = db.get(models.AppSetting, KEY)
    stored = (row.value if row else None) or {}
    try:
        return schemas.Settings(**stored).model_dump()
    except ValidationError as exc:
        # A bad stored value must not take down settings, export and backups: use its default.
        bad = {e["loc"][0] for e in exc.errors() if e["loc"]}
        return schemas.Settings(**{k: v for k, v in stored.items() if k not in bad}).model_dump()


def save(db: Session, data: dict) -> dict:
    data = {k: v for k, v in data.items() if k != "notify_url_set"}  # read-only, derived
    if data.get("notify_url") == MASK:
        del data["notify_url"]  # the mask sent back (or imported) means "keep the stored URL"
    row = db.get(models.AppSetting, KEY)
    current = (row.value if row else None) or {}
    merged = {**current, **data}
    try:
        out = schemas.Settings(**merged).model_dump()  # validate before anything is written
    except ValidationError as exc:
        bad = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        if any(k in data for k in bad):
            raise HTTPException(422, f"Invalid settings value for: {', '.join(bad)}")
        merged = {k: v for k, v in merged.items() if k not in bad}  # heal old bad stored keys
        out = schemas.Settings(**merged).model_dump()
    if row:
        row.value = merged
    else:
        db.add(models.AppSetting(key=KEY, value=merged))
    db.commit()
    return out


@router.get("", response_model=schemas.SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    return public(load(db))


@router.patch("", response_model=schemas.SettingsOut)
def update_settings(payload: schemas.Settings, db: Session = Depends(get_db)):
    return public(save(db, payload.model_dump(exclude_unset=True)))
