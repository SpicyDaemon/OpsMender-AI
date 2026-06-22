"""Auto-start policy helpers for externally ingested incidents."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config_loader import AppConfig
from backend.db.models import Incident
from backend.db.repos import IncidentRepo, SessionRepo
from backend.services.session_orchestration import (
    admit_session,
    dispatch_session_ready,
)
from backend.tiers.resolution import resolve_session_tier_for_incident

_ACTIVE_SESSION_STATUSES = {"queued", "active", "awaiting_approval"}
logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class IngestAutoStartPolicy:
    """The effective AI session auto-start decision input.

    Auto-start is purely tier-driven, so the only input is the resolved session
    tier for the incident.
    """

    session_tier: int


async def load_auto_start_policy(
    db: AsyncSession,
    org_id: uuid.UUID,
    config: AppConfig,
    *,
    incident: Incident | None = None,
) -> IngestAutoStartPolicy:
    """Resolve the effective auto-start policy (the session tier for the incident)."""
    return IngestAutoStartPolicy(
        session_tier=await resolve_session_tier_for_incident(
            db,
            org_id,
            config,
            incident=incident,
        ),
    )


def should_auto_start_session(
    incident: Incident,
    *,
    dedup_action: str,
    policy: IngestAutoStartPolicy,
) -> bool:
    """Return True when an ingested incident should auto-create a session."""
    return auto_start_skip_reason(
        incident,
        dedup_action=dedup_action,
        policy=policy,
    ) is None


def auto_start_skip_reason(
    incident: Incident,
    *,
    dedup_action: str,
    policy: IngestAutoStartPolicy,
) -> str | None:
    """Return a stable reason code for *not* auto-starting on incident creation,
    or ``None`` when an AI session should auto-start immediately.

    The decision is **tier-driven**, not gated by the legacy ingest opt-in flag:

    - **T0 (Autonomous)** → auto-start immediately for every newly created
      incident (manual, fire-test, or ingested). Test incidents are not exempt.
    - **T1/T2** → defer; the session starts after an Admin/Operator
      acknowledges the incident (``auto_start_deferred_to_ack``).

    The legacy ``enabled`` / ``min_severity`` / ``source`` ingest controls no
    longer gate this — tier alone decides.
    """
    if dedup_action != "created":
        return "auto_start_skipped_not_created"
    if incident.status == "resolved":
        return "auto_start_skipped_terminal_incident"
    if policy.session_tier != 0:
        # T1/T2 wait for an Admin/Operator acknowledgment before starting.
        return "auto_start_deferred_to_ack"
    return None


async def has_active_session_for_incident(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
) -> bool:
    """Return True when the incident already has a non-terminal session."""
    sessions = await SessionRepo.list_by_incident(db, org_id, incident_id)
    return any(session.status in _ACTIVE_SESSION_STATUSES for session in sessions)


async def provision_auto_started_session(
    app,
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    tier: int,
) -> None:
    """Provision and run an auto-started session outside incident intake."""
    try:
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
            if incident is None:
                logger.info(
                    "incident.auto_start: skipped deleted incident=%s",
                    incident_id,
                )
                return
            if await has_active_session_for_incident(db, org_id, incident_id):
                logger.info(
                    "incident.auto_start: skipped existing active session for incident=%s",
                    incident_id,
                )
                return
            admission = await admit_session(
                db,
                org_id,
                incident=incident,
                tier=tier,
                queue_ttl_seconds=app.state.config.sessions.queue_ttl_seconds,
            )
            session = admission.session
            await db.commit()

        logger.info(
            "incident.auto_start: provisioned incident=%s session=%s tier=%s status=%s",
            incident_id,
            session.id,
            tier,
            session.status,
        )
        if admission.queued and admission.created:
            from backend.bots.notifier import schedule_session_chat_event

            schedule_session_chat_event(
                app.state.session_factory,
                org_id=org_id,
                task_registry=app.state.background_tasks,
                event_type="session.queued",
                session_id=session.id,
            )
        elif admission.start_required:
            await dispatch_session_ready(app, session.id)
    except Exception:
        logger.exception(
            "incident.auto_start: provisioning_failed incident=%s tier=%s",
            incident_id,
            tier,
        )


def schedule_auto_started_session(
    app,
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    tier: int,
) -> None:
    """Queue session provisioning without blocking the incident request."""
    task = asyncio.create_task(
        provision_auto_started_session(
            app,
            org_id=org_id,
            incident_id=incident_id,
            tier=tier,
        ),
        name=f"incident-auto-start:{incident_id}",
    )
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)


async def cancel_auto_start_for_incident(
    app,
    *,
    incident_id: uuid.UUID,
) -> None:
    """Cancel queued auto-start provisioning before deleting an incident."""
    name = f"incident-auto-start:{incident_id}"
    tasks = [
        task
        for task in list(app.state.background_tasks)
        if not task.done() and task.get_name() == name
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
