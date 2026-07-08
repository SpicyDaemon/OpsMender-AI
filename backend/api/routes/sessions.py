"""Session endpoints.

POST /sessions                      — start a new incident response session
GET  /sessions/{id}                 — get session details
GET  /sessions/{id}/messages        — list co-pilot chat messages
POST /sessions/{id}/messages        — append a user message + fire async reply
POST /sessions/{id}/rollback        — replay compensating inverses (Sprint 17)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_current_session_factory, get_db, get_mcp_pool
from backend.api.routes.ws import publish
from backend.api.session_runner import (
    cancel_session_workflow,
    schedule_session_workflow,
)
from backend.api.schemas import (
    ModelCapacityRow,
    OrchestrationOverview,
    OrchestrationSession,
    QueueReprioritizeRequest,
    RollbackStepResponse,
    SessionCreate,
    SessionListResponse,
    SessionMessageCreate,
    SessionMessageListResponse,
    SessionMessageResponse,
    SessionOverrideRequest,
    SessionResponse,
    SessionRollbackRequest,
    SessionRollbackResponse,
    WSMessage,
)
from backend.audit.pg_logger import PgAuditLogger
from backend.chat import respond_to_user_message
from backend.config_loader import Config
from backend.db.models import User
from backend.db.repos import (
    ApprovalRequestRepo,
    AuditEntryRepo,
    IncidentAssignmentRepo,
    IncidentRepo,
    MCPServerRepo,
    ModelConfigRepo,
    SessionMessageRepo,
    SessionRepo,
    SkillRepo,
    WorkflowProfileRepo,
)
from backend.mcp.client import list_tools as mcp_list_tools
from backend.mcp.pool import MCPServerPool
from backend.skills.parser import loads as load_skill_def
from backend.tiers.enforcement import normalize_tier
from backend.tiers.resolution import resolve_session_tier_for_incident
from backend.tiers.sandbox import Tier0Sandbox
from backend.bots.notifier import schedule_session_chat_event
from backend.llm.selection import choose_model_for_incident_service
from backend.services.session_orchestration import (
    admit_session,
    dispatch_session_ready,
    schedule_queue_drain,
)
from backend.workflow.rollback import (
    reconstruct_tool_calls,
    replay_compensating_inverses,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tier0_max_session_seconds() -> int:
    try:
        return Config.load().tier0.max_session_seconds
    except (FileNotFoundError, ValueError):
        return 600


def _to_session_response(
    session, *, capacity_warning: str | None = None
) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        incident_id=session.incident_id,
        workflow_profile_id=getattr(session, "workflow_profile_id", None),
        model_config_id=getattr(session, "model_config_id", None),
        tier=session.tier,
        model_provider=session.model_provider,
        model_id=session.model_id,
        status=session.status,
        summary=session.summary,
        queued_at=getattr(session, "queued_at", None),
        queue_expires_at=getattr(session, "queue_expires_at", None),
        queue_reason=getattr(session, "queue_reason", None),
        force_started=bool(getattr(session, "force_started", False)),
        capacity_warning=capacity_warning,
        started_at=session.started_at,
        ended_at=session.ended_at,
        tier0_max_session_seconds=_tier0_max_session_seconds()
        if int(session.tier) == 0
        else None,
    )


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new session",
)
async def create_session(
    body: SessionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = None
    # Validate linked incident exists (if provided)
    if body.incident_id is not None:
        incident = await IncidentRepo.get_by_id(db, org_id, body.incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Incident not found",
            )

    workflow_profile_id = body.workflow_profile_id
    if workflow_profile_id is not None:
        profile = await WorkflowProfileRepo.get_by_id(db, org_id, workflow_profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow profile not found",
            )
        if not profile.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workflow profile is inactive",
            )
    else:
        default_profile = await WorkflowProfileRepo.get_default(db, org_id)
        workflow_profile_id = None if default_profile is None else default_profile.id

    resolved_tier = await resolve_session_tier_for_incident(
        db,
        org_id,
        request.app.state.config,
        incident=incident,
        requested_tier=body.tier,
    )

    # ACK gate: a Tier 1 / Tier 2 session linked to an incident may only be
    # started once an operator has acknowledged the incident (which records an
    # active assignment). Tier 0 sessions auto-start without an ack.
    if incident is not None and resolved_tier in (1, 2):
        assignment = await IncidentAssignmentRepo.get_active(
            db, org_id, incident.id
        )
        if assignment is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Acknowledge the incident before starting a Tier "
                    f"{resolved_tier} AI session."
                ),
            )

    try:
        admission = await admit_session(
            db,
            org_id,
            incident=incident,
            tier=resolved_tier,
            workflow_profile_id=workflow_profile_id,
            requested_provider=body.model_provider,
            requested_model_id=body.model_id,
            force=body.force,
            actor_user_id=user.id,
            queue_ttl_seconds=request.app.state.config.sessions.queue_ttl_seconds,
            takeover_existing=True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    session = admission.session

    if admission.takeover:
        session.tier = resolved_tier
        session.workflow_profile_id = workflow_profile_id
        if session.status in _RUNNING_STATUSES:
            cancel_session_workflow(request.app, session_id=session.id)
            await _expire_pending_approvals(db, org_id, session.id)
        if incident is not None and session.status != "queued":
            await IncidentAssignmentRepo.assign(
                db,
                org_id,
                incident_id=incident.id,
                user_id=user.id,
                assigned_by="session_takeover",
            )
        await AuditEntryRepo.create(
            db,
            org_id,
            session_id=session.id,
            tier=resolved_tier,
            entry_type="session_takeover",
            result={"actor_user_id": str(user.id)},
            permitted=True,
        )

    briefing = (body.initial_briefing or "").strip()
    briefing_message_id: uuid.UUID | None = None
    if briefing:
        msg = await SessionMessageRepo.create(
            db,
            org_id,
            session_id=session.id,
            role="user",
            content=briefing,
            node_context="initial_briefing",
        )
        briefing_message_id = msg.id

    await db.commit()
    await db.refresh(session)
    # ``refresh`` opens a new read transaction. Close it before returning so
    # the in-memory SQLite test harness cannot roll it back over a following
    # request on the shared connection.
    await db.commit()

    if admission.created or admission.start_required:
        if admission.started_from_queue:
            event_type = "session.started_from_queue"
        elif admission.queued:
            event_type = "session.queued"
        else:
            event_type = "session.created"
        schedule_session_chat_event(
            request.app.state.session_factory,
            org_id=org_id,
            task_registry=request.app.state.background_tasks,
            event_type=event_type,
            session_id=session.id,
            actor_user_id=user.id,
            base_url=os.environ.get("OPSMENDER_PUBLIC_URL"),
        )

    # If a briefing was provided, fire the responder so the chat has an
    # assistant reply waiting by the time the UI connects.
    if briefing_message_id is not None:
        factory = get_current_session_factory()
        asyncio.create_task(
            respond_to_user_message(
                factory,
                org_id=org_id,
                session_id=session.id,
                user_message_id=briefing_message_id,
            )
        )

    if admission.start_required:
        await dispatch_session_ready(request.app, session.id)

    return _to_session_response(
        session,
        capacity_warning=admission.warning,
    )


@router.get(
    "",
    response_model=SessionListResponse,
    summary="List sessions for the active organization",
)
async def list_sessions(
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Sprint 59 / UX-direction Sprint C: powers the dashboard
    Attention Queue's "active sessions" + "failed sessions" panels.

    Optional ``status_filter`` accepts any value the ``Session.status``
    column carries: ``active`` / ``awaiting_approval`` / ``completed``
    / ``failed`` / ``timed_out``. When unset, returns the most-recent
    sessions across all statuses.
    """

    items = await SessionRepo.list_all(
        db, org_id, status=status_filter, limit=limit, offset=offset
    )
    rows = [_to_session_response(s) for s in items]
    return SessionListResponse(items=rows, total=len(rows))


@router.get(
    "/orchestration",
    response_model=OrchestrationOverview,
    summary="AI session orchestration overview (per-model occupancy + queue)",
)
async def orchestration_overview(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """v2 Phase 3: per-model concurrency occupancy plus the currently running and
    queued AI sessions (with the reason each is queued). Read-only."""
    configs = await ModelConfigRepo.list_all(db, org_id)
    occupancy = await SessionRepo.active_occupancy_by_model_config(db, org_id)
    name_by_id = {c.id: c.name for c in configs}
    models = [
        ModelCapacityRow(
            model_config_id=c.id,
            name=c.name,
            provider=c.provider,
            model_id=c.model_id,
            max_concurrent_sessions=c.max_concurrent_sessions,
            running=int(occupancy.get(c.id, 0)),
        )
        for c in configs
        if c.is_active
    ]

    active: list[OrchestrationSession] = []
    queued: list[OrchestrationSession] = []
    for session, incident in await SessionRepo.list_live_with_incident(db, org_id):
        row = OrchestrationSession(
            session_id=session.id,
            incident_id=session.incident_id,
            incident_title=getattr(incident, "title", None),
            priority=getattr(incident, "priority", None),
            status=session.status,
            tier=session.tier,
            model_config_id=session.model_config_id,
            model_name=name_by_id.get(session.model_config_id),
            queued_at=session.queued_at,
            queue_expires_at=session.queue_expires_at,
            queue_reason=session.queue_reason,
            queue_rank=session.queue_rank,
            force_started=session.force_started,
            started_at=session.started_at,
        )
        (queued if session.status == "queued" else active).append(row)

    return OrchestrationOverview(
        models=models,
        active_sessions=active,
        queued_sessions=queued,
        active_total=len(active),
        queued_total=len(queued),
    )


@router.post(
    "/queue/purge",
    summary="Cancel every queued AI session (admin)",
)
async def purge_session_queue(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    """v2 queue admin — cancel all queued sessions at once."""
    count = await SessionRepo.purge_queue(
        db, org_id, reason=f"Queue purged by {user.username}"
    )
    await db.commit()
    return {"cancelled": count}


@router.post(
    "/{session_id}/queue/cancel",
    summary="Cancel a single queued AI session (admin/operator)",
)
async def cancel_queued_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    ok = await SessionRepo.cancel_queued_session(
        db, org_id, session_id, reason=f"Removed from queue by {user.username}"
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session is not queued",
        )
    await db.commit()
    return {"cancelled": True}


@router.post(
    "/{session_id}/queue/reprioritize",
    summary="Move a queued session within the queue (admin)",
)
async def reprioritize_queued_session(
    session_id: uuid.UUID,
    body: QueueReprioritizeRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    """Move a queued session to the front or back of the drain order. Ranked
    sessions drain before unranked ones (overriding P0→P3 + FIFO)."""
    min_rank, max_rank = await SessionRepo.queue_rank_bounds(db, org_id)
    if body.direction in ("front", "up"):
        rank = (min_rank if min_rank is not None else 0) - 1
    else:  # back / down
        rank = (max_rank if max_rank is not None else 0) + 1
    ok = await SessionRepo.set_queue_rank(db, org_id, session_id, rank=rank)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Session is not queued"
        )
    await db.commit()
    return {"queue_rank": rank}


@router.post(
    "/{session_id}/queue/force-start",
    response_model=SessionResponse,
    summary="Force-start a queued session now, bypassing capacity (admin)",
)
async def force_start_queued_session(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    """Soft override: admit a queued session immediately even if its model is at
    capacity. Audited; counts toward occupancy for later allocations."""
    session = await SessionRepo.get_by_id(db, org_id, session_id)
    if session is None or session.status != "queued":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Session is not queued"
        )
    incident = (
        await IncidentRepo.get_by_id(db, org_id, session.incident_id)
        if session.incident_id is not None
        else None
    )
    model = await choose_model_for_incident_service(
        db,
        org_id,
        service_id=incident.service_id if incident is not None else None,
        ingestion_model_config_id=(
            incident.ingestion_model_config_id if incident is not None else None
        ),
    )
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No active model is configured to start this session",
        )
    occupancy = (
        await SessionRepo.active_occupancy_by_model_config(db, org_id)
    ).get(model.id, 0)
    cap = int(model.max_concurrent_sessions or 0)
    session.status = "active"
    session.model_config_id = model.id
    session.model_provider = model.provider
    session.model_id = model.model_id
    session.started_at = _utcnow()
    session.queue_reason = None
    session.queue_rank = None
    session.force_started = True
    session.force_started_by = user.id
    session.force_start_occupancy = occupancy
    session.force_start_cap = cap
    await db.commit()
    await dispatch_session_ready(request.app, session.id)
    refreshed = await SessionRepo.get_by_id(db, org_id, session_id)
    return _to_session_response(refreshed)


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get session details",
)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    session = await SessionRepo.get_by_id(db, org_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return _to_session_response(session)


# ---------------------------------------------------------------------------
# Intercept — Stop / Override a running session (admin or operator)
# ---------------------------------------------------------------------------

_RUNNING_STATUSES = {"active", "awaiting_approval"}
_STOPPABLE_STATUSES = {"queued", *_RUNNING_STATUSES}


async def _expire_pending_approvals(
    db: AsyncSession, org_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    """Resolve any dangling pending approval requests for a session.

    Used when a session is intercepted so a paused approval prompt does not
    linger as ``pending`` after the workflow it belonged to has been aborted.
    """
    pending = await ApprovalRequestRepo.list_pending(db, org_id, session_id=session_id)
    for req in pending:
        await ApprovalRequestRepo.resolve(db, org_id, req.id, status="expired")


@router.post(
    "/{session_id}/stop",
    response_model=SessionResponse,
    summary="Stop a running session (intercept)",
)
async def stop_session(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Hard-abort a live AI session. The AI stops immediately and the operator
    takes over manually elsewhere. An in-flight tool call may still complete
    server-side — use the Tier 0 auto-rollback / manual rollback paths if it
    must be reverted."""
    session = await SessionRepo.get_by_id(db, org_id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.status not in _STOPPABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is not running (status={session.status})",
        )

    was_queued = session.status == "queued"
    cancel_session_workflow(request.app, session_id=session_id)
    await _expire_pending_approvals(db, org_id, session_id)
    session.status = "cancelled" if was_queued else "stopped"
    session.ended_at = _utcnow()
    await db.flush()
    if was_queued:
        from backend.services.session_orchestration import notify_capacity_event

        await notify_capacity_event(
            db,
            org_id,
            session=session,
            event_type="session.queue_cancelled",
            title="Queued AI session cancelled",
            body=f"Cancelled by {user.username}.",
        )
    await db.commit()
    if was_queued or session.model_config_id is not None:
        schedule_queue_drain(request.app, org_id=org_id)
    if was_queued:
        schedule_session_chat_event(
            request.app.state.session_factory,
            org_id=org_id,
            task_registry=request.app.state.background_tasks,
            event_type="session.queue_cancelled",
            session_id=session_id,
            actor_user_id=user.id,
            base_url=os.environ.get("OPSMENDER_PUBLIC_URL"),
        )

    await publish(
        session_id,
        WSMessage(
            type="session_end",
            data={
                "status": (
                    "cancelled" if was_queued else "stopped"
                ),
                "summary": f"Session stopped by {user.username}",
                "stopped_by": str(user.id),
            },
        ),
    )
    refreshed = await SessionRepo.get_by_id(db, org_id, session_id)
    return _to_session_response(refreshed if refreshed is not None else session)


@router.post(
    "/{session_id}/override",
    response_model=SessionResponse,
    summary="Override a running session into Tier 1 / Tier 2 (intercept)",
)
async def override_session(
    session_id: uuid.UUID,
    body: SessionOverrideRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Stop the AI's current autonomy and continue the *same* session under
    operator control at a less-autonomous tier (convert in place).

    The session row is reused — its tier flips to the chosen Tier 1 (Approval
    Required) or Tier 2 (Advisory Only) and the workflow is re-run under
    operator supervision. Override can only *reduce* autonomy, so the target
    tier must be strictly less autonomous than the current tier (a larger tier
    number); Tier 2 sessions cannot be overridden further.
    """
    target_tier = normalize_tier(body.tier)
    session = await SessionRepo.get_by_id(db, org_id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.status not in _RUNNING_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is not running (status={session.status})",
        )
    if target_tier <= normalize_tier(int(session.tier)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Override can only reduce autonomy. Choose a tier less "
                f"autonomous than the current Tier {normalize_tier(int(session.tier))}."
            ),
        )

    cancel_session_workflow(request.app, session_id=session_id)
    await _expire_pending_approvals(db, org_id, session_id)

    # Convert in place: same session row, new tier, back to active so the
    # re-run workflow drives under operator supervision.
    await SessionRepo.set_status(db, org_id, session_id, status="active")
    session.tier = target_tier

    # A human is now in control — record them as the incident assignee so the
    # incident is acknowledged/owned (mirrors the Tier 1/2 ack-then-start gate).
    if session.incident_id is not None:
        await IncidentAssignmentRepo.assign(
            db,
            org_id,
            incident_id=session.incident_id,
            user_id=user.id,
            assigned_by="override",
        )
    await db.commit()

    await publish(
        session_id,
        WSMessage(
            type="session_overridden",
            data={
                "status": "active",
                "tier": target_tier,
                "overridden_by": str(user.id),
            },
        ),
    )

    schedule_session_workflow(request.app, session_id=session_id)
    refreshed = await SessionRepo.get_by_id(db, org_id, session_id)
    return _to_session_response(refreshed if refreshed is not None else session)


# ---------------------------------------------------------------------------
# Co-pilot chat
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/messages",
    response_model=SessionMessageListResponse,
    summary="List co-pilot chat messages for a session",
)
async def list_session_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    session = await SessionRepo.get_by_id(db, org_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    items = await SessionMessageRepo.list_by_session(db, org_id, session_id)
    return SessionMessageListResponse(
        items=[SessionMessageResponse.model_validate(m) for m in items],
        total=len(items),
    )


@router.post(
    "/{session_id}/messages",
    response_model=SessionMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send a user message to the co-pilot",
)
async def create_session_message(
    session_id: uuid.UUID,
    body: SessionMessageCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    session = await SessionRepo.get_by_id(db, org_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    message = await SessionMessageRepo.create(
        db,
        org_id,
        session_id=session_id,
        role="user",
        content=body.content,
    )
    await db.commit()
    persisted_message = await SessionMessageRepo.get_by_id(db, org_id, message.id)
    if persisted_message is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist session message",
        )
    message = persisted_message

    # Push the user event immediately so other connected clients see it.
    await publish(
        session_id,
        WSMessage(
            type="chat_message_user",
            data={
                "id": str(message.id),
                "session_id": str(session_id),
                "role": "user",
                "content": message.content,
                "created_at": message.created_at.isoformat(),
                "node_context": message.node_context,
            },
        ),
    )

    # Fire the assistant reply in the background — the route returns
    # immediately so the UI can optimistically render the user bubble.
    factory = get_current_session_factory()
    asyncio.create_task(
        respond_to_user_message(
            factory,
            org_id=org_id,
            session_id=session_id,
            user_message_id=message.id,
        )
    )

    return message


# ---------------------------------------------------------------------------
# Rollback (Sprint 17 — Tier 0 sandbox)
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/rollback",
    response_model=SessionRollbackResponse,
    summary="Roll back a session's executed operations (admin-only)",
)
async def rollback_session(
    session_id: uuid.UUID,
    body: SessionRollbackRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
    pool: MCPServerPool = Depends(get_mcp_pool),
):
    session = await SessionRepo.get_by_id(db, org_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    if not body.dry_run and not body.mcp_server:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mcp_server is required unless dry_run is true",
        )

    entries = await AuditEntryRepo.list_by_session(db, org_id, session_id)
    tool_calls = reconstruct_tool_calls(entries)
    if not tool_calls:
        return SessionRollbackResponse(
            session_id=session_id,
            dry_run=body.dry_run,
            attempted=0,
            succeeded=0,
            failed=0,
            skipped=0,
            steps=[],
        )

    # -- Resolve the skill bound to the chosen MCP server --------------
    server_row = None
    if body.mcp_server:
        server_row = await MCPServerRepo.get_by_name(db, org_id, body.mcp_server)
        if server_row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MCP server not found: {body.mcp_server}",
            )
    skill_row = await SkillRepo.get_for_mcp_server(
        db, org_id, server_row.id if server_row else None
    )
    if skill_row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No skill definition available — cannot resolve compensating inverses",
        )
    skill_def = load_skill_def(skill_row.content_md)

    # -- Dry-run: resolve inverses without calling MCP -----------------
    if body.dry_run:

        async def _noop(_tool, _params):
            return None

        report = await replay_compensating_inverses(
            session_id=str(session_id),
            tier=int(session.tier),
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=_noop,
            audit_logger=None,
        )
        return SessionRollbackResponse(
            session_id=session_id,
            dry_run=True,
            attempted=report.attempted,
            succeeded=report.succeeded,
            failed=report.failed,
            skipped=report.skipped,
            steps=[
                RollbackStepResponse(
                    original_tool=s.original_tool,
                    inverse_tool=s.inverse_tool,
                    parameters=s.parameters,
                    status=s.status,
                    error=s.error,
                )
                for s in report.steps
            ],
        )

    # -- Live rollback: spawn an MCP session + use the Tier 0 sandbox --
    logger = PgAuditLogger(db, org_id)
    async with pool.connect(org_id, body.mcp_server) as mcp_session:
        tools = await mcp_list_tools(mcp_session)
        sandbox = Tier0Sandbox.from_skill(skill_def, available_tools=tools)

        async def _caller(tool_name: str, params: dict):
            # Tier 0 sandbox guards even the rollback path.
            return await sandbox.call_tool(mcp_session, tool_name, params)

        report = await replay_compensating_inverses(
            session_id=str(session_id),
            tier=int(session.tier),
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=_caller,
            audit_logger=logger,
        )
    await db.commit()

    return SessionRollbackResponse(
        session_id=session_id,
        dry_run=False,
        attempted=report.attempted,
        succeeded=report.succeeded,
        failed=report.failed,
        skipped=report.skipped,
        steps=[
            RollbackStepResponse(
                original_tool=s.original_tool,
                inverse_tool=s.inverse_tool,
                parameters=s.parameters,
                status=s.status,
                error=s.error,
            )
            for s in report.steps
        ],
    )
