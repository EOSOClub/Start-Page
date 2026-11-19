"""Built-in page templates (see page_templates.py). Writers only: see auth.SENSITIVE_PREFIXES."""
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import page_templates, pages
from ..database import get_db

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("")
def list_templates() -> list[dict]:
    """Every template's metadata and widget rectangles (no widget configs)."""
    return page_templates.list_all()


@router.get("/{template_id}")
def get_template(template_id: str) -> dict:
    """The whole template: its metadata and the page in the portable format."""
    return page_templates.load(template_id)


@router.post("/{template_id}/apply", status_code=201)
def apply_template(template_id: str, body: Any = Depends(pages.read_json_body), db: Session = Depends(get_db)) -> dict:
    """Create a new page from a template. Same options and response as a page import."""
    opts = pages.validate_model(pages.ApplyOptions, body if isinstance(body, dict) else {})
    return pages.run_import(db, page_templates.page_of(template_id), opts)
