"""FastAPI application factory for OpsMender AI.

Usage::

    # Development
    uvicorn backend.api.app:create_app --factory --reload

    # Programmatic
    app = create_app()
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config_loader import AppConfig, check_production_safety
from backend.api.deps import set_mcp_pool, set_session_factory
from backend.db.engine import get_engine, get_session_factory, resolve_database_url
from backend.mcp.mcp_json import MCPJSONSyncer
from backend.mcp.pool import MCPServerPool
from backend.sla.downsampler import UptimeDownsampler
from backend.sla.poller import SLAPoller
from backend.skills.importer import auto_import as auto_import_skills


def _workflow_concurrency_from_env(default: int = 1) -> int:
    raw = os.environ.get("OPSMENDER_INCIDENT_SESSION_CONCURRENCY")
    if raw is None:
        # Backwards-compatible aliases for early local installs.
        raw = os.environ.get("OPSMENDER_SESSION_WORKFLOW_CONCURRENCY")
    if raw is None:
        raw = os.environ.get("OPSMENDER_AI_WORKER_CONCURRENCY")
    if raw is None:
        return default
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return default


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup / shutdown lifecycle.

    On startup:
    - Create async engine from ``OPSMENDER_DATABASE_URL``
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

    # Sprint 56: bootstrap admin from env vars if the users table is empty.
    # No-op when bootstrap env vars are unset or users already exist.
    from backend.people.bootstrap import bootstrap_admin

    await bootstrap_admin(factory, config.people)

    # Bootstrap default model config from env vars (OPSMENDER_MODEL_PROVIDER
    # + OPSMENDER_MODEL_ID + provider-specific base_url) so an operator can
    # bring up a working agent loop on a fresh DB without clicking through
    # /dashboard/models first. No-op if any model_configs row already exists.
    from backend.llm.bootstrap import bootstrap_model_config

    await bootstrap_model_config(factory, config.providers)

    pool = MCPServerPool(factory, env_fallback=config.mcp_servers)
    set_mcp_pool(pool)
    app.state.mcp_pool = pool
    sla_poller = SLAPoller(factory, config)
    app.state.sla_poller = sla_poller
    if config.sla.poller_enabled:
        await sla_poller.start()

    downsampler = UptimeDownsampler(factory)
    app.state.uptime_downsampler = downsampler
    await downsampler.start()

    # Escalation chain scheduler (Sprint 34). Always on — no-op when there
    # are no chains running.
    from backend.paging.scheduler import EscalationScheduler

    escalation_scheduler = EscalationScheduler(factory)
    app.state.escalation_scheduler = escalation_scheduler
    await escalation_scheduler.start()

    # Audit scheduler (Sprint 39 step 2). No-op when no schedules exist.
    from backend.auditor.scheduler import AuditScheduler

    audit_scheduler = AuditScheduler(
        factory,
        pool=app.state.mcp_pool,
        config=app.state.config,
    )
    app.state.audit_scheduler = audit_scheduler
    await audit_scheduler.start()

    # Data-retention scheduler (Sprint 53). Enabled by default so a fresh
    # deployment auto-prunes from day one; operators can disable per category
    # via Config → "Storage & retention" or set OPSMENDER_RETENTION_ENABLED=false.
    from backend.retention.scheduler import RetentionScheduler

    retention_scheduler = RetentionScheduler(factory)
    app.state.retention_scheduler = retention_scheduler
    await retention_scheduler.start()

    # mcp.json file mirror (Sprint 42 step 6). Opt-in via OPSMENDER_MCP_JSON_SYNC.
    # When enabled, reconcile the file against every org's MCP servers on
    # startup so file edits made while the service was down are applied.
    mcp_json_syncer = MCPJSONSyncer(factory)
    app.state.mcp_json_syncer = mcp_json_syncer
    if mcp_json_syncer.enabled:
        try:
            from backend.db.repos import OrganizationRepo

            async with factory() as session:
                orgs = await OrganizationRepo.list_all(session)
            target_orgs = (
                [mcp_json_syncer.pinned_org_id]
                if mcp_json_syncer.pinned_org_id is not None
                else [org.id for org in orgs]
            )
            for target in target_orgs:
                await mcp_json_syncer.reconcile_on_startup(target)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                "mcp_json: startup reconcile failed: %s", exc
            )

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

    await downsampler.stop()
    await sla_poller.stop()
    await escalation_scheduler.stop()
    await audit_scheduler.stop()
    await retention_scheduler.stop()
    await engine.dispose()


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build and return the fully-configured FastAPI application."""
    config = config or AppConfig.load()
    check_production_safety(config)

    app = FastAPI(
        title=config.app.name,
        description="AI-powered incident response with tiered access controls",
        version=config.app.version,
        lifespan=_lifespan,
    )
    app.state.config = config
    app.state.session_factory = None
    app.state.mcp_pool = MCPServerPool(None, env_fallback=config.mcp_servers)
    app.state.mcp_json_syncer = MCPJSONSyncer(None)
    app.state.session_tasks = set()
    app.state.background_tasks = set()
    app.state.session_workflow_concurrency = _workflow_concurrency_from_env()
    app.state.session_workflow_semaphore = asyncio.Semaphore(
        app.state.session_workflow_concurrency
    )

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
    from backend.api.routes.webhook_triggers import router as webhook_triggers_router
    from backend.api.routes.workflow_profiles import router as workflow_profiles_router
    from backend.api.routes.agent_team_profiles import (
        router as agent_team_profiles_router,
    )
    from backend.api.routes.sla import router as sla_router
    from backend.api.routes.bot_connectors import router as bot_connectors_router
    from backend.api.routes.bot_oauth import router as bot_oauth_router
    from backend.api.routes.bot_webhooks import router as bot_webhooks_router
    from backend.api.routes.organizations import (
        router as organizations_router,
        tenant_router,
    )
    from backend.api.routes.sso import router as sso_router
    from backend.api.routes.saml import router as saml_router
    from backend.api.routes.audits import router as audits_router
    from backend.api.routes.paging import router as paging_router
    from backend.api.routes.slack_paging import router as slack_paging_router
    from backend.api.routes.teams_paging import router as teams_paging_router
    from backend.api.routes.memories import (
        router as memories_router,
        sessions_memory_router,
    )
    from backend.api.routes.retention import router as retention_router
    from backend.api.routes.invites import (
        admin_router as invites_admin_router,
        public_router as invites_public_router,
    )

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
    app.include_router(webhook_triggers_router)
    app.include_router(workflow_profiles_router)
    app.include_router(agent_team_profiles_router)
    app.include_router(sla_router)
    app.include_router(bot_oauth_router)
    app.include_router(bot_connectors_router)
    app.include_router(bot_webhooks_router)
    app.include_router(organizations_router)
    app.include_router(tenant_router)
    app.include_router(sso_router)
    app.include_router(saml_router)
    app.include_router(audits_router)
    app.include_router(paging_router)
    app.include_router(slack_paging_router)
    app.include_router(teams_paging_router)
    app.include_router(memories_router)
    app.include_router(sessions_memory_router)
    app.include_router(retention_router)
    app.include_router(invites_admin_router)
    app.include_router(invites_public_router)

    # -- Health check -------------------------------------------------------
    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    # -- Frontend (static export) — MUST be registered last so API routes win
    from backend.api.static import mount_frontend

    mount_frontend(app, config.app.frontend_static_dir)

    return app
