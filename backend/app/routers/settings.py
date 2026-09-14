from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])
KEY = "app"


def load(db: Session) -> dict:
    row = db.get(models.AppSetting, KEY)
    stored = row.value if row else {}
    return schemas.Settings(**stored).model_dump()


def save(db: Session, data: dict) -> dict:
    row = db.get(models.AppSetting, KEY)
    current = row.value if row else {}
    merged = {**current, **data}
    if row:
        row.value = merged
    else:
        db.add(models.AppSetting(key=KEY, value=merged))
    db.commit()
    return schemas.Settings(**merged).model_dump()


@router.get("", response_model=schemas.Settings)
def get_settings(db: Session = Depends(get_db)):
    return load(db)


@router.patch("", response_model=schemas.Settings)
def update_settings(payload: schemas.Settings, db: Session = Depends(get_db)):
    return save(db, payload.model_dump(exclude_unset=True))
