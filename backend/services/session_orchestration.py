"""Durable capacity admission, priority queueing, and queue draining."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.bots.notifier import schedule_session_chat_event
from backend.db.models import Incident, ModelConfig, Session
from backend.db.repos import (
    ApprovalRequestRepo,
    AuditEntryRepo,
    IncidentAssignmentRepo,
    IncidentPageRepo,
    IncidentRepo,
    ModelConfigRepo,
    SessionRepo,
)
from backend.llm.selection import (
    choose_model_config_by_identity,
    choose_model_for_incident_service,
    has_active_model_configs,
)
from backend.notifications import (
    CATEGORY_APPROVAL,
    CATEGORY_SESSION,
    emit_to_users,
    org_user_ids_with_roles,
)

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


@dataclass(slots=True)
class SessionAdmission:
    session: Session
    queued: bool = False
    warning: str | None = None
    takeover: bool = False
    start_required: bool = False
    created: bool = False
    started_from_queue: bool = False


async def notify_capacity_event(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    session: Session,
    event_type: str,
    title: str,
    body: str,
) -> None:
    user_ids: set[uuid.UUID] = set()
    if session.incident_id is not None:
        assignment = await IncidentAssignmentRepo.get_active(
            db, org_id, session.incident_id
        )
        if assignment is not None:
            user_ids.add(assignment.assigned_to)
        pages = await IncidentPageRepo.list_for_incident(
            db, org_id, session.incident_id
        )
        user_ids.update(page.user_id for page in pages)
    if not user_ids:
        user_ids.update(
            await org_user_ids_with_roles(db, org_id, ("admin", "operator"))
        )
    await emit_to_users(
        db,
        org_id,
        user_ids,
        event_type=event_type,
        category=CATEGORY_SESSION,
        title=title,
        body=body,
        link=f"/dashboard/sessions/detail?id={session.id}",
        incident_id=session.incident_id,
        session_id=session.id,
    )


async def _force_candidate(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident | None,
    requested_provider: str | None,
    requested_model_id: str | None,
) -> tuple[ModelConfig | None, int, int]:
    model: ModelConfig | None = None
    if requested_provider and requested_model_id:
        model, _ = await choose_model_config_by_identity(
            db,
            org_id,
            provider=requested_provider,
            model_id=requested_model_id,
        )
    elif incident is not None:
        model = await choose_model_for_incident_service(
            db,
            org_id,
            service_id=incident.service_id,
            ingestion_model_config_id=incident.ingestion_model_config_id,
        )
    if model is None:
        return None, 0, 0
    occupancy = (
        await SessionRepo.active_occupancy_by_model_config(db, org_id)
    ).get(model.id, 0)
    return model, occupancy, int(model.max_concurrent_sessions or 0)


async def admit_session(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident | None,
    tier: int,
    workflow_profile_id: uuid.UUID | None = None,
    agent_team_profile_id: uuid.UUID | None = None,
    requested_provider: str | None = None,
    requested_model_id: str | None = None,
    force: bool = False,
    actor_user_id: uuid.UUID | None = None,
    queue_ttl_seconds: int = 900,
    takeover_existing: bool = False,
) -> SessionAdmission:
    """Create or take over a session under the capacity policy."""
    existing = (
        await SessionRepo.get_nonterminal_for_incident(db, org_id, incident.id)
        if incident is not None
        else None
    )
    if existing is not None:
        if existing.status == "queued" and force:
            if actor_user_id is None:
                raise ValueError("force start requires an actor")
            model, occupancy, cap = await _force_candidate(
                db,
                org_id,
                incident=incident,
                requested_provider=requested_provider,
                requested_model_id=requested_model_id,
            )
            if model is None:
                raise ValueError("No configured model is available to force")
            warning = (
                f"Model {model.name} is at {occupancy}/{cap}; forcing this "
                "session exceeds its configured limit."
            )
            existing.status = "active"
            existing.model_config_id = model.id
            existing.model_provider = model.provider
            existing.model_id = model.model_id
            existing.started_at = _utcnow()
            existing.queue_reason = None
            existing.force_started = True
            existing.force_started_by = actor_user_id
            existing.force_start_occupancy = occupancy
            existing.force_start_cap = cap
            await AuditEntryRepo.create(
                db,
                org_id,
                session_id=existing.id,
                tier=tier,
                entry_type="session_force_start",
                result={
                    "actor_user_id": str(actor_user_id),
                    "incident_id": str(incident.id) if incident else None,
                    "model_config_id": str(model.id),
                    "occupancy": occupancy,
                    "cap": cap,
                    "from_queue": True,
                },
                permitted=True,
            )
            await notify_capacity_event(
                db,
                org_id,
                session=existing,
                event_type="session.started_from_queue",
                title=f"AI session started: {incident.title}",
                body=f"An operator forced a start on {model.name}.",
            )
            return SessionAdmission(
                session=existing,
                warning=warning,
                takeover=True,
                start_required=True,
                started_from_queue=True,
            )
        return SessionAdmission(
            session=existing,
            queued=existing.status == "queued",
            warning=(
                "The existing AI session will be taken over."
                if takeover_existing and existing.status != "queued"
                else "An existing AI session was retained for this incident."
                if existing.status != "queued"
                else "This incident already has an AI session waiting for capacity."
            ),
            takeover=takeover_existing,
            start_required=takeover_existing and existing.status != "queued",
        )

    model: ModelConfig | None = None
    has_saved_match = False
    requested_config_id: uuid.UUID | None = None
    if requested_provider and requested_model_id:
        model, has_saved_match = await choose_model_config_by_identity(
            db,
            org_id,
            provider=requested_provider,
            model_id=requested_model_id,
            respect_capacity=True,
        )
        if has_saved_match:
            saved, _ = await choose_model_config_by_identity(
                db,
                org_id,
                provider=requested_provider,
                model_id=requested_model_id,
            )
            requested_config_id = None if saved is None else saved.id
    elif incident is not None:
        model = await choose_model_for_incident_service(
            db,
            org_id,
            service_id=incident.service_id,
            ingestion_model_config_id=incident.ingestion_model_config_id,
            respect_capacity=True,
        )

    configured = await has_active_model_configs(db, org_id)
    at_capacity = configured and model is None and (
        incident is not None or has_saved_match
    )

    warning: str | None = None
    force_occupancy: int | None = None
    force_cap: int | None = None
    if at_capacity and force:
        if actor_user_id is None:
            raise ValueError("force start requires an actor")
        model, force_occupancy, force_cap = await _force_candidate(
            db,
            org_id,
            incident=incident,
            requested_provider=requested_provider,
            requested_model_id=requested_model_id,
        )
        if model is None:
            raise ValueError("No configured model is available to force")
        warning = (
            f"Model {model.name} is at {force_occupancy}/{force_cap}; "
            "forcing this session exceeds its configured limit."
        )

    if at_capacity and not force:
        if incident is None:
            raise ValueError("All configured incident-response models are at capacity")
        if incident.status != "open" or incident.acknowledged_at is not None:
            raise ValueError(
                "The incident was already acknowledged or closed; a delayed "
                "AI session will not be queued."
            )
        now = _utcnow()
        session = await SessionRepo.create(
            db,
            org_id,
            tier=tier,
            incident_id=incident.id,
            workflow_profile_id=workflow_profile_id,
            agent_team_profile_id=agent_team_profile_id,
            requested_model_config_id=requested_config_id,
            status="queued",
            queued_at=now,
            queue_expires_at=now + timedelta(seconds=queue_ttl_seconds),
            queue_reason="model_capacity",
        )
        await notify_capacity_event(
            db,
            org_id,
            session=session,
            event_type="session.queued",
            title=f"AI session queued: {incident.title}",
            body="All configured incident-response models are at capacity.",
        )
        return SessionAdmission(session=session, queued=True, created=True)

    session = await SessionRepo.create(
        db,
        org_id,
        tier=tier,
        incident_id=None if incident is None else incident.id,
        workflow_profile_id=workflow_profile_id,
        agent_team_profile_id=agent_team_profile_id,
        model_config_id=None if model is None else model.id,
        requested_model_config_id=requested_config_id,
        model_provider=(
            requested_provider if model is None else model.provider
        ),
        model_id=requested_model_id if model is None else model.model_id,
        force_started=warning is not None,
        force_started_by=actor_user_id if warning else None,
        force_start_occupancy=force_occupancy,
        force_start_cap=force_cap,
    )
    if warning is not None:
        await AuditEntryRepo.create(
            db,
            org_id,
            session_id=session.id,
            tier=tier,
            entry_type="session_force_start",
            result={
                "actor_user_id": str(actor_user_id),
                "incident_id": (
                    str(incident.id) if incident is not None else None
                ),
                "model_config_id": str(model.id),
                "occupancy": force_occupancy,
                "cap": force_cap,
            },
            permitted=True,
        )
    return SessionAdmission(
        session=session,
        warning=warning,
        start_required=incident is not None,
        created=True,
    )


async def dispatch_session_ready(app, session_id: uuid.UUID) -> None:
    """Run locally or notify a distributed worker that a session is admitted."""
    deployment = app.state.config.deployment
    if deployment.mode == "monolith" or deployment.service_role == "worker":
        from backend.api.session_runner import schedule_session_workflow

        schedule_session_workflow(app, session_id=session_id)
        return
    publisher = app.state.incident_event_publisher
    await publisher.publish_session_ready(session_id)


async def drain_session_queue(app, *, org_id: uuid.UUID | None = None) -> int:
    """Admit queued sessions by priority/FIFO until no capacity remains."""
    factory = app.state.session_factory
    async with factory() as db:
        queued_ids = [
            item.id
            for item in await SessionRepo.list_queued_for_drain(
                db, org_id, limit=200
            )
        ]

    started: list[tuple[uuid.UUID, uuid.UUID]] = []
    notified: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    now = _utcnow()
    for session_id in queued_ids:
        async with factory() as db:
            session = await SessionRepo.get_by_id_global_for_update(db, session_id)
            if session is None or session.status != "queued":
                continue
            incident = (
                await IncidentRepo.get_by_id(db, session.org_id, session.incident_id)
                if session.incident_id is not None
                else None
            )
            expiry = _aware(session.queue_expires_at)
            if expiry is not None and now >= expiry:
                await SessionRepo.set_status(
                    db,
                    session.org_id,
                    session.id,
                    status="cancelled",
                    summary="AI session queue wait expired.",
                    ended_at=now,
                )
                await notify_capacity_event(
                    db,
                    session.org_id,
                    session=session,
                    event_type="session.queue_expired",
                    title="Queued AI session expired",
                    body="The session was dropped after waiting too long for capacity.",
                )
                await db.commit()
                notified.append(
                    (session.org_id, session.id, "session.queue_expired")
                )
                continue
            if (
                incident is None
                or incident.status != "open"
                or incident.acknowledged_at is not None
            ):
                await SessionRepo.set_status(
                    db,
                    session.org_id,
                    session.id,
                    status="cancelled",
                    summary="Incident was handled before AI capacity became available.",
                    ended_at=now,
                )
                await notify_capacity_event(
                    db,
                    session.org_id,
                    session=session,
                    event_type="session.queue_cancelled",
                    title="Queued AI session cancelled",
                    body="The incident was already acknowledged or closed.",
                )
                await db.commit()
                notified.append(
                    (session.org_id, session.id, "session.queue_cancelled")
                )
                continue

            model: ModelConfig | None = None
            if session.requested_model_config_id is not None:
                requested = await ModelConfigRepo.get_by_id(
                    db, session.org_id, session.requested_model_config_id
                )
                if requested is not None:
                    model, _ = await choose_model_config_by_identity(
                        db,
                        session.org_id,
                        provider=requested.provider,
                        model_id=requested.model_id,
                        respect_capacity=True,
                    )
            else:
                model = await choose_model_for_incident_service(
                    db,
                    session.org_id,
                    service_id=incident.service_id,
                    ingestion_model_config_id=incident.ingestion_model_config_id,
                    respect_capacity=True,
                )
            if model is None:
                continue
            admitted = await SessionRepo.admit_queued(
                db,
                session.org_id,
                session.id,
                model_config_id=model.id,
                model_provider=model.provider,
                model_id=model.model_id,
                started_at=now,
            )
            if not admitted:
                continue
            await notify_capacity_event(
                db,
                session.org_id,
                session=session,
                event_type="session.started_from_queue",
                title=f"AI session started: {incident.title}",
                body=f"Capacity became available on {model.name}.",
            )
            await db.commit()
            started.append((session.org_id, session.id))

    for event_org_id, session_id, event_type in notified:
        schedule_session_chat_event(
            factory,
            org_id=event_org_id,
            task_registry=app.state.background_tasks,
            event_type=event_type,
            session_id=session_id,
        )
    for event_org_id, session_id in started:
        schedule_session_chat_event(
            factory,
            org_id=event_org_id,
            task_registry=app.state.background_tasks,
            event_type="session.started_from_queue",
            session_id=session_id,
        )
        await dispatch_session_ready(app, session_id)
    return len(started)


def schedule_queue_drain(app, *, org_id: uuid.UUID | None = None) -> asyncio.Task:
    async def delayed_drain() -> int:
        # Callers often schedule immediately before their transaction commits.
        await asyncio.sleep(0.1)
        return await drain_session_queue(app, org_id=org_id)

    task = asyncio.create_task(
        delayed_drain(),
        name=f"session-queue-drain:{org_id or 'all'}",
    )
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)
    return task


async def sweep_approval_holds(app) -> int:
    """Prompt near-expiry approvals and expire abandoned slot holds."""
    factory = app.state.session_factory
    now = _utcnow()
    warned = 0
    expired_count = 0
    async with factory() as db:
        from backend.db.repos import OrganizationRepo

        orgs = await OrganizationRepo.list_all(db)
    for org in orgs:
        async with factory() as db:
            pending = await ApprovalRequestRepo.list_pending(db, org.id)
            for request in pending:
                expires_at = _aware(request.expires_at)
                if expires_at is None:
                    continue
                remaining = (expires_at - now).total_seconds()
                if remaining <= 0:
                    await ApprovalRequestRepo.resolve(
                        db, org.id, request.id, status="expired"
                    )
                    await SessionRepo.set_status(
                        db,
                        org.id,
                        request.session_id,
                        status="timed_out",
                        ended_at=now,
                    )
                    expired_count += 1
                    continue
                if (
                    remaining
                    <= app.state.config.sessions.approval_warning_seconds
                    and request.extension_notified_at is None
                    and await ApprovalRequestRepo.mark_extension_notified(
                        db, org.id, request.id, at=now
                    )
                ):
                    session = await SessionRepo.get_by_id(
                        db, org.id, request.session_id
                    )
                    user_ids = await org_user_ids_with_roles(
                        db, org.id, ("admin", "operator")
                    )
                    await emit_to_users(
                        db,
                        org.id,
                        user_ids,
                        event_type="approval.extension_requested",
                        category=CATEGORY_APPROVAL,
                        title="Extend AI session approval hold?",
                        body=(
                            "This approval will expire in about a minute and "
                            "release its model slot."
                        ),
                        link="/dashboard/approvals",
                        incident_id=(
                            None if session is None else session.incident_id
                        ),
                        session_id=request.session_id,
                    )
                    warned += 1
            await db.commit()
    if warned:
        log.info("session.approval_hold warnings=%s", warned)
    if expired_count:
        log.info("session.approval_hold expired=%s", expired_count)
        schedule_queue_drain(app)
    return warned


class SessionQueueScheduler:
    def __init__(self, app, *, poll_interval_seconds: int = 30) -> None:
        self._app = app
        self._poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(), name="opsmender-session-queue-scheduler"
            )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await drain_session_queue(self._app)
                await sweep_approval_holds(self._app)
            except Exception:
                log.exception("session queue scheduler tick failed")
            await asyncio.sleep(self._poll_interval_seconds)
