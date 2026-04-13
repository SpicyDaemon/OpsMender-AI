"""FastAPI application factory for AI Incident Manager.

Usage::

    # Development
    uvicorn backend.api.app:create_app --factory --reload

    # Programmatic
    app = create_app()
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.deps import set_session_factory
from backend.db.engine import get_engine, get_session_factory


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup / shutdown lifecycle.

    On startup:
    - Create async engine from ``AIM_DATABASE_URL``
    - Bind session factory so ``get_db`` works

    On shutdown:
    - Dispose the engine and connection pool
    """
    database_url = os.environ.get(
        "AIM_DATABASE_URL",
        "postgresql+asyncpg://aim:aim@localhost:5432/aim",
    )

    engine = get_engine(database_url)
    factory = get_session_factory(engine)
    set_session_factory(factory)

    yield

    await engine.dispose()


def create_app() -> FastAPI:
    """Build and return the fully-configured FastAPI application."""
    app = FastAPI(
        title="AI Incident Manager",
        description="AI-powered incident response with tiered access controls",
        version="0.2.0",
        lifespan=_lifespan,
    )

    # -- CORS ---------------------------------------------------------------
    allowed_origins = os.environ.get("AIM_CORS_ORIGINS", "*").split(",")
    # allow_credentials=True is incompatible with wildcard origins per CORS spec
    allow_credentials = "*" not in allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Routes -------------------------------------------------------------
    from backend.api.routes.auth import router as auth_router
    from backend.api.routes.incidents import router as incidents_router
    from backend.api.routes.sessions import router as sessions_router
    from backend.api.routes.approvals import router as approvals_router
    from backend.api.routes.models import router as models_router
    from backend.api.routes.audit import router as audit_router
    from backend.api.routes.config import router as config_router
    from backend.api.routes.ws import router as ws_router

    app.include_router(auth_router)
    app.include_router(incidents_router)
    app.include_router(sessions_router)
    app.include_router(approvals_router)
    app.include_router(models_router)
    app.include_router(audit_router)
    app.include_router(config_router)
    app.include_router(ws_router)

    # -- Health check -------------------------------------------------------
    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    return app
