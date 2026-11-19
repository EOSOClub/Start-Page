import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth, backups, health, integrations, models, notify, pwa, schemas
from .database import Base, SessionLocal, engine, get_db
from .routers import auth as auth_router
from .routers import backups as backups_router
from .routers import discovery as discovery_router
from .routers import config, dashboards, favicons, icons, services, settings, weather, widgets
from .routers import health as health_router
from .routers import integrations as integrations_router
from .routers import notify as notify_router
from .routers import pages as pages_router
from .routers import templates as templates_router

logging.basicConfig(level=logging.INFO)
# httpx logs every request URL at INFO, and a notification target (a webhook URL) is a secret.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DB_BUSY = {"detail": "Database busy — please retry"}


def _migrate():
    """Add columns introduced after the first release to existing SQLite databases."""
    insp = inspect(engine)
    wanted = {
        "services": {
            "health_type": "VARCHAR DEFAULT 'http'",
            "depends_on": "JSON DEFAULT '[]'",
        },
        "widgets": {
            "layout_sm": "JSON",  # phone placement; NULL = none
        },
    }
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        # create_all() never adds an index to a table that already exists (phase-1 databases).
        if "health_checks" in insp.get_table_names():
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {models.HEALTH_CHECK_INDEX} "
                    "ON health_checks (service_id, checked_at)"
                )
            )


TLS_MIGRATION_KEY = "migration:integrations_verify_tls"


def _migrate_integration_tls(session_factory=None):
    """Integrations now verify TLS certificates by default. Rows that existed before that change were
    created with the old default (off, and the key usually absent): write `verify_tls: false` into every
    HTTP-based one, once, so nothing that works today starts failing. Recorded in app_settings, so it
    never runs twice (a row created later without the key means "verify")."""
    with (session_factory or SessionLocal)() as db:
        if db.get(models.AppSetting, TLS_MIGRATION_KEY) is not None:
            return
        changed = 0
        for row in db.query(models.Integration).all():
            spec = integrations.SPECS.get(row.type)
            cfg = dict(row.config or {})
            if spec is not None and spec.uses_http and "verify_tls" not in cfg:
                cfg["verify_tls"] = False
                row.config = cfg
                changed += 1
        db.add(models.AppSetting(key=TLS_MIGRATION_KEY, value={"done": True, "rows": changed}))
        db.commit()
        if changed:
            logging.getLogger("startpage").info("kept TLS verification off for %d existing integration(s)", changed)


def _repair_rows():
    """Make rows written by older code safe: a stored protocol other than http/https, a `host:port` host,
    and layout numbers that the API models refuse (NaN, huge, negative, a phone layout that is not a
    placement). Idempotent and cheap; every fix is logged and a replaced service value is kept in
    metadata.import_original. Separate from _migrate() (columns) on purpose."""
    from .validation import repair_service

    fixed = 0
    with SessionLocal() as db:
        for svc in db.query(models.Service).all():
            fields = {"protocol": svc.protocol, "host": svc.host, "port": svc.port, "path": svc.path or "",
                      "metadata_": svc.metadata_ or {}}
            before = dict(fields)
            repair_service(fields)
            if fields != before:
                for key, value in fields.items():
                    setattr(svc, key, value)
                fixed += 1
        for w in db.query(models.Widget).all():
            clean = {
                "x": schemas._num(w.x, 0, schemas.X_MAX, 0), "y": schemas._num(w.y, 0, schemas.Y_MAX, 0),
                "w": schemas._num(w.w, 0.01, schemas.X_MAX, 4, positive=True),
                "h": schemas._num(w.h, 0.01, schemas.Y_MAX, 3, positive=True),
            }
            changed = False
            for key, value in clean.items():
                if getattr(w, key) != value:
                    setattr(w, key, value)
                    changed = True
            if w.layout_sm is not None:
                safe = schemas.sanitize_layout_sm(w.layout_sm)
                stored = w.layout_sm if isinstance(w.layout_sm, dict) else {}
                if safe is None or any(stored.get(k) != safe[k] for k in ("x", "y", "w", "h")):
                    w.layout_sm = safe
                    changed = True
            fixed += changed
        for dash in db.query(models.Dashboard).all():
            settings = schemas.DashboardOut._settings_out(dash.settings)
            if settings != (dash.settings or {}):
                dash.settings = settings
                fixed += 1
        if fixed:
            db.commit()
            logging.getLogger("startpage").warning("repaired %d stored row(s) that held invalid values", fixed)


def _seed():
    """Create a default empty dashboard on first run."""
    with SessionLocal() as db:
        if db.query(models.Dashboard).count() == 0:
            db.add(models.Dashboard(name="Home", position=0))
            db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _migrate()
    _migrate_integration_tls()
    _repair_rows()
    _seed()
    health.start()
    integrations.start()
    backups.start()
    yield
    health.stop()
    await notify.stop()
    integrations.stop()
    await integrations.close_all()
    backups.stop()


# The interactive docs live under /api/ so the auth guard covers them (and guests are refused).
app = FastAPI(
    title="Start Page",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
    swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
)

# The frontend is served from the same origin (or via the Vite proxy in dev), so
# no cross-origin access is needed. Extra origins can be allowed explicitly.
# The CORS middleware is added last (below) so it is the outermost layer: the 401/403/503
# answers of the guards then carry CORS headers too and an allowed origin can read them.
CORS_ORIGINS = [o.strip() for o in os.environ.get("STARTPAGE_CORS_ORIGINS", "").split(",") if o.strip()]

ALLOW_EMBED = os.environ.get("STARTPAGE_ALLOW_EMBED", "").lower() in ("1", "true", "yes")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-requested-by"
APP_CLIENT = "startpage"  # the X-Requested-By value the frontend sends

# Counts successful API writes made by anything other than the app itself (e.g. the
# Claude MCP server). Open tabs poll /api/revision and reload when it moves; `boot`
# changes on restart so a reset counter still reads as a change. In memory on purpose:
# the container runs a single uvicorn worker.
revision = {"rev": 0, "boot": uuid.uuid4().hex}


def _resolve_principal(request: Request) -> auth.Principal:
    # Same session source as the routes (tests override get_db). A generator dependency:
    # closing it runs its cleanup.
    gen = app.dependency_overrides.get(get_db, get_db)()
    try:
        return auth.resolve(next(gen), request)
    finally:
        gen.close()


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    """Optional authentication for /api/* (see auth.py). Off until a password is set.

    Declared before csrf_guard, so csrf_guard is the outer layer and runs first.
    The SPA shell and /assets are never gated: the login screen has to load.
    """
    path = auth.api_path(request)  # from the ASGI scope, not request.url (built from the Host header)
    if path is None:
        return await call_next(request)
    try:
        principal = await run_in_threadpool(_resolve_principal, request)
    except OperationalError:
        # Middleware sits outside the app's exception handlers; answer the same 503 (and stay closed).
        return JSONResponse(DB_BUSY, status_code=503)
    request.state.principal = principal
    if not auth.allowed(principal, request.method, path):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Reject state-changing API calls that don't come from the app itself.

    This page is open in every browser tab, so any website visited could otherwise
    POST to it. A custom header can't be sent cross-origin without a CORS preflight
    (which fails), and Sec-Fetch-Site flags cross-site requests in modern browsers.
    """
    unsafe = request.method in UNSAFE_METHODS and auth.api_path(request) is not None
    if unsafe:
        cross_site = request.headers.get("sec-fetch-site") == "cross-site"
        origin = request.headers.get("origin")
        if (cross_site and origin not in CORS_ORIGINS) or CSRF_HEADER not in request.headers:
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
    response = await call_next(request)
    if unsafe and response.status_code < 400 and request.headers[CSRF_HEADER] != APP_CLIENT:
        revision["rev"] += 1
    return response


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = "/" + str(request.scope.get("path", "")).lstrip("/")  # the ASGI scope, not request.url (Host header)
    # Hardening for every response. nosniff: an API or favicon answer is never re-interpreted as HTML or
    # script. Referrer-Policy: the app's URLs (and any token in them) never travel to other sites.
    # X-Frame-Options SAMEORIGIN: other sites cannot frame the app (clickjacking); the app's own iframe
    # widgets and its sandboxed custom-widget frames are unaffected (they frame other sites or srcdoc).
    # Embedding the page in another dashboard on a different origin needs STARTPAGE_ALLOW_EMBED=1.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if not ALLOW_EMBED:
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if path.startswith("/assets/"):
        # Vite emits content-hashed file names, so they never change. Errors (a 404 for a file that
        # does not exist yet) must not be cached for a year: only successful and 304 responses are.
        if response.status_code < 400:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/") and response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.add_middleware(GZipMiddleware, minimum_size=1024)
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    """The default 422 echoes the offending input: a NaN there made the answer itself fail to serialise (a
    500), and a 50 kB value came back as 50 kB. Only where and why is returned."""
    errors = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": str(e.get("msg", ""))[:300]} for e in exc.errors()[:100]]
    return JSONResponse({"detail": errors}, status_code=422)


@app.exception_handler(IntegrityError)
async def on_integrity_error(request: Request, exc: IntegrityError):
    return JSONResponse({"detail": "Invalid reference or duplicate id"}, status_code=400)


@app.exception_handler(OperationalError)
async def on_operational_error(request: Request, exc: OperationalError):
    return JSONResponse(DB_BUSY, status_code=503)


@app.get("/api/revision")
def get_revision():
    """Change marker for writes made outside the app (see `revision`)."""
    return revision


app.include_router(auth_router.router)
app.include_router(dashboards.router)
app.include_router(widgets.router)
app.include_router(services.router)
app.include_router(discovery_router.router)
app.include_router(health_router.router)
app.include_router(notify_router.router)
app.include_router(config.router)
app.include_router(pages_router.router)
app.include_router(templates_router.router)
app.include_router(integrations_router.router)
app.include_router(settings.router)
app.include_router(backups_router.router)
app.include_router(icons.router)
app.include_router(favicons.router)
app.include_router(weather.router)
# Manifest, service worker and icons at the site root. Included before the SPA catch-all below.
app.include_router(pwa.router)

# Serve the built frontend (frontend/dist -> backend/static) when present.
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # Not a file server: only the shell is served from here. The other root files (sw.js, the
        # icons, the manifest) have explicit routes in pwa.py and /assets is mounted above, so no
        # request path is ever turned into a file path. Anything else is a real 404, never the
        # HTML shell (unknown /api/ paths included).
        if full_path in ("", "index.html"):
            return FileResponse(STATIC_DIR / "index.html")
        return JSONResponse({"detail": "Not found"}, status_code=404)
