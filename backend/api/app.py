"""FastAPI application factory for AI Incident Manager.

Usage::

    # Development
    uvicorn backend.api.app:create_app --factory --reload

    # Programmatic
    app = create_app()
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config_loader import AppConfig
from backend.api.deps import set_mcp_pool, set_session_factory
from backend.db.engine import get_engine, get_session_factory, resolve_database_url
from backend.detector.runner import DetectorBudgetGuard
from backend.detector.scheduler import DetectorScheduler
from backend.mcp.pool import MCPServerPool
from backend.skills.importer import auto_import as auto_import_skills


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup / shutdown lifecycle.

    On startup:
    - Create async engine from ``AIM_DATABASE_URL``
    - Bind session factory so ``get_db`` works

    On shutdown:
    - Dispose the engine and connection pool
    """
    config: AppConfig = app.state.config
    database_url = resolve_database_url(config.db)

    engine = get_engine(database_url)
    factory = get_session_factory(engine)
    set_session_factory(factory)
    app.state.database_url = database_url
    app.state.session_factory = factory

    pool = MCPServerPool(factory, env_fallback=config.mcp_servers)
    set_mcp_pool(pool)
    app.state.mcp_pool = pool
    app.state.detector_budget = DetectorBudgetGuard(
        max_runs_per_hour=config.detector.max_runs_per_hour,
        global_budget=config.detector.budget,
    )
    scheduler = DetectorScheduler(
        factory,
        pool=pool,
        config=config,
        budget_guard=app.state.detector_budget,
    )
    app.state.detector_scheduler = scheduler
    if config.detector.enabled:
        await scheduler.start()

    # Import any SKILL.md files under ./skills/ that aren't already in the DB.
    # Best-effort: failures are logged but do not block startup.
    try:
        await auto_import_skills(factory, skills_dir="skills")
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "skills.auto_import: startup scan failed: %s", exc
        )

    yield

    session_tasks = list(getattr(app.state, "session_tasks", set()))
    for task in session_tasks:
        task.cancel()
    if session_tasks:
        import asyncio
        await asyncio.gather(*session_tasks, return_exceptions=True)

    background_tasks = list(getattr(app.state, "background_tasks", set()))
    for task in background_tasks:
        task.cancel()
    if background_tasks:
        import asyncio
        await asyncio.gather(*background_tasks, return_exceptions=True)

    await scheduler.stop()
    await engine.dispose()


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build and return the fully-configured FastAPI application."""
    config = config or AppConfig.load()

    app = FastAPI(
        title=config.app.name,
        description="AI-powered incident response with tiered access controls",
        version=config.app.version,
        lifespan=_lifespan,
    )
    app.state.config = config
    app.state.session_factory = None
    app.state.mcp_pool = MCPServerPool(None, env_fallback=config.mcp_servers)
    app.state.session_tasks = set()
    app.state.background_tasks = set()

    # -- Ingest rate limiter ------------------------------------------------
    from backend.ingest.rate_limiter import IngestRateLimiter
    app.state.ingest_limiter = IngestRateLimiter(
        max_requests=config.ingest.rate_limit,
        window_seconds=config.ingest.rate_window,
    )

    # -- CORS ---------------------------------------------------------------
    allowed_origins = config.cors.origins
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
    from backend.api.routes.mcp_servers import router as mcp_servers_router
    from backend.api.routes.skills import router as skills_router
    from backend.api.routes.audit import router as audit_router
    from backend.api.routes.config import router as config_router
    from backend.api.routes.ws import router as ws_router
    from backend.api.routes.ingest import router as ingest_router
    from backend.api.routes.detectors import router as detectors_router
    from backend.api.routes.webhook_triggers import router as webhook_triggers_router

    app.include_router(auth_router)
    app.include_router(incidents_router)
    app.include_router(sessions_router)
    app.include_router(approvals_router)
    app.include_router(models_router)
    app.include_router(mcp_servers_router)
    app.include_router(skills_router)
    app.include_router(audit_router)
    app.include_router(config_router)
    app.include_router(ws_router)
    app.include_router(ingest_router)
    app.include_router(detectors_router)
    app.include_router(webhook_triggers_router)

    # -- Health check -------------------------------------------------------
    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    # -- Frontend (static export) — MUST be registered last so API routes win
    from backend.api.static import mount_frontend
    mount_frontend(app, config.app.frontend_static_dir)

    return app
