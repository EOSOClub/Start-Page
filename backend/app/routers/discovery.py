"""Service discovery from Docker. Writers only (see auth.SENSITIVE_PREFIXES): it reveals the
infrastructure and makes the server talk to Docker. No state is kept between the two calls."""
import asyncio
import logging
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import auth, discovery, integrations, models
from ..database import get_db
from ..integrations.types import docker
from . import icons
from .services import to_out

log = logging.getLogger("startpage.discovery")
router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class ImportItem(BaseModel):
    key: str = Field(max_length=200)
    action: Literal["create", "update"]
    service_id: Optional[str] = Field(default=None, max_length=64)
    fields: dict[str, Any] = Field(default_factory=dict)


class ImportRequest(BaseModel):
    items: list[ImportItem] = Field(max_length=discovery.MAX_ITEMS)


def _writer_only(request: Request) -> None:
    if auth.is_guest(request):
        raise HTTPException(401, "Authentication required")


def _request_host(request: Request) -> Optional[str]:
    """Hostname of the Host header (port removed) when it is a plain hostname or IPv4 address."""
    raw = request.headers.get("host", "").strip()
    host = raw.rsplit(":", 1)[0] if ":" in raw and not raw.startswith("[") else raw
    return host if discovery.valid_host(host) else None


def _integration_host(integ) -> Optional[str]:
    """The Docker host of a TCP integration URL (containers are published there)."""
    url = str((integ.config or {}).get("url") or "").strip()
    if not url or url.startswith("unix://"):
        return None
    try:
        host = urlsplit(url if "://" in url else f"http://{url}").hostname
    except ValueError:
        return None
    return host if host and discovery.valid_host(host) else None


@router.get("/docker")
async def discover_docker(
    request: Request,
    integration_id: Optional[str] = Query(default=None, max_length=64),
    default_host: Optional[str] = Query(default=None, max_length=253),
    db: Session = Depends(get_db),
):
    """Candidate services from the containers of one docker integration (or all of them)."""
    _writer_only(request)
    if default_host is not None and not discovery.valid_host(default_host.strip()):
        raise HTTPException(422, "default_host must be a plain hostname or IPv4 address")
    if integration_id:
        integ = db.get(models.Integration, integration_id)
        if not integ:
            raise HTTPException(404, "Integration not found")
        if integ.type != "docker":
            raise HTTPException(400, "Not a docker integration")
        sources = [integ]
    else:
        sources = [i for i in db.query(models.Integration).filter_by(type="docker", enabled=True).order_by(models.Integration.name).all()]

    if default_host:
        fallback, fallback_note = default_host.strip(), "from the default_host parameter"
    else:
        header = _request_host(request)
        fallback = header or "localhost"
        fallback_note = "from the address this page was opened on" if header else "(localhost: no usable Host header)"
    services = db.query(models.Service).all()
    names = icons._cache.get("names") or []
    icon_names = set(names) if names else None

    async def one(integ):
        snap = integrations.snapshot(integ)
        try:
            rows = await integrations.call(snap, docker.list_containers)
        except integrations.ActionFailed as exc:
            return integ, None, str(exc)
        return integ, rows, ""

    def derive_all(integ, rows, host, note):
        """CPU-bound label parsing for one integration: runs in a thread, never on the event loop."""
        found, skip = [], []
        for row in rows:
            row = {**row, "integration_id": integ.id, "integration_name": integ.name}
            try:
                cand, why, evidence = discovery.derive(row, host, note, icon_names)
                if cand is None:
                    skip.append({"container_name": row["name"], "reason": why})
                    continue
                discovery.match_existing(cand, evidence, services)
                found.append(cand)
            except Exception:  # noqa: BLE001  one odd container must never fail the whole listing
                log.exception("discovery: could not analyse a container")
                skip.append({"container_name": row.get("name", "?"), "reason": "could not be analysed"})
        return found, skip

    candidates, skipped, errors = [], [], []
    for integ, rows, error in await asyncio.gather(*[one(i) for i in sources]):
        if rows is None:
            errors.append({"integration_id": integ.id, "error": error})
            continue
        own = _integration_host(integ)
        host, note = (own, "from the Docker integration's URL") if own else (fallback, fallback_note)
        found, skip = await asyncio.to_thread(derive_all, integ, rows, host, note)
        candidates += found
        skipped += skip
    candidates.sort(key=lambda c: (c["existing_service_id"] is not None, c["category"].lower(), c["name"].lower()))
    return {"candidates": candidates, "skipped": skipped, "errors": errors}


@router.post("/import")
def import_discovered(payload: ImportRequest, request: Request, db: Session = Depends(get_db)):
    """Create or update services from what the user picked (fields as edited in the UI).

    Per item: an invalid item lands in `errors` and does not stop the others; the valid ones are
    saved in one transaction. `update` changes only the fields supplied."""
    _writer_only(request)
    return discovery.import_items(db, [i.model_dump() for i in payload.items], to_out)
