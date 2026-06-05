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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    DEFAULT_POSTMORTEM_TEMPLATE,
    IncidentCreate,
    IncidentListResponse,
    IncidentPostmortemResponse,
    IncidentPostmortemUpdate,
    IncidentResponse,
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
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
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
from backend.api.schemas import (
    IncidentAssignmentResponse,
    IncidentAssignRequest,
    IncidentBulkActionRequest,
    IncidentBulkActionResponse,
    IncidentBulkActionResult,
    IncidentPagingPanelResponse,
    SuppressedByMaintenanceWindow,
)
from backend.paging.service import compute_priority_for_payload
from backend.paging import escalation as _esc_kickoff
from backend.skills.parser import loads as load_skill_def_text

router = APIRouter(prefix="/incidents", tags=["incidents"])


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
    return IncidentResponse(**data)


async def _to_incident_list_response(
    db: AsyncSession, org_id: uuid.UUID, incidents
) -> list[IncidentResponse]:
    services = await ServiceRepo.list_all(db, org_id)
    teams = await TeamRepo.list_all(db, org_id)
    service_by_id = {service.id: service for service in services}
    team_by_id = {team.id: team for team in teams}
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
        responses.append(IncidentResponse(**data))
    return responses


@router.post(
    "",
    response_model=IncidentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new incident",
)
async def create_incident(
    body: IncidentCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if body.service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, body.service_id)
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service not found",
            )

    # v1 priority comes from the selected service when one is present. For
    # manual incidents without a service, fall back to severity mapping.
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
    return await _to_incident_response(db, org_id, incident)


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
    # Drop unknown values so a stray value can't make the result empty.
    allowed_severities = {"critical", "high", "medium", "low"}
    allowed_sources = {"manual", "ingested"}
    severity = [s for s in (severity or []) if s in allowed_severities] or None
    source = [s for s in (source or []) if s in allowed_sources] or None

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
    )
    return IncidentListResponse(
        items=await _to_incident_list_response(db, org_id, items),
        total=total,
    )


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

    await db.commit()
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

    if incident.status in ("resolved", "closed"):
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
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Apply ``action`` to every id in ``incident_ids``. Per-row failures
    don't abort the batch — each result reports its own ``ok`` + optional
    ``error``. Self-only enforcement on reassign matches the per-incident
    `/assign` route: only admin/operator can target someone else.

    Acknowledge = assign the current user (or ``user_id``) AND advance status
    from ``open`` → ``in_progress`` if it's still ``open``. The ack payload
    is incident-local; we don't poke the chain engine here (that path is
    via ``/incidents/{id}/ack`` for chain-driven sessions).

    Resolve = set status to ``resolved``. Idempotent.
    """
    action = body.action
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

            if action == "resolve":
                if incident.status != "closed":
                    await IncidentRepo.update_status(
                        db, org_id, incident_id, "resolved"
                    )
            elif action == "acknowledge":
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
