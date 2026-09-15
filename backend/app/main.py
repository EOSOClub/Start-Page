import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import backups, health, integrations, models
from .database import Base, SessionLocal, engine
from .routers import backups as backups_router
from .routers import config, dashboards, favicons, icons, services, settings, weather, widgets
from .routers import health as health_router
from .routers import integrations as integrations_router

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _migrate():
    """Add columns introduced after the first release to existing SQLite databases."""
    insp = inspect(engine)
    wanted = {
        "services": {
            "health_type": "VARCHAR DEFAULT 'http'",
            "depends_on": "JSON DEFAULT '[]'",
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
    _seed()
    health.start()
    integrations.start()
    backups.start()
    yield
    health.stop()
    integrations.stop()
    backups.stop()


app = FastAPI(title="Start Page", lifespan=lifespan)

# The frontend is served from the same origin (or via the Vite proxy in dev), so
# no cross-origin access is needed. Extra origins can be allowed explicitly.
CORS_ORIGINS = [o.strip() for o in os.environ.get("STARTPAGE_CORS_ORIGINS", "").split(",") if o.strip()]
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-requested-by"


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Reject state-changing API calls that don't come from the app itself.

    This page is open in every browser tab, so any website visited could otherwise
    POST to it. A custom header can't be sent cross-origin without a CORS preflight
    (which fails), and Sec-Fetch-Site flags cross-site requests in modern browsers.
    """
    if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
        cross_site = request.headers.get("sec-fetch-site") == "cross-site"
        origin = request.headers.get("origin")
        if (cross_site and origin not in CORS_ORIGINS) or CSRF_HEADER not in request.headers:
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/"):
        # Vite emits content-hashed file names, so they never change.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/") and response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.exception_handler(IntegrityError)
async def on_integrity_error(request: Request, exc: IntegrityError):
    return JSONResponse({"detail": "Invalid reference or duplicate id"}, status_code=400)


@app.exception_handler(OperationalError)
async def on_operational_error(request: Request, exc: OperationalError):
    return JSONResponse({"detail": "Database busy — please retry"}, status_code=503)


app.include_router(dashboards.router)
app.include_router(widgets.router)
app.include_router(services.router)
app.include_router(health_router.router)
app.include_router(config.router)
app.include_router(integrations_router.router)
app.include_router(settings.router)
app.include_router(backups_router.router)
app.include_router(icons.router)
app.include_router(favicons.router)
app.include_router(weather.router)

# Serve the built frontend (frontend/dist -> backend/static) when present.
if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        # A path that reaches the SPA catch-all is either a real build artifact or a
        # 404 — never the HTML shell: unknown /api/ paths must answer a real 404.
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        # Resolve BEFORE the containment check: Path("static/../data/x").is_relative_to()
        # is lexically True, and resolve() also follows symlinks out of static.
        candidate = (STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_relative_to(STATIC_DIR.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        if full_path:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(STATIC_DIR / "index.html")
