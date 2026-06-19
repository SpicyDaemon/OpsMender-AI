"""Incident endpoints.

POST /incidents        — create a new incident
GET  /incidents        — list incidents (filterable, paginated)
GET  /incidents/{id}   — get single incident with sessions
"""

from __future__ import annotations

import os
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    DEFAULT_POSTMORTEM_TEMPLATE,
    IncidentCommentCreate,
    IncidentCommentListResponse,
    IncidentCommentResponse,
    IncidentCreate,
    IncidentCreateResponse,
    FireTestIncidentRequest,
    FireTestIncidentResponse,
    IncidentListResponse,
    IncidentPostmortemResponse,
    IncidentPostmortemUpdate,
    IncidentResponse,
    PostmortemMemoryCandidate,
    PostmortemMemoryCandidatesResponse,
    IncidentTimelineItemResponse,
    IncidentTimelineResponse,
    IncidentUpdate,
    SessionListResponse,
    SessionResponse,
)
from backend.config_loader import Config
from backend.db.models import User
from backend.db.repos import (
    AuditEntryRepo,
    BotConnectorRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentCommentRepo,
    IncidentMemoryRepo,
    IncidentNotificationReceiptRepo,
    IncidentPageRepo,
    IncidentRepo,
    IngestLogRepo,
    MaintenanceWindowRepo,
    SessionRepo,
    ServiceRepo,
    SkillRepo,
    TeamRepo,
    UserRepo,
)
from backend.notifications import (
    CATEGORY_INCIDENT,
    CATEGORY_MENTION,
    emit_notification,
    parse_mentions,
)
from backend.api.schemas import (
    IncidentAssignmentResponse,
    IncidentAssignRequest,
    IncidentBulkActionRequest,
    IncidentBulkActionResponse,
    IncidentBulkActionResult,
    IncidentCombineRequest,
    IncidentCombineResponse,
    IncidentPagingPanelResponse,
    SuppressedByMaintenanceWindow,
)
from backend.paging.service import compute_priority_for_payload
from backend.paging import escalation as _esc_kickoff
from backend.skills.parser import loads as load_skill_def_text
from backend.memory.candidates import candidate_title, extract_memory_candidates
from backend.api.session_runner import (
    cancel_session_workflows,
    stop_incident_sessions,
)
from backend.ingest.autostart import (
    auto_start_skip_reason,
    cancel_auto_start_for_incident,
    load_auto_start_policy,
    schedule_auto_started_session,
)
from backend.llm.selection import choose_model_for_incident_service

import logging

router = APIRouter(prefix="/incidents", tags=["incidents"])

_log = logging.getLogger(__name__)


async def _notify_channels(
    db: AsyncSession,
    incident_id: uuid.UUID,
    org_id: uuid.UUID,
    event_type: str,
) -> None:
    """Best-effort: post an incident card to enabled Notification Channels.

    Must be called *after* the incident change is committed so the background
    delivery (which opens its own DB session) sees the new state.

    We first check — on the already-committed request session — whether any
    enabled channel even wants notifications. Only then do we spawn the
    fire-and-forget delivery task. This keeps the hot path cheap and, crucially,
    avoids opening a second concurrent session when there is nothing to deliver.
    Never raises into the request path — channel delivery is non-critical.
    """
    try:
        connectors = await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        wants = any(
            "notifications" in (c.allowed_capabilities or []) for c in connectors
        )
        if not wants:
            return

        from backend.api.deps import get_current_session_factory
        from backend.bots.notifier import schedule_incident_event

        factory = get_current_session_factory()
        schedule_incident_event(
            factory,
            org_id=org_id,
            event_type=event_type,
            incident_id=incident_id,
            base_url=os.environ.get("OPSMENDER_PUBLIC_URL"),
        )
    except Exception:  # pragma: no cover - delivery is best-effort
        _log.warning("incident channel notify skipped", exc_info=True)


def _tier0_max_session_seconds() -> int:
    try:
        return Config.load().tier0.max_session_seconds
    except (FileNotFoundError, ValueError):
        return 600


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _to_session_response(session) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        incident_id=session.incident_id,
        workflow_profile_id=getattr(session, "workflow_profile_id", None),
        agent_team_profile_id=getattr(session, "agent_team_profile_id", None),
        tier=session.tier,
        model_provider=session.model_provider,
        model_id=session.model_id,
        status=session.status,
        summary=session.summary,
        started_at=session.started_at,
        ended_at=session.ended_at,
        tier0_max_session_seconds=_tier0_max_session_seconds()
        if int(session.tier) == 0
        else None,
    )


def _assignment_title(assigned_by: str) -> str:
    if assigned_by == "self_ack":
        return "Ownership acknowledged"
    if assigned_by == "admin_force":
        return "Ownership force-taken"
    if assigned_by == "manual":
        return "Ownership reassigned"
    return "Ownership updated"


def _assignment_body(assigned_by: str, actor_label: str | None) -> str | None:
    if actor_label is None:
        return None
    if assigned_by == "self_ack":
        return f"{actor_label} acknowledged the page and became the current owner."
    if assigned_by == "admin_force":
        return f"{actor_label} force-took the incident from the command surface."
    if assigned_by == "manual":
        return f"{actor_label} was assigned as the current owner."
    return f"{actor_label} became the current owner."


def _user_display(user) -> tuple[str | None, str | None]:
    """(display_name, email) for a user, or (None, None) when the user is
    missing or soft-deleted — the frontend then renders 'Deleted user <id>'."""
    if user is None or getattr(user, "deleted_at", None) is not None:
        return None, None
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return (full or user.username), user.email


# AI session statuses that count as "in progress" for the list indicator.
_IN_PROGRESS_SESSION_STATUSES = {"active", "awaiting_approval"}


def _resolve_ai_session(sessions) -> tuple[bool, str | None]:
    """Pick the representative AI-session state for an incident.

    ``sessions`` is the incident's sessions newest-first. An in-progress session
    (`active` / `awaiting_approval`) wins; otherwise the most recent session's
    status is reported. Returns ``(ai_session_active, ai_session_status)`` —
    ``(False, None)`` when the incident has never had a session.
    """
    if not sessions:
        return False, None
    in_progress = next(
        (s for s in sessions if s.status in _IN_PROGRESS_SESSION_STATUSES),
        None,
    )
    if in_progress is not None:
        return True, in_progress.status
    return False, sessions[0].status


def _resolve_responder_from(
    assignment,
    pages,
    user_by_id: dict,
) -> dict:
    """Resolve responder/assignment state from already-fetched rows.

    Acknowledged (active assignment) wins; otherwise the latest escalation page
    is the current target — ``awaiting`` at the first level, ``escalated`` after.
    ``pages`` must be ordered oldest-first (latest page last).
    """
    latest = pages[-1] if pages else None

    ack_uid = assignment.assigned_to if assignment is not None else None
    esc_uid = latest.user_id if latest is not None else None
    esc_step = latest.step_index if latest is not None else None

    if ack_uid is not None:
        state, resp_uid = "assigned", ack_uid
    elif esc_uid is not None:
        state = "escalated" if (esc_step or 0) > 0 else "awaiting"
        resp_uid = esc_uid
    else:
        state, resp_uid = "unassigned", None

    resp_name, resp_email = _user_display(user_by_id.get(resp_uid))
    ack_name, _ = _user_display(user_by_id.get(ack_uid))
    esc_name, _ = _user_display(user_by_id.get(esc_uid))
    return {
        "responder_user_id": resp_uid,
        "responder_display_name": resp_name,
        "responder_email": resp_email,
        "responder_state": state,
        "acknowledged_by_user_id": ack_uid,
        "acknowledged_by_display_name": ack_name,
        "escalated_to_user_id": esc_uid,
        "escalated_to_display_name": esc_name,
    }


async def _to_incident_response(
    db: AsyncSession, org_id: uuid.UUID, incident
) -> IncidentResponse:
    data = IncidentResponse.model_validate(incident).model_dump()
    if incident.service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, incident.service_id)
        if service is not None:
            data["service_name"] = service.name
            data["team_id"] = service.team_id
            team = await TeamRepo.get_by_id(db, org_id, service.team_id)
            data["team_name"] = team.name if team is not None else None
    # include_deleted=True: historical responder references must render a
    # fallback display (e.g. "deleted_user-<id>") rather than crashing.
    user_by_id = {u.id: u for u in await UserRepo.list_all(db, limit=1000, include_deleted=True)}
    assignment = await IncidentAssignmentRepo.get_active(db, org_id, incident.id)
    pages = list(await IncidentPageRepo.list_for_incident(db, org_id, incident.id))
    data.update(_resolve_responder_from(assignment, pages, user_by_id))
    sessions = await SessionRepo.list_by_incident(db, org_id, incident.id)
    active, status = _resolve_ai_session(sessions)
    data["ai_session_active"] = active
    data["ai_session_status"] = status
    return IncidentResponse(**data)


async def _to_incident_list_response(
    db: AsyncSession, org_id: uuid.UUID, incidents
) -> list[IncidentResponse]:
    services = await ServiceRepo.list_all(db, org_id)
    teams = await TeamRepo.list_all(db, org_id)
    service_by_id = {service.id: service for service in services}
    team_by_id = {team.id: team for team in teams}
    user_by_id = {u.id: u for u in await UserRepo.list_all(db, limit=1000, include_deleted=True)}
    # Batch all per-incident lookups into one query each instead of N+1.
    incident_ids = [incident.id for incident in incidents]
    assignment_by_incident = await IncidentAssignmentRepo.get_active_for_incidents(
        db, org_id, incident_ids
    )
    pages_by_incident = await IncidentPageRepo.list_for_incidents(
        db, org_id, incident_ids
    )
    sessions_by_incident = await SessionRepo.list_for_incidents(
        db, org_id, incident_ids
    )
    responses: list[IncidentResponse] = []
    for incident in incidents:
        data = IncidentResponse.model_validate(incident).model_dump()
        if incident.service_id is not None:
            service = service_by_id.get(incident.service_id)
            if service is not None:
                data["service_name"] = service.name
                data["team_id"] = service.team_id
                team = team_by_id.get(service.team_id)
                data["team_name"] = team.name if team is not None else None
        data.update(
            _resolve_responder_from(
                assignment_by_incident.get(incident.id),
                pages_by_incident.get(incident.id, []),
                user_by_id,
            )
        )
        active, status = _resolve_ai_session(
            sessions_by_incident.get(incident.id, [])
        )
        data["ai_session_active"] = active
        data["ai_session_status"] = status
        responses.append(IncidentResponse(**data))
    return responses


async def _create_incident_record(
    db: AsyncSession,
    org_id: uuid.UUID,
    body: IncidentCreate,
):
    service = None
    if body.service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, body.service_id)
        if service is None or not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manual incidents must be linked to an active service.",
            )

    selected_model = await choose_model_for_incident_service(
        db,
        org_id,
        service_id=body.service_id,
    )

    # v1 priority comes from the selected service when one is present.
    # Synthetic test incidents may remain unbound and use severity mapping.
    payload = {
        "title": body.title,
        "description": body.description,
        "severity": body.severity,
        "source": body.external_source,
    }
    priority_result = await compute_priority_for_payload(
        db,
        org_id,
        payload,
        service_id=body.service_id,
    )
    incident = await IncidentRepo.create(
        db,
        org_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
        priority=priority_result.priority,
        response_mode=priority_result.response_mode,
        service_id=body.service_id,
        ingestion_model_config_id=(
            None if selected_model is None else selected_model.id
        ),
        external_id=body.external_id,
        external_source=body.external_source,
    )
    # Kick off the escalation chain when the response mode pages humans.
    if priority_result.response_mode in ("page", "escalate_immediate"):
        link = await _esc_kickoff.select_chain_for_incident(
            db,
            org_id,
            service_id=incident.service_id,
            priority=priority_result.priority,
        )
        if link is not None:
            from backend.paging.channel_factory import build_channel_factory

            await _esc_kickoff.start_chain(
                db,
                org_id,
                incident_id=incident.id,
                chain_id=link.chain_id,
                mode=priority_result.response_mode,
                channel_factory=build_channel_factory(),
            )
            from backend.paging.slack_channel_mirror import (
                mirror_incident_to_slack_channel,
            )

            await mirror_incident_to_slack_channel(
                db,
                org_id,
                incident=incident,
                base_url=os.environ.get("OPSMENDER_PUBLIC_URL"),
            )
    return incident


@router.post(
    "",
    response_model=IncidentCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new incident (auto-starts a T0 session; T1/T2 wait for ACK)",
)
async def create_incident(
    body: IncidentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if body.service_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manual incidents must be linked to an active service.",
        )
    incident = await _create_incident_record(db, org_id, body)
    await db.commit()
    await _notify_channels(db, incident.id, org_id, "incident.created")
    auto_status, reason, tier = await _resolve_auto_start_on_create(
        request, db, org_id, incident
    )
    inc_resp = await _to_incident_response(db, org_id, incident)
    return IncidentCreateResponse(
        **inc_resp.model_dump(),
        resolved_tier=tier,
        auto_start_status=auto_status,
        auto_start_reason=reason,
        auto_start_message=_auto_start_message(
            context="created", tier=tier, status=auto_status, reason=reason
        ),
    )


_TIER_LABEL = {
    0: "T0 — Autonomous",
    1: "T1 — Approval Required",
    2: "T2 — Advisory Only",
}
_AUTO_START_REASON_PRETTY = {
    "no_enabled_model": "no enabled model configured",
}


def _auto_start_message(
    *, context: str, tier: int, status: str, reason: str | None
) -> str:
    """User-facing toast copy for an auto-start outcome.

    ``context`` is ``"created"`` (incident creation) or ``"acknowledged"``.
    """
    label = _TIER_LABEL.get(tier, f"T{tier}")
    verb = "created" if context == "created" else "acknowledged"
    if status == "queued":
        return f"Incident {verb}. AI session auto-started under {label}."
    if status == "failed":
        pretty = _AUTO_START_REASON_PRETTY.get(reason or "", reason or "unknown error")
        return f"Incident {verb}. AI session auto-start failed: {pretty}."
    # status == "skipped"
    if reason == "auto_start_deferred_to_ack":
        return (
            "Incident created. Acknowledge it, then start the AI session "
            f"(starts under {label})."
        )
    if reason == "auto_start_deferred_to_manual_start":
        return f"Incident acknowledged. Start the AI session when ready ({label})."
    return f"Incident {verb}."


async def _resolve_auto_start_on_create(
    request: Request, db: AsyncSession, org_id: uuid.UUID, incident
) -> tuple[str, str | None, int]:
    """Decide + (for T0) schedule an AI session for a freshly created incident.

    Returns ``(status, reason, tier)``: ``queued`` (T0 session scheduled),
    ``skipped`` (T1/T2 → waits for ACK), or ``failed`` (e.g. no model). Never
    raises — incident creation must succeed regardless of the auto-start outcome.
    """
    try:
        policy = await load_auto_start_policy(
            db, org_id, request.app.state.config, incident=incident
        )
        tier = policy.session_tier
        skip = auto_start_skip_reason(incident, dedup_action="created", policy=policy)
        if skip is not None:
            return ("skipped", skip, tier)
        model = await choose_model_for_incident_service(
            db,
            org_id,
            service_id=incident.service_id,
            ingestion_model_config_id=getattr(
                incident, "ingestion_model_config_id", None
            ),
        )
        if model is None:
            _log.warning(
                "incident.auto_start: no_enabled_model incident=%s tier=%s",
                incident.id,
                tier,
            )
            return ("failed", "no_enabled_model", tier)
        schedule_auto_started_session(
            request.app, org_id=org_id, incident_id=incident.id, tier=tier
        )
        _log.info(
            "incident.auto_start: queued incident=%s tier=%s", incident.id, tier
        )
        return ("queued", None, tier)
    except Exception:  # noqa: BLE001 — never block incident creation
        _log.exception("incident.auto_start: resolve_failed incident=%s", incident.id)
        return ("failed", "auto_start_error", 2)


async def _resolve_auto_start_on_ack(
    request: Request, db: AsyncSession, org_id: uuid.UUID, incident
) -> tuple[str, str | None, int]:
    """Acknowledgment no longer auto-starts the AI session.

    Tier 1/2 sessions are started explicitly by an operator *after* acknowledging
    (the ACK gate in ``create_session`` enforces ack-first). A Tier 0 incident
    already started its session at creation. This returns a ``skipped`` status so
    the ACK response carries clear "now start the session" copy. Never raises.
    """
    try:
        policy = await load_auto_start_policy(
            db, org_id, request.app.state.config, incident=incident
        )
        return ("skipped", "auto_start_deferred_to_manual_start", policy.session_tier)
    except Exception:  # noqa: BLE001 — never block acknowledgment
        _log.exception(
            "incident.ack_auto_start: resolve_failed incident=%s", incident.id
        )
        return ("skipped", "auto_start_deferred_to_manual_start", 2)


@router.post(
    "/fire-test",
    response_model=FireTestIncidentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a synthetic incident and conditionally auto-start Tier 0",
)
async def fire_test_incident(
    body: FireTestIncidentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    service_name = None
    if body.service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, body.service_id)
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service not found",
            )
        service_name = service.name

    scope = f" for {service_name}" if service_name else ""
    incident = await _create_incident_record(
        db,
        org_id,
        IncidentCreate(
            title=f"TEST · synthetic alert{scope}",
            description=(
                f"Synthetic alert fired from the Incidents page{scope}. "
                "Use this to verify ingestion, paging, sessions, and operator "
                "workflow end to end."
            ),
            severity="high",
            service_id=body.service_id,
            external_id=f"test-{uuid.uuid4()}",
            external_source="opsmender-test",
        ),
    )
    await db.commit()
    await _notify_channels(db, incident.id, org_id, "incident.created")
    auto_status, reason, tier = await _resolve_auto_start_on_create(
        request, db, org_id, incident
    )
    return FireTestIncidentResponse(
        incident=await _to_incident_response(db, org_id, incident),
        resolved_tier=tier,
        auto_start_status=auto_status,
        auto_start_reason=reason,
        message=_auto_start_message(
            context="created", tier=tier, status=auto_status, reason=reason
        ),
    )


@router.get(
    "",
    response_model=IncidentListResponse,
    summary="List incidents",
)
async def list_incidents(
    status_filter: list[str] | None = Query(None, alias="status"),
    severity: list[str] | None = Query(None),
    service_id: uuid.UUID | None = Query(None),
    team_id: list[uuid.UUID] | None = Query(None),
    source: list[str] | None = Query(None),
    updated_from: datetime | None = Query(None),
    updated_to: datetime | None = Query(None),
    query: str | None = Query(None, alias="q"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    # Multi-value OR filters: repeated query params (e.g. ?status=open&status=in_progress).
    # Closed is no longer a lifecycle state; an all-invalid status query
    # intentionally returns no rows instead of broadening to the full list.
    requested_statuses = status_filter
    allowed_statuses = {"open", "in_progress", "resolved", "merged"}
    status_filter = [
        value for value in (status_filter or []) if value in allowed_statuses
    ] or None
    if requested_statuses and status_filter is None:
        return IncidentListResponse(items=[], total=0)

    # Drop unknown values so a stray value can't make the result empty.
    allowed_severities = {"critical", "high", "medium", "low"}
    allowed_sources = {"manual", "ingested"}
    severity = [s for s in (severity or []) if s in allowed_severities] or None
    source = [s for s in (source or []) if s in allowed_sources] or None

    # Merged (combined) incidents are folded away — hidden from the default list
    # unless the caller explicitly asks for status=merged.
    exclude_statuses = ["merged"]

    items = await IncidentRepo.list_all(
        db,
        org_id,
        status=status_filter,
        severity=severity,
        service_id=service_id,
        team_id=team_id,
        source=source,
        updated_from=updated_from,
        updated_to=updated_to,
        query=query,
        exclude_statuses=exclude_statuses,
        limit=limit,
        offset=offset,
    )
    total = await IncidentRepo.count_all(
        db,
        org_id,
        status=status_filter,
        severity=severity,
        service_id=service_id,
        team_id=team_id,
        source=source,
        updated_from=updated_from,
        updated_to=updated_to,
        query=query,
        exclude_statuses=exclude_statuses,
    )
    return IncidentListResponse(
        items=await _to_incident_list_response(db, org_id, items),
        total=total,
    )


@router.delete(
    "/{incident_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete an incident",
)
async def delete_incident(
    incident_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )
    await cancel_auto_start_for_incident(
        request.app,
        incident_id=incident_id,
    )
    sessions = await SessionRepo.list_by_incident(db, org_id, incident_id)
    await cancel_session_workflows(
        request.app,
        session_ids=[session.id for session in sessions],
    )
    await IncidentRepo.delete_permanently(db, org_id, incident_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{incident_id}/sessions",
    response_model=SessionListResponse,
    summary="List sessions for an incident",
)
async def list_incident_sessions(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    items = await SessionRepo.list_by_incident(db, org_id, incident_id)
    return SessionListResponse(
        items=[_to_session_response(item) for item in items],
        total=len(items),
    )


@router.get(
    "/{incident_id}",
    response_model=IncidentResponse,
    summary="Get a single incident",
)
async def get_incident(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
    )
    return await _to_incident_response(db, org_id, incident)


@router.patch(
    "/{incident_id}",
    response_model=IncidentResponse,
    summary="Update incident routing and lifecycle fields",
)
async def update_incident(
    incident_id: uuid.UUID,
    body: IncidentUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    # Capture the pre-update status now: update_fields re-selects the row and
    # can refresh this identity-mapped instance to the new status, which would
    # otherwise defeat the resolved-transition guard below.
    prior_status = incident.status
    service_changed = body.service_id_set and body.service_id != incident.service_id
    if body.service_id_set and body.service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, body.service_id)
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service not found",
            )

    updated = await IncidentRepo.update_fields(
        db,
        org_id,
        incident_id,
        status=body.status,
        severity=body.severity,
        service_id=body.service_id,
        service_id_set=body.service_id_set,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    if service_changed:
        await IncidentAssignmentRepo.release(db, org_id, incident_id)
        if updated.response_mode in ("page", "escalate_immediate"):
            link = await _esc_kickoff.select_chain_for_incident(
                db,
                org_id,
                service_id=updated.service_id,
                priority=updated.priority,
            )
            if link is not None:
                from backend.paging.channel_factory import build_channel_factory

                await _esc_kickoff.restart_chain_for_handoff(
                    db,
                    org_id,
                    incident_id=updated.id,
                    chain_id=link.chain_id,
                    mode=updated.response_mode or "page",
                    channel_factory=build_channel_factory(),
                )

    if body.status == "resolved" and prior_status != "resolved":
        # Resolving an incident stops any AI sessions still working it.
        await stop_incident_sessions(
            request.app,
            db,
            org_id,
            incident_id,
            reason=f"Incident {body.status} by {user.username}",
        )
    await db.commit()
    if body.status == "resolved" and prior_status != "resolved":
        await _notify_channels(db, incident_id, org_id, "incident.resolved")
    refreshed = await IncidentRepo.get_by_id(db, org_id, incident_id)
    assert refreshed is not None
    return await _to_incident_response(db, org_id, refreshed)


@router.get(
    "/{incident_id}/postmortem",
    response_model=IncidentPostmortemResponse,
    summary="Get an incident's postmortem markdown",
)
async def get_incident_postmortem(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    """Sprint 61 Step 4 — return the operator-authored postmortem.

    Returns the stored markdown (``null`` when none), the last edit
    timestamp, and a canonical section template so a fresh editor can
    prefill the recommended structure without the frontend hardcoding it.
    """
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )
    return IncidentPostmortemResponse(
        incident_id=incident.id,
        postmortem_md=incident.postmortem_md,
        postmortem_updated_at=_aware(incident.postmortem_updated_at),
        template=DEFAULT_POSTMORTEM_TEMPLATE,
    )


@router.put(
    "/{incident_id}/postmortem",
    response_model=IncidentPostmortemResponse,
    summary="Set or clear an incident's postmortem markdown",
)
async def put_incident_postmortem(
    incident_id: uuid.UUID,
    body: IncidentPostmortemUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Sprint 61 Step 4 — write the postmortem markdown.

    Passing an empty or whitespace-only string clears the postmortem.
    Operator role required; viewers can read but not edit.
    """
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )
    await IncidentRepo.set_postmortem(db, org_id, incident_id, body.postmortem_md)
    await db.commit()
    refreshed = await IncidentRepo.get_by_id(db, org_id, incident_id)
    assert refreshed is not None
    return IncidentPostmortemResponse(
        incident_id=refreshed.id,
        postmortem_md=refreshed.postmortem_md,
        postmortem_updated_at=_aware(refreshed.postmortem_updated_at),
        template=DEFAULT_POSTMORTEM_TEMPLATE,
    )


@router.post(
    "/{incident_id}/postmortem/memory-candidates",
    response_model=PostmortemMemoryCandidatesResponse,
    summary="Create memories from the postmortem's Memory-candidates bullets",
)
async def create_postmortem_memory_candidates(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Turn each bullet under *Memory candidates* into a recallable memory.

    Re-running is safe: a candidate whose text already matches an existing
    memory for the service is skipped. Requires a saved postmortem.
    """
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )
    if not (incident.postmortem_md or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Save the postmortem before extracting memory candidates.",
        )

    candidates = extract_memory_candidates(incident.postmortem_md)
    if not candidates:
        return PostmortemMemoryCandidatesResponse(created=0, skipped=0, items=[])

    # Dedup against existing memories for the same service.
    existing = await IncidentMemoryRepo.list_for_org(
        db,
        org_id,
        service_id=incident.service_id,
        global_only=incident.service_id is None,
    )
    existing_titles = {m.title.strip().lower() for m in existing}

    tags: list[str] = []
    if isinstance(incident.severity, str) and incident.severity:
        tags.append(incident.severity.lower())

    items: list[PostmortemMemoryCandidate] = []
    created = 0
    skipped = 0
    for candidate in candidates:
        title = candidate_title(candidate)
        if title.strip().lower() in existing_titles:
            skipped += 1
            items.append(PostmortemMemoryCandidate(title=title, created=False))
            continue
        memory = await IncidentMemoryRepo.create(
            db,
            org_id=org_id,
            service_id=incident.service_id,
            source_incident_id=incident.id,
            title=title,
            summary_md=candidate,
            tags=list(tags),
            created_by_user_id=user.id,
        )
        existing_titles.add(title.strip().lower())
        created += 1
        items.append(
            PostmortemMemoryCandidate(
                memory_id=memory.id, title=title, created=True
            )
        )

    await db.commit()
    return PostmortemMemoryCandidatesResponse(
        created=created, skipped=skipped, items=items
    )


async def _comment_to_response(
    db: AsyncSession, comment
) -> IncidentCommentResponse:
    author_label = None
    if comment.author_user_id is not None:
        person = await UserRepo.get_by_id(db, comment.author_user_id)
        if person is not None:
            author_label = person.username
    return IncidentCommentResponse(
        id=comment.id,
        incident_id=comment.incident_id,
        body=comment.body,
        author_user_id=comment.author_user_id,
        author_label=author_label,
        created_at=_aware(comment.created_at) or comment.created_at,
        updated_at=_aware(comment.updated_at) or comment.updated_at,
    )


@router.get(
    "/{incident_id}/comments",
    response_model=IncidentCommentListResponse,
    summary="List operator comments on an incident",
)
async def list_incident_comments(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found"
        )
    comments = await IncidentCommentRepo.list_for_incident(db, org_id, incident_id)
    items = [await _comment_to_response(db, c) for c in comments]
    return IncidentCommentListResponse(items=items, total=len(items))


@router.post(
    "/{incident_id}/comments",
    response_model=IncidentCommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an operator comment to an incident",
)
async def create_incident_comment(
    incident_id: uuid.UUID,
    body: IncidentCommentCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found"
        )
    comment = await IncidentCommentRepo.create(
        db,
        org_id,
        incident_id=incident_id,
        body=body.body.strip(),
        author_user_id=user.id,
    )
    # Notify anyone @mentioned in the comment (resolve handles → org users).
    mentions = parse_mentions(comment.body)
    if mentions:
        members = await UserRepo.list_by_org(db, org_id)
        by_username = {m["username"].lower(): m["user_id"] for m in members}
        for handle in mentions:
            target_id = by_username.get(handle)
            if target_id is None or target_id == user.id:
                continue
            await emit_notification(
                db,
                org_id,
                target_id,
                event_type="mention.comment",
                category=CATEGORY_MENTION,
                title=f"{user.username} mentioned you",
                body=comment.body[:200],
                link=f"/dashboard/incidents/{incident_id}",
                incident_id=incident_id,
            )
    await db.commit()
    await db.refresh(comment)
    return await _comment_to_response(db, comment)


@router.delete(
    "/{incident_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an incident comment (author or admin)",
)
async def delete_incident_comment(
    incident_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    comment = await IncidentCommentRepo.get_by_id(db, org_id, comment_id)
    if comment is None or comment.incident_id != incident_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
        )
    # Only the author or an admin may delete a comment.
    if user.role != "admin" and comment.author_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the author or an admin can delete this comment.",
        )
    await IncidentCommentRepo.delete(db, org_id, comment_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{incident_id}/timeline",
    response_model=IncidentTimelineResponse,
    summary="Interleaved timeline for an incident command center",
)
async def get_incident_timeline(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        )

    sessions = list(await SessionRepo.list_by_incident(db, org_id, incident_id))
    assignments = list(await IncidentAssignmentRepo.list_for_incident(db, org_id, incident_id))
    pages = list(await IncidentPageRepo.list_for_incident(db, org_id, incident_id))
    ingest_logs = list(await IngestLogRepo.list_for_incident(db, org_id, incident_id))
    chain_state = await IncidentChainStateRepo.get_for_incident(db, org_id, incident_id)

    session_labels: dict[uuid.UUID, str] = {}
    session_by_id: dict[uuid.UUID, object] = {}
    for index, session in enumerate(
        sorted(sessions, key=lambda row: _aware(row.started_at) or row.started_at)
    ):
        session_labels[session.id] = f"S{index + 1}"
        session_by_id[session.id] = session

    user_ids = {
        assignment.assigned_to for assignment in assignments
    } | {page.user_id for page in pages}
    user_lookup: dict[uuid.UUID, str] = {}
    for user_id in user_ids:
        person = await UserRepo.get_by_id(db, user_id)
        if person is not None:
            user_lookup[user_id] = person.username

    skill = await SkillRepo.get_for_mcp_server(db, org_id, None)
    skill_def = load_skill_def_text(skill.content_md) if skill is not None else None

    items: list[IncidentTimelineItemResponse] = [
        IncidentTimelineItemResponse(
            id=f"incident:{incident.id}:opened",
            happened_at=_aware(incident.created_at) or incident.created_at,
            lane="response",
            event_type="incident_opened",
            title=(
                "Synthetic alert opened"
                if incident.external_source == "opsmender-test"
                else "Inbound alert opened"
                if incident.external_source
                else "Incident opened manually"
            ),
            body=incident.title,
            status=incident.status,
            metadata={
                "source": incident.external_source or "manual",
                "external_id": incident.external_id,
            },
        )
    ]

    for session in sessions:
        session_label = session_labels.get(session.id)
        provider = (
            f"{session.model_provider}/{session.model_id}"
            if session.model_provider and session.model_id
            else session.model_provider or session.model_id
        )
        items.append(
            IncidentTimelineItemResponse(
                id=f"session:{session.id}:started",
                happened_at=_aware(session.started_at) or session.started_at,
                lane="response",
                event_type="session_started",
                title=f"{session_label or 'Session'} started",
                body=provider,
                session_id=session.id,
                session_label=session_label,
                session_tier=session.tier,
                status=session.status,
                metadata={
                    "model_provider": session.model_provider,
                    "model_id": session.model_id,
                },
            )
        )
        if session.ended_at is not None:
            items.append(
                IncidentTimelineItemResponse(
                    id=f"session:{session.id}:ended",
                    happened_at=_aware(session.ended_at) or session.ended_at,
                    lane="response",
                    event_type="session_ended",
                    title=f"{session_label or 'Session'} {session.status.replace('_', ' ')}",
                    body=session.summary,
                    session_id=session.id,
                    session_label=session_label,
                    session_tier=session.tier,
                    status=session.status,
                )
            )

        audit_entries = list(await AuditEntryRepo.list_by_session(db, org_id, session.id))
        pending_starts: dict[str, deque] = defaultdict(deque)
        for entry in audit_entries:
            if entry.entry_type == "tool_call_start" and entry.tool_name:
                pending_starts[entry.tool_name].append(entry)
                continue
            if entry.entry_type == "tool_call_blocked" and entry.tool_name:
                items.append(
                    IncidentTimelineItemResponse(
                        id=f"audit:{entry.id}",
                        happened_at=_aware(entry.timestamp) or entry.timestamp,
                        lane="tool",
                        event_type="tool_blocked",
                        title=entry.tool_name,
                        body=entry.block_reason,
                        session_id=session.id,
                        session_label=session_label,
                        session_tier=entry.tier,
                        tool_name=entry.tool_name,
                        safety_class=(
                            skill_def.classify(entry.tool_name) if skill_def else "unknown"
                        ),
                        tier_decision="blocked",
                        status="blocked",
                        metadata=entry.tool_parameters or {},
                        json_payload=entry.result or entry.tool_parameters or {},
                    )
                )
                continue
            if entry.entry_type == "tool_call_end" and entry.tool_name:
                start_entry = None
                if pending_starts[entry.tool_name]:
                    start_entry = pending_starts[entry.tool_name].popleft()
                body = None
                if entry.result and entry.result.get("error"):
                    body = str(entry.result.get("error"))
                elif entry.result and entry.result.get("isError"):
                    body = "Tool returned an error result."
                elif entry.duration_ms is not None:
                    body = f"Completed in {entry.duration_ms} ms."
                items.append(
                    IncidentTimelineItemResponse(
                        id=f"audit:{entry.id}",
                        happened_at=_aware(entry.timestamp) or entry.timestamp,
                        lane="tool",
                        event_type="tool_completed",
                        title=entry.tool_name,
                        body=body,
                        session_id=session.id,
                        session_label=session_label,
                        session_tier=entry.tier,
                        tool_name=entry.tool_name,
                        safety_class=(
                            skill_def.classify(entry.tool_name) if skill_def else "unknown"
                        ),
                        tier_decision="permitted",
                        duration_ms=entry.duration_ms,
                        status=(
                            "error"
                            if entry.result and (entry.result.get("error") or entry.result.get("isError"))
                            else "completed"
                        ),
                        metadata=start_entry.tool_parameters if start_entry is not None else {},
                        json_payload=entry.result or {},
                    )
                )

    for assignment in assignments:
        actor_label = user_lookup.get(assignment.assigned_to)
        items.append(
            IncidentTimelineItemResponse(
                id=f"assignment:{assignment.id}:assigned",
                happened_at=_aware(assignment.assigned_at) or assignment.assigned_at,
                lane="response",
                event_type="ownership_assigned",
                title=_assignment_title(assignment.assigned_by),
                body=_assignment_body(assignment.assigned_by, actor_label),
                actor_user_id=assignment.assigned_to,
                actor_label=actor_label,
                metadata={"assigned_by": assignment.assigned_by},
            )
        )
        if assignment.released_at is not None:
            items.append(
                IncidentTimelineItemResponse(
                    id=f"assignment:{assignment.id}:released",
                    happened_at=_aware(assignment.released_at) or assignment.released_at,
                    lane="response",
                    event_type="ownership_released",
                    title="Ownership released",
                    body=(
                        f"{actor_label} was removed as the active owner."
                        if actor_label
                        else "The active owner was released."
                    ),
                    actor_user_id=assignment.assigned_to,
                    actor_label=actor_label,
                )
            )

    for page in pages:
        actor_label = user_lookup.get(page.user_id)
        items.append(
            IncidentTimelineItemResponse(
                id=f"page:{page.id}:sent",
                happened_at=_aware(page.sent_at) or page.sent_at,
                lane="response",
                event_type="escalation_step_fired",
                title=f"Escalation step {(page.step_index or 0) + 1} fired",
                body=(
                    f"Paged {actor_label or str(page.user_id)[:8]} via {page.channel}."
                ),
                actor_user_id=page.user_id,
                actor_label=actor_label,
                status=page.delivery_status,
                metadata={
                    "step_index": page.step_index,
                    "channel": page.channel,
                    "delivery_error": page.delivery_error,
                },
            )
        )
        if page.ack_at is not None:
            items.append(
                IncidentTimelineItemResponse(
                    id=f"page:{page.id}:ack",
                    happened_at=_aware(page.ack_at) or page.ack_at,
                    lane="response",
                    event_type="page_acknowledged",
                    title="Page acknowledged",
                    body=(
                        f"{actor_label or str(page.user_id)[:8]} acknowledged via {page.ack_via or 'chat'}."
                    ),
                    actor_user_id=page.user_id,
                    actor_label=actor_label,
                    status="acknowledged",
                    metadata={"ack_via": page.ack_via},
                )
            )

    for log in ingest_logs:
        items.append(
            IncidentTimelineItemResponse(
                id=f"ingest:{log.id}",
                happened_at=_aware(log.created_at) or log.created_at,
                lane="evidence",
                event_type="alert_evidence",
                title=f"{log.provider} payload received",
                body=(
                    f"Dedup action: {log.dedup_action}."
                    if log.dedup_action
                    else "Inbound payload captured."
                ),
                status=log.error and "error" or log.dedup_action,
                metadata={"error": log.error},
                json_payload=log.raw_payload,
            )
        )

    if incident.status == "resolved":
        items.append(
            IncidentTimelineItemResponse(
                id=f"incident:{incident.id}:final",
                happened_at=_aware(incident.updated_at) or incident.updated_at,
                lane="response",
                event_type="incident_status_changed",
                title=f"Incident {incident.status.replace('_', ' ')}",
                body=(
                    "Escalation chain finished."
                    if chain_state is not None and chain_state.finished_at is not None
                    else None
                ),
                status=incident.status,
            )
        )

    # v1.2 — operator comments on the timeline.
    comments = await IncidentCommentRepo.list_for_incident(db, org_id, incident_id)
    for comment in comments:
        author_label = None
        if comment.author_user_id is not None:
            author_label = user_lookup.get(comment.author_user_id)
            if author_label is None:
                person = await UserRepo.get_by_id(db, comment.author_user_id)
                author_label = person.username if person is not None else None
        items.append(
            IncidentTimelineItemResponse(
                id=f"comment:{comment.id}",
                happened_at=_aware(comment.created_at) or comment.created_at,
                lane="comment",
                event_type="comment",
                title=f"{author_label} commented" if author_label else "Comment",
                body=comment.body,
                actor_user_id=comment.author_user_id,
                actor_label=author_label,
            )
        )

    # v1.2 — notification history: every incident channel post is a timeline event.
    receipts = await IncidentNotificationReceiptRepo.list_for_incident(
        db, org_id, incident_id
    )
    for receipt in receipts:
        platform_label = (receipt.platform or "channel").replace("_", " ").title()
        lifecycle = (
            (receipt.lifecycle_event or "")
            .replace("incident.", "")
            .replace("session.", "session ")
            .replace("_", " ")
            .strip()
        )
        body = (
            f"{lifecycle} → {receipt.external_channel_id}"
            if receipt.external_channel_id
            else (lifecycle or None)
        )
        items.append(
            IncidentTimelineItemResponse(
                id=f"notification:{receipt.id}",
                happened_at=_aware(receipt.last_sent_at) or receipt.last_sent_at,
                lane="notification",
                event_type="notification_sent",
                title=f"Notified {platform_label}",
                body=body,
                status=receipt.delivery_status,
                metadata={
                    "platform": receipt.platform,
                    "lifecycle_event": receipt.lifecycle_event,
                    "channel": receipt.external_channel_id,
                },
            )
        )

    items.sort(key=lambda item: item.happened_at, reverse=True)
    return IncidentTimelineResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# Paging panel + incident assignment (Sprint 33)
# ---------------------------------------------------------------------------


async def _ensure_can_act_on_incident(
    db, org_id, user, incident
) -> None:
    """Allow admins/operators globally OR the active assignee (D-021 #9)."""

    if user.role in ("admin", "operator"):
        return
    active = await IncidentAssignmentRepo.get_active(db, org_id, incident.id)
    if active is not None and active.assigned_to == user.id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions for this incident",
    )


@router.get(
    "/{incident_id}/paging",
    response_model=IncidentPagingPanelResponse,
    summary="Paging panel for an incident",
)
async def get_incident_paging(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    assignment = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    suppressed = None
    if incident.suppressed_by_maintenance_window_id is not None:
        mw = await MaintenanceWindowRepo.get_by_id(
            db, org_id, incident.suppressed_by_maintenance_window_id
        )
        if mw is not None:
            suppressed = SuppressedByMaintenanceWindow(
                id=mw.id,
                name=mw.name,
                starts_at=mw.starts_at,
                ends_at=mw.ends_at,
                scope_type=mw.scope_type,
            )
    return IncidentPagingPanelResponse(
        incident_id=incident_id,
        priority=incident.priority,
        response_mode=incident.response_mode,
        service_id=incident.service_id,
        assignment=(
            IncidentAssignmentResponse.model_validate(assignment)
            if assignment is not None
            else None
        ),
        suppressed_by_maintenance_window=suppressed,
    )


@router.post(
    "/{incident_id}/assign",
    response_model=IncidentAssignmentResponse,
    summary="Take over an incident (or assign someone else)",
)
async def assign_incident(
    incident_id: uuid.UUID,
    body: IncidentAssignRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Self-takeover: any authenticated user may grab an unassigned incident
    # via self_ack (incident-scoped authority kicks in afterwards).
    target_user_id = body.user_id or user.id
    if target_user_id != user.id and user.role not in ("admin", "operator"):
        raise HTTPException(
            status_code=403,
            detail="Only admin/operator can assign other users",
        )

    assigned_by = "self_ack" if target_user_id == user.id else "manual"
    assignment = await IncidentAssignmentRepo.assign(
        db,
        org_id,
        incident_id=incident_id,
        user_id=target_user_id,
        assigned_by=assigned_by,
    )
    # Notify the assignee when someone else assigns them (self-ack is silent).
    if target_user_id != user.id:
        await emit_notification(
            db,
            org_id,
            target_user_id,
            event_type="incident.assigned",
            category=CATEGORY_INCIDENT,
            title=f"You were assigned: {incident.title}",
            body=f"{user.username} assigned this incident to you.",
            link=f"/dashboard/incidents/{incident_id}",
            incident_id=incident_id,
        )
    await db.commit()
    await db.refresh(assignment)
    return IncidentAssignmentResponse.model_validate(assignment)


@router.post(
    "/{incident_id}/release",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Release the active assignment",
)
async def release_incident(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    await _ensure_can_act_on_incident(db, org_id, user, incident)
    released = await IncidentAssignmentRepo.release(db, org_id, incident_id)
    if not released:
        raise HTTPException(status_code=404, detail="No active assignment")
    await db.commit()
    from fastapi import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/bulk",
    response_model=IncidentBulkActionResponse,
    summary="Run a single action across a set of incidents (Sprint 50)",
)
async def bulk_incident_action(
    body: IncidentBulkActionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Apply ``action`` to every id in ``incident_ids``.

    Lifecycle and delete actions are atomic. Legacy acknowledge/reassign
    actions retain per-row result reporting. Self-only enforcement on reassign
    matches the per-incident `/assign` route.

    Acknowledge = assign the current user (or ``user_id``) AND advance status
    from ``open`` → ``in_progress`` if it's still ``open``. The ack payload
    is incident-local; we don't poke the chain engine here (that path is
    via ``/incidents/{id}/ack`` for chain-driven sessions).

    Resolve/reopen/delete are validated atomically before any row is changed.
    Admins may run them across services. Operators may resolve or reopen only
    when every selected incident belongs to the same service.
    """
    action = body.action
    unique_ids = list(dict.fromkeys(body.incident_ids))

    if action in {"resolve", "reopen", "delete"}:
        incidents = []
        for incident_id in unique_ids:
            incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
            if incident is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Incident {incident_id} not found",
                )
            incidents.append(incident)

        if action == "delete":
            if user.role != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only Admins can permanently delete incidents.",
                )
        elif user.role == "operator":
            service_ids = {incident.service_id for incident in incidents}
            if len(service_ids) != 1 or None in service_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Operators can update incident status only when every "
                        "selected incident belongs to the same service."
                    ),
                )

        allowed_statuses = (
            {"open", "in_progress"} if action == "resolve" else {"resolved"}
        )
        if action != "delete":
            invalid = [
                incident
                for incident in incidents
                if incident.status not in allowed_statuses
            ]
            if invalid:
                expected = (
                    "open or in progress" if action == "resolve" else "resolved"
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"All selected incidents must be {expected} for "
                        f"the {action} action."
                    ),
                )

        if action == "delete":
            session_ids: list[uuid.UUID] = []
            for incident in incidents:
                await cancel_auto_start_for_incident(
                    request.app, incident_id=incident.id
                )
                sessions = await SessionRepo.list_by_incident(
                    db, org_id, incident.id
                )
                session_ids.extend(session.id for session in sessions)
            await cancel_session_workflows(request.app, session_ids=session_ids)
            for incident in incidents:
                await IncidentRepo.delete_permanently(db, org_id, incident.id)
        else:
            next_status = "resolved" if action == "resolve" else "open"
            for incident in incidents:
                await IncidentRepo.update_status(
                    db, org_id, incident.id, next_status
                )
                if action == "resolve":
                    await stop_incident_sessions(
                        request.app,
                        db,
                        org_id,
                        incident.id,
                        reason=f"Incident resolved by {user.username}",
                    )

        await db.commit()
        if action == "resolve":
            for incident in incidents:
                await _notify_channels(
                    db, incident.id, org_id, "incident.resolved"
                )
        return IncidentBulkActionResponse(
            action=action,
            succeeded=len(incidents),
            failed=0,
            items=[
                IncidentBulkActionResult(incident_id=incident.id, ok=True)
                for incident in incidents
            ],
        )

    # Role gate for reassign — match the per-incident /assign route.
    if action == "reassign":
        if body.user_id is None:
            raise HTTPException(
                status_code=400,
                detail="reassign requires user_id",
            )
        if body.user_id != user.id and user.role not in ("admin", "operator"):
            raise HTTPException(
                status_code=403,
                detail="Only admin/operator can reassign other users",
            )

    items: list[IncidentBulkActionResult] = []
    succeeded = 0
    failed = 0
    for incident_id in body.incident_ids:
        try:
            incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
            if incident is None:
                items.append(
                    IncidentBulkActionResult(
                        incident_id=incident_id, ok=False, error="not found"
                    )
                )
                failed += 1
                continue

            if action == "acknowledge":
                target = body.user_id or user.id
                if target != user.id and user.role not in ("admin", "operator"):
                    items.append(
                        IncidentBulkActionResult(
                            incident_id=incident_id,
                            ok=False,
                            error="Only admin/operator can assign other users",
                        )
                    )
                    failed += 1
                    continue
                await IncidentAssignmentRepo.assign(
                    db,
                    org_id,
                    incident_id=incident_id,
                    user_id=target,
                    assigned_by="self_ack" if target == user.id else "manual",
                )
                if target != user.id:
                    await emit_notification(
                        db,
                        org_id,
                        target,
                        event_type="incident.assigned",
                        category=CATEGORY_INCIDENT,
                        title=f"You were assigned: {incident.title}",
                        body=f"{user.username} assigned this incident to you.",
                        link=f"/dashboard/incidents/{incident_id}",
                        incident_id=incident_id,
                    )
                if incident.status == "open":
                    await IncidentRepo.update_status(
                        db, org_id, incident_id, "in_progress"
                    )
            elif action == "reassign":
                await IncidentAssignmentRepo.assign(
                    db,
                    org_id,
                    incident_id=incident_id,
                    user_id=body.user_id,  # already validated above
                    assigned_by="manual",
                )
                if body.user_id != user.id:
                    await emit_notification(
                        db,
                        org_id,
                        body.user_id,
                        event_type="incident.assigned",
                        category=CATEGORY_INCIDENT,
                        title=f"You were assigned: {incident.title}",
                        body=f"{user.username} assigned this incident to you.",
                        link=f"/dashboard/incidents/{incident_id}",
                        incident_id=incident_id,
                    )
            items.append(
                IncidentBulkActionResult(incident_id=incident_id, ok=True)
            )
            succeeded += 1
        except HTTPException as exc:
            items.append(
                IncidentBulkActionResult(
                    incident_id=incident_id,
                    ok=False,
                    error=str(exc.detail),
                )
            )
            failed += 1
        except Exception as exc:  # noqa: BLE001
            items.append(
                IncidentBulkActionResult(
                    incident_id=incident_id, ok=False, error=str(exc)
                )
            )
            failed += 1

    if succeeded > 0:
        await db.commit()
    return IncidentBulkActionResponse(
        action=action, succeeded=succeeded, failed=failed, items=items
    )


@router.post(
    "/{primary_id}/combine",
    response_model=IncidentCombineResponse,
    summary="Combine (merge) secondary incidents into a primary",
)
async def combine_incidents(
    primary_id: uuid.UUID,
    body: IncidentCombineRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Fold one or more *secondary* incidents into a *primary* incident.

    Each secondary's comments move to the primary (so the combined timeline
    reads as one), its in-progress AI sessions are stopped, its active
    assignment is released, and it transitions to a terminal ``merged`` state
    pointing at the primary — it is never deleted, so the audit trail and
    external fingerprint survive. A system note is added to the primary for each
    folded incident, plus the operator's optional ``note``.
    """
    primary = await IncidentRepo.get_by_id(db, org_id, primary_id)
    if primary is None:
        raise HTTPException(status_code=404, detail="Primary incident not found")
    if primary.status == "merged":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Primary incident is itself merged into another incident.",
        )

    secondary_ids = [sid for sid in dict.fromkeys(body.secondary_ids)]
    if primary_id in secondary_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An incident cannot be combined into itself.",
        )

    secondaries = []
    for sid in secondary_ids:
        sec = await IncidentRepo.get_by_id(db, org_id, sid)
        if sec is None:
            raise HTTPException(
                status_code=404, detail=f"Secondary incident {sid} not found"
            )
        if sec.status == "merged":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Incident {sid} is already merged.",
            )
        secondaries.append(sec)

    moved_comments = 0
    stopped_sessions = 0
    for sec in secondaries:
        stopped_sessions += await stop_incident_sessions(
            request.app,
            db,
            org_id,
            sec.id,
            reason=f"Combined into incident {primary_id} by {user.username}",
        )
        moved_comments += await IncidentCommentRepo.repoint(
            db, org_id, from_incident_id=sec.id, to_incident_id=primary_id
        )
        # Capture the secondary's assignee before releasing, so we can tell
        # them their incident was folded into the primary.
        sec_assignment = await IncidentAssignmentRepo.get_active(db, org_id, sec.id)
        sec_assignee = sec_assignment.assigned_to if sec_assignment else None
        await IncidentAssignmentRepo.release(db, org_id, sec.id)
        await IncidentRepo.combine_into(db, org_id, sec.id, primary_id=primary_id)
        if sec_assignee is not None and sec_assignee != user.id:
            await emit_notification(
                db,
                org_id,
                sec_assignee,
                event_type="incident.combined",
                category=CATEGORY_INCIDENT,
                title=f"Incident combined: {sec.title}",
                body=(
                    f"{user.username} combined this incident into "
                    f"“{primary.title}”."
                ),
                link=f"/dashboard/incidents/{primary_id}",
                incident_id=primary_id,
            )
        await IncidentCommentRepo.create(
            db,
            org_id,
            incident_id=primary_id,
            body=(
                f"Combined incident “{sec.title}” "
                f"({str(sec.id)[:8]}…) into this incident."
            ),
            author_user_id=user.id,
        )

    if body.note and body.note.strip():
        await IncidentCommentRepo.create(
            db,
            org_id,
            incident_id=primary_id,
            body=body.note.strip(),
            author_user_id=user.id,
        )

    await db.commit()
    for sid in secondary_ids:
        await _notify_channels(db, sid, org_id, "incident.resolved")

    refreshed = await IncidentRepo.get_by_id(db, org_id, primary_id)
    assert refreshed is not None
    return IncidentCombineResponse(
        primary=await _to_incident_response(db, org_id, refreshed),
        merged_incident_ids=secondary_ids,
        moved_comments=moved_comments,
        stopped_sessions=stopped_sessions,
    )


@router.get(
    "/{primary_id}/merged",
    response_model=IncidentListResponse,
    summary="List secondary incidents combined into this one",
)
async def list_merged_incidents(
    primary_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    primary = await IncidentRepo.get_by_id(db, org_id, primary_id)
    if primary is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    merged = await IncidentRepo.list_merged_into(db, org_id, primary_id)
    return IncidentListResponse(
        items=await _to_incident_list_response(db, org_id, merged),
        total=len(merged),
    )


# ---------------------------------------------------------------------------
# Escalation chain actions (Sprint 34)
# ---------------------------------------------------------------------------

from backend.api.schemas import (
    IncidentAckRequest,
    IncidentChainPanelResponse,
    IncidentChainStateResponse,
    IncidentPageResponse,
    IncidentTakeRequest,
)
from backend.db.repos import IncidentChainStateRepo, IncidentPageRepo
from backend.paging import escalation as _esc


@router.get(
    "/{incident_id}/chain",
    response_model=IncidentChainPanelResponse,
    summary="Chain state + page log for an incident",
)
async def get_incident_chain(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    state = await IncidentChainStateRepo.get_for_incident(db, org_id, incident_id)
    pages = await IncidentPageRepo.list_for_incident(db, org_id, incident_id)
    return IncidentChainPanelResponse(
        incident_id=incident_id,
        state=(
            IncidentChainStateResponse.model_validate(state)
            if state is not None
            else None
        ),
        pages=[IncidentPageResponse.model_validate(p) for p in pages],
    )


@router.post(
    "/{incident_id}/ack",
    response_model=IncidentChainPanelResponse,
    summary="Ack an incident (button click / slash command / web UI / API)",
)
async def ack_incident(
    incident_id: uuid.UUID,
    body: IncidentAckRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    await _esc.handle_ack(
        db,
        org_id,
        incident_id=incident_id,
        user_id=user.id,
        via=body.via,
    )
    await db.commit()
    await _notify_channels(db, incident_id, org_id, "incident.acknowledged")
    # T1/T2 sessions start on acknowledgment (T0 already started at creation).
    # Duplicate acks are safe — an active session short-circuits this.
    auto_status, reason, tier = await _resolve_auto_start_on_ack(
        request, db, org_id, incident
    )
    state = await IncidentChainStateRepo.get_for_incident(db, org_id, incident_id)
    pages = await IncidentPageRepo.list_for_incident(db, org_id, incident_id)
    return IncidentChainPanelResponse(
        incident_id=incident_id,
        state=(
            IncidentChainStateResponse.model_validate(state)
            if state is not None
            else None
        ),
        pages=[IncidentPageResponse.model_validate(p) for p in pages],
        auto_start_status=auto_status,
        auto_start_reason=reason,
        resolved_tier=tier,
        auto_start_message=_auto_start_message(
            context="acknowledged", tier=tier, status=auto_status, reason=reason
        ),
    )


@router.post(
    "/{incident_id}/take",
    response_model=IncidentChainPanelResponse,
    summary="Request soft-takeover, confirm one, or admin-force",
)
async def take_incident(
    incident_id: uuid.UUID,
    body: IncidentTakeRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    if body.force:
        if user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail="Force-takeover requires admin",
            )
        await _esc.handle_force_takeover(
            db, org_id, incident_id=incident_id, admin_id=user.id
        )
    elif body.confirm:
        await _esc.handle_takeover_confirm(
            db, org_id, incident_id=incident_id
        )
    else:
        await _esc.handle_takeover_request(
            db, org_id, incident_id=incident_id, requester_id=user.id
        )
    await db.commit()
    state = await IncidentChainStateRepo.get_for_incident(db, org_id, incident_id)
    pages = await IncidentPageRepo.list_for_incident(db, org_id, incident_id)
    return IncidentChainPanelResponse(
        incident_id=incident_id,
        state=(
            IncidentChainStateResponse.model_validate(state)
            if state is not None
            else None
        ),
        pages=[IncidentPageResponse.model_validate(p) for p in pages],
    )
