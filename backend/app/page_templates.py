"""Built-in page templates: files in `app/templates/<id>.json` in the portable page format
(see pages.py) plus {id, title, description, tags, requires}. They ship with the backend code.

A template id is only ever looked up in the directory listing, never joined into a path, so
`../` and friends simply do not exist.
"""
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from . import pages

TEMPLATE_DIR = Path(__file__).with_name("templates")


def _files() -> dict[str, Path]:
    return {p.stem: p for p in sorted(TEMPLATE_DIR.glob("*.json")) if p.is_file()}


def template_ids() -> list[str]:
    return list(_files())


def load(template_id: str) -> dict[str, Any]:
    """The whole template file. 404 for any id that is not in the template directory."""
    path = _files().get(template_id) if isinstance(template_id, str) else None
    if path is None:
        raise HTTPException(404, "Template not found")
    return json.loads(path.read_text(encoding="utf-8"))


def meta(doc: dict[str, Any]) -> dict[str, Any]:
    """Card data for the list: the metadata plus the widget rectangles (no configs) for the mini-map."""
    widgets = doc.get("widgets", [])
    rows = max((w["y"] + w["h"] for w in widgets), default=0)
    return {
        "id": doc["id"], "title": doc["title"], "description": doc["description"], "tags": doc.get("tags", []),
        "requires": doc.get("requires", []), "name": doc.get("name", doc["title"]),
        "columns": doc.get("columns", 24), "row_height": doc.get("row_height", 40), "rows": rows,
        "widget_count": len(widgets),
        "layout": [{"type": w["type"], "x": w["x"], "y": w["y"], "w": w["w"], "h": w["h"]} for w in widgets],
    }


def list_all() -> list[dict[str, Any]]:
    return [meta(load(i)) for i in template_ids()]


def page_of(template_id: str) -> pages.PortablePage:
    return pages.parse_page(load(template_id))


def find(template_id: str) -> Optional[dict[str, Any]]:
    try:
        return load(template_id)
    except HTTPException:
        return None
