from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import notify, schemas
from ..database import get_db
from .settings import load

router = APIRouter(prefix="/api/notify", tags=["notify"])


@router.post("/test", response_model=schemas.NotifyResult)
async def test(db: Session = Depends(get_db)):
    cfg = load(db)
    if cfg["notify_type"] == "none" or not cfg["notify_url"]:
        raise HTTPException(400, "Choose a notification type and enter a URL first")
    try:
        await notify.send_test(cfg)
    except notify.NotifyError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": None}
