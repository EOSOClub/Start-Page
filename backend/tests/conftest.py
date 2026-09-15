"""Shared fixtures for the backend test suite.

Isolation contract (ADR — Iteration 009):
- Tests use a dedicated in-memory SQLite engine; the real backend/data/startpage.db
  is never opened, never migrated and never seeded.
- TestClient(app) is used WITHOUT the context manager, so the app's lifespan never
  runs: no create_all/_migrate/_seed on the real DB, no health/integrations/backups
  background loops, no network calls, no backup file writes.
- Every router endpoint goes through Depends(get_db), so the override below fully
  isolates the CRUD surface.
"""

import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db

# The SPA catch-all and /assets mount only register when the frontend build dir
# exists (main.py guards on STATIC_DIR.is_dir()). backend/static/ is gitignored,
# so on a fresh clone the app would register no SPA routes and the traversal /
# api-404 regression tests would pass vacuously. Bootstrap the (gitignored) build
# dir BEFORE importing app.main — the only FS writes here are backend/static/,
# never backend/data/, so the iteration-009 isolation contract holds. A real build
# (index.html present) is left untouched.
_STATIC = Path(__file__).resolve().parents[1] / "static"
(_STATIC / "assets").mkdir(parents=True, exist_ok=True)
if not (_STATIC / "index.html").exists():
    (_STATIC / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")

from app.main import app  # noqa: E402, F401  (importing main registers the models)

HEADERS = {"x-requested-by": "startpage"}


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fks(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session):
    def override():
        yield session

    app.dependency_overrides[get_db] = override
    # Bare TestClient on purpose — entering the context manager would run the
    # real lifespan (real DB writes + background loops + network).
    yield TestClient(app)
    app.dependency_overrides.clear()