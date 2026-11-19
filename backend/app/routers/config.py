"""Whole-configuration import/export as a single JSON bundle."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import integrations, models, schemas
from ..integrations.base import merge_secrets, validate_config
from ..database import get_db
from ..validation import neutralise_urls, repair_service
from .services import to_out as service_out
from .settings import load as load_settings, save as save_settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/export", response_model=schemas.ExportBundle)
def export_config(db: Session = Depends(get_db)):
    services = [service_out(s) for s in db.query(models.Service).all()]
    dashboards = db.query(models.Dashboard).order_by(models.Dashboard.position).all()
    integrations = db.query(models.Integration).all()
    return schemas.ExportBundle(
        version=schemas.BUNDLE_VERSION, services=services, dashboards=dashboards, integrations=integrations, settings=load_settings(db)
    )


@router.post("/import", response_model=schemas.ImportResult)
def import_config(
    bundle: schemas.ImportBundle,
    db: Session = Depends(get_db),
    replace: bool = Query(False, description="Wipe existing config before importing"),
):
    # Validate before the first write, so a bad bundle changes nothing.
    unknown = sorted({i.type for i in bundle.integrations} - set(integrations.SPECS))
    if unknown:
        raise HTTPException(422, f"Unknown integration type(s) {unknown}; valid: {sorted(integrations.SPECS)}")

    # Old databases and backups may hold values the current rules refuse (a `javascript` protocol, a
    # javascript: link). The import never fails for them: the offending field is neutralised and reported.
    warnings: list[str] = []
    widget_ids: set[str] = set()  # ids this import has already used
    if replace:
        # Widgets are always rebuilt from the bundle. Everything else is removed only when its
        # id is absent from the bundle; the rest is updated in place below. Deleting and
        # re-inserting a service would cascade away its health history, incident log and
        # alert state (health_checks / health_events / health_alert_state).
        db.query(models.Widget).delete()
        for model, keep in (
            (models.Dashboard, {d.id for d in bundle.dashboards}),
            (models.Service, {s.id for s in bundle.services}),
            (models.Integration, {i.id for i in bundle.integrations}),
        ):
            gone = [row_id for (row_id,) in db.query(model.id).all() if row_id not in keep]
            for i in range(0, len(gone), 500):
                db.query(model).filter(model.id.in_(gone[i : i + 500])).delete(synchronize_session=False)
        db.flush()

    for s in bundle.services:
        data = s.model_dump(exclude={"url"})
        data["metadata_"] = data.pop("metadata", {})
        label = (s.name or s.id)[:60]
        warnings.extend(f'Service "{label}": {note}.' for note in repair_service(data))
        existing = db.get(models.Service, s.id)
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            db.add(models.Service(**data))
    db.flush()

    for d in bundle.dashboards:
        data = d.model_dump(exclude={"widgets"})
        dash = db.get(models.Dashboard, d.id)
        if dash:
            for k, v in data.items():
                setattr(dash, k, v)
            for w in list(dash.widgets):
                db.delete(w)
        else:
            dash = models.Dashboard(**data)
            db.add(dash)
        db.flush()
        for w in d.widgets:
            wd = w.model_dump()
            if wd.get("layout_sm"):  # a phone position that does not fit the imported grid is dropped, not an error
                cols = schemas.phone_columns(d.settings)
                if schemas.fit_error(wd["layout_sm"]["x"], wd["layout_sm"]["w"], cols):
                    wd["layout_sm"] = None
                    warnings.append(f"Widget {wd['type']} on page \"{d.name[:60]}\": its phone position does not fit the page's {cols} phone columns and was dropped.")
            wd["config"], notes = neutralise_urls(wd.get("config") or {}, wd["type"])
            warnings.extend(f"Widget {wd['type']} on page \"{d.name[:60]}\": {note}." for note in notes)
            # A widget keeps its id when that id is free, so undo steps recorded before a
            # page was deleted still address the right widget once the page is restored.
            # An id that is still live elsewhere (a widget moved between pages in the
            # bundle) or repeated inside the bundle gets a fresh one instead: Widget.id
            # is a leaf key (nothing FKs it), so that is invisible to the user and a
            # same-id collision stays impossible. This page's own old widgets were
            # deleted and flushed above, so their ids count as free.
            wid = wd.get("id")
            if not wid or wid in widget_ids or db.get(models.Widget, wid):
                wd.pop("id", None)
            else:
                widget_ids.add(wid)
            wd["dashboard_id"] = dash.id
            if wd.get("service_id") and not db.get(models.Service, wd["service_id"]):
                wd["service_id"] = None
            db.add(models.Widget(**wd))
    changed_integrations: list[tuple[str, bool]] = []  # (id, enabled): invalidated AFTER the commit
    for i in bundle.integrations:
        spec = integrations.SPECS[i.type]
        data = i.model_dump()
        existing = db.get(models.Integration, i.id)
        # The mask (a bundle that came from the API, not from export) never overwrites or becomes a
        # secret: it keeps the stored value, or is dropped when there is none. Only a stored row of the
        # SAME type counts: a secret must not survive into a row of another type with the same id.
        stored = existing.config if existing and existing.type == i.type else None
        config = merge_secrets(spec.fields, data["config"], stored)
        if spec.uses_http and bundle.version < 3 and "verify_tls" not in config:
            config["verify_tls"] = False  # older bundles were written when certificates were not verified
        clean, errors = validate_config(spec, config, None)
        if errors:
            # Restore must not lose a row: it is imported as it came, but disabled, and reported.
            data["enabled"] = False
            warnings.append(f'Integration "{(i.name or i.id)[:60]}": ' + "; ".join(f"{k} {m}" for k, m in errors[:4])
                            + ". It was imported disabled: fix it and enable it.")
        else:
            config = clean
        data["config"] = config
        if existing:
            if (existing.type, existing.config, existing.enabled) != (data["type"], data["config"], data["enabled"]):
                changed_integrations.append((i.id, data["enabled"]))  # edited: fresh client and an immediate refresh
            for k, v in data.items():
                setattr(existing, k, v)
        else:
            db.add(models.Integration(**data))
    if bundle.settings:
        settings = {}
        for key, value in bundle.settings.items():  # a bad value is dropped and reported, never an error
            try:
                schemas.Settings(**{key: value})
                settings[key] = value
            except ValidationError:
                warnings.append(f'Setting "{str(key)[:60]}" was not imported (invalid value).')
        if settings:
            save_settings(db, settings)
    counts = {
        "services": len(bundle.services), "dashboards": len(bundle.dashboards),
        "widgets": sum(len(d.widgets) for d in bundle.dashboards), "integrations": len(bundle.integrations),
    }
    db.commit()
    # After the commit: a poll that ran in between would rebuild its context from the OLD stored row.
    for integration_id, enabled in changed_integrations:
        integrations.invalidate(integration_id, drop_data=not enabled)
    return schemas.ImportResult(counts=counts, warnings=warnings)
