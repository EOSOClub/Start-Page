"""Share a single page: export it as portable JSON, import such a file (or preview what it would do)."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, pages
from ..database import get_db

router = APIRouter(prefix="/api/dashboards", tags=["pages"])


@router.get("/{dashboard_id}/export")
def export_dashboard(dashboard_id: str, db: Session = Depends(get_db)) -> dict:
    """The page as portable JSON (`startpage-page` v1). Integration config and secrets are never included;
    `warnings` lists anything in the page that looks secret. Writers only (see auth.SENSITIVE_PATTERNS)."""
    dash = db.get(models.Dashboard, dashboard_id)
    if not dash:
        raise HTTPException(404, "Dashboard not found")
    return pages.export_page(db, dash)


def _request(body: Any) -> pages.ImportRequest:
    return pages.validate_model(pages.ImportRequest, body)


@router.post("/import", status_code=201)
def import_dashboard(body: Any = Depends(pages.read_json_body), db: Session = Depends(get_db)) -> dict:
    """Create a NEW page from a page file; never overwrites. One transaction."""
    req = _request(body)
    return pages.run_import(db, pages.parse_page(req.page), req)


@router.post("/import/preview")
def preview_import(body: Any = Depends(pages.read_json_body), db: Session = Depends(get_db)) -> dict:
    """What `import` would do with this body, without writing anything."""
    req = _request(body)
    return pages.run_import(db, pages.parse_page(req.page), req, preview=True)
