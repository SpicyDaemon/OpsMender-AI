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
from backend.logging_config import configure_logging
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
    deployment = config.deployment
    run_bootstrap = deployment.mode == "monolith" or deployment.service_role == "api"
    run_schedulers = (
        deployment.mode == "monolith" or deployment.service_role == "scheduler"
    )
    run_worker = (
        deployment.mode == "distributed" and deployment.service_role == "worker"
    )
    scheduler_stoppers = []
    worker_bus = None

    if deployment.mode == "distributed":
        from backend.services.incident_events import IncidentEventPublisher

        app.state.incident_event_publisher = IncidentEventPublisher(database_url)

    # Re-apply the persisted log level (saved from the dashboard Config page)
    # now that the DB is reachable, so a UI change survives a restart. Falls
    # back to the env-var level already applied in create_app() when no
    # override row exists. Best-effort: a read failure must not block startup.
    try:
        from backend.db.repos import RuntimeConfigRepo

        async with factory() as session:
            persisted_level = await RuntimeConfigRepo.get_global_value(
                session, "logging_level"
            )
        if persisted_level:
            configure_logging(persisted_level)
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(
            "logging: failed to apply persisted log level: %s", exc
        )

    # Sprint 56: bootstrap admin from env vars if the users table is empty.
    # No-op when bootstrap env vars are unset or users already exist.
    if run_bootstrap:
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
    if run_schedulers:
        from backend.services.incident_events import dispatch_incident_created

        async def incident_created_callback(
            org_id, incident_id, auto_start_tier
        ):
            await dispatch_incident_created(
                app,
                org_id=org_id,
                incident_id=incident_id,
                auto_start_tier=auto_start_tier,
            )

        sla_poller = SLAPoller(
            factory,
            config,
            incident_created_callback=incident_created_callback,
        )
        app.state.sla_poller = sla_poller
        if config.sla.poller_enabled:
            await sla_poller.start()
        scheduler_stoppers.append(sla_poller.stop)

        downsampler = UptimeDownsampler(factory)
        app.state.uptime_downsampler = downsampler
        await downsampler.start()
        scheduler_stoppers.append(downsampler.stop)

        from backend.paging.scheduler import EscalationScheduler

        escalation_scheduler = EscalationScheduler(factory)
        app.state.escalation_scheduler = escalation_scheduler
        await escalation_scheduler.start()
        scheduler_stoppers.append(escalation_scheduler.stop)

        from backend.auditor.scheduler import AuditScheduler

        audit_scheduler = AuditScheduler(
            factory,
            pool=app.state.mcp_pool,
            config=app.state.config,
        )
        app.state.audit_scheduler = audit_scheduler
        await audit_scheduler.start()
        scheduler_stoppers.append(audit_scheduler.stop)

        from backend.services.audit_archiver import AuditArchiveScheduler

        audit_archive_scheduler = AuditArchiveScheduler(factory, config.audit)
        app.state.audit_archive_scheduler = audit_archive_scheduler
        await audit_archive_scheduler.start()
        scheduler_stoppers.append(audit_archive_scheduler.stop)

        from backend.retention.scheduler import RetentionScheduler

        retention_scheduler = RetentionScheduler(factory)
        app.state.retention_scheduler = retention_scheduler
        await retention_scheduler.start()
        scheduler_stoppers.append(retention_scheduler.stop)

        from backend.reports.scheduler import ReportScheduler

        report_scheduler = ReportScheduler(factory)
        app.state.report_scheduler = report_scheduler
        await report_scheduler.start()
        scheduler_stoppers.append(report_scheduler.stop)

        from backend.services.session_orchestration import SessionQueueScheduler

        session_queue_scheduler = SessionQueueScheduler(
            app,
            poll_interval_seconds=config.sessions.sweep_interval_seconds,
        )
        app.state.session_queue_scheduler = session_queue_scheduler
        await session_queue_scheduler.start()
        scheduler_stoppers.append(session_queue_scheduler.stop)

    # mcp.json file mirror (Sprint 42 step 6). Opt-in via OPSMENDER_MCP_JSON_SYNC.
    # When enabled, reconcile the file against every org's MCP servers on
    # startup so file edits made while the service was down are applied.
    mcp_json_syncer = MCPJSONSyncer(factory if run_bootstrap else None)
    app.state.mcp_json_syncer = mcp_json_syncer
    if run_bootstrap and mcp_json_syncer.enabled:
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
    if run_bootstrap:
        try:
            await auto_import_skills(factory, skills_dir="skills")
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                "skills.auto_import: startup scan failed: %s", exc
            )

    if run_worker:
        from backend.services.incident_events import (
            start_incident_created_subscriber,
        )

        worker_bus = await start_incident_created_subscriber(app, database_url)

    yield

    if worker_bus is not None:
        worker_conn, unsubscribe = worker_bus
        await unsubscribe()
        await worker_conn.close()

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

    for stop in reversed(scheduler_stoppers):
        await stop()
    await engine.dispose()


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build and return the fully-configured FastAPI application."""
    config = config or AppConfig.load()
    check_production_safety(config)

    # Apply the env-var / .env log level immediately (process-global). The
    # lifespan startup re-applies any persisted dashboard override on top of
    # this once the DB is reachable.
    configure_logging(config.app.log_level)

    # Interactive API docs (/docs, /redoc, /openapi.json) enumerate the full
    # attack surface, so they are disabled by default in production. Operators
    # can opt back in with OPSMENDER_ENABLE_API_DOCS=true; development keeps
    # them on.
    docs_enabled = config.deployment.environment == "development" or (
        os.environ.get("OPSMENDER_ENABLE_API_DOCS", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    app = FastAPI(
        title=config.app.name,
        description="AI-powered incident response with tiered access controls",
        version=config.app.version,
        lifespan=_lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.config = config
    app.state.session_factory = None
    app.state.mcp_pool = MCPServerPool(None, env_fallback=config.mcp_servers)
    app.state.mcp_json_syncer = MCPJSONSyncer(None)
    app.state.session_tasks = set()
    app.state.background_tasks = set()
    app.state.incident_event_publisher = None
    app.state.deployment_mode = config.deployment.mode
    app.state.service_role = config.deployment.service_role
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

    # -- Security response headers -------------------------------------------
    # Conservative browser hardening for every response (API + served
    # frontend). Deliberately no CSP/HSTS here: CSP needs tuning against the
    # Next.js bundle, and HSTS is only meaningful behind TLS — both belong to
    # the operator's reverse proxy.
    @app.middleware("http")
    async def _security_headers(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        return response

    # -- Routes -------------------------------------------------------------
    from backend.api.routes.auth import router as auth_router
    from backend.api.routes.mfa import router as mfa_router
    from backend.api.routes.incidents import router as incidents_router
    from backend.api.routes.sessions import router as sessions_router
    from backend.api.routes.approvals import router as approvals_router
    from backend.api.routes.models import router as models_router
    from backend.api.routes.mcp_servers import router as mcp_servers_router
    from backend.api.routes.skills import router as skills_router
    from backend.api.routes.audit import router as audit_router
    from backend.api.routes.config import router as config_router
    from backend.api.routes.ws import router as ws_router
    from backend.api.routes.ingest import (
        router as ingest_router,
        webhook_router as ingest_webhook_router,
    )
    from backend.api.routes.reports import router as reports_router
    from backend.api.routes.integrations import router as integrations_router
    from backend.api.routes.workflow_profiles import router as workflow_profiles_router
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
    from backend.api.routes.analytics import router as analytics_router
    from backend.api.routes.api_tokens import router as api_tokens_router
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
    from backend.api.routes.notifications import router as notifications_router
    from backend.api.routes.ticket_sync import router as ticket_sync_router
    from backend.api.routes.voice import router as voice_router
    from backend.api.routes.voice_settings import router as voice_settings_router

    api_routers = [
        auth_router,
        mfa_router,
        incidents_router,
        sessions_router,
        approvals_router,
        models_router,
        mcp_servers_router,
        skills_router,
        audit_router,
        config_router,
        ws_router,
        ingest_router,
        reports_router,
        integrations_router,
        workflow_profiles_router,
        sla_router,
        bot_oauth_router,
        bot_connectors_router,
        organizations_router,
        tenant_router,
        sso_router,
        saml_router,
        audits_router,
        analytics_router,
        api_tokens_router,
        paging_router,
        memories_router,
        sessions_memory_router,
        retention_router,
        invites_admin_router,
        invites_public_router,
        notifications_router,
        voice_settings_router,
    ]
    dispatcher_routers = [
        ingest_webhook_router,
        bot_webhooks_router,
        slack_paging_router,
        teams_paging_router,
        ticket_sync_router,
        voice_router,
    ]

    deployment = config.deployment
    if deployment.mode == "monolith":
        selected_routers = [*api_routers, *dispatcher_routers]
    elif deployment.service_role == "api":
        selected_routers = api_routers
    elif deployment.service_role == "dispatcher":
        selected_routers = dispatcher_routers
    else:
        selected_routers = []

    for selected_router in selected_routers:
        app.include_router(selected_router)

    # -- Health check -------------------------------------------------------
    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    # -- Frontend (static export) — MUST be registered last so API routes win
    if deployment.mode == "monolith" or deployment.service_role == "api":
        from backend.api.static import mount_frontend

        mount_frontend(app, config.app.frontend_static_dir)

    return app
