"""Incident endpoints.

POST /incidents        — create a new incident
GET  /incidents        — list incidents (filterable, paginated)
GET  /incidents/{id}   — get single incident with sessions
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    IncidentCreate,
    IncidentListResponse,
    IncidentResponse,
    SessionListResponse,
    SessionResponse,
)
from backend.config_loader import Config
from backend.db.models import User
from backend.db.repos import (
    IncidentAssignmentRepo,
    IncidentRepo,
    SessionRepo,
)
from backend.api.schemas import (
    IncidentAssignmentResponse,
    IncidentAssignRequest,
    IncidentPagingPanelResponse,
)
from backend.paging.service import compute_priority_for_payload
from backend.paging import escalation as _esc_kickoff

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _tier0_max_session_seconds() -> int:
    try:
        return Config.load().tier0.max_session_seconds
    except (FileNotFoundError, ValueError):
        return 600


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
    user: User = Depends(require_role("admin", "operator")),
):
    # Run priority rules first so priority/response_mode go in with the INSERT
    # rather than via a follow-up UPDATE (D-021: locked at creation).
    payload = {
        "title": body.title,
        "description": body.description,
        "severity": body.severity,
    }
    priority_result = await compute_priority_for_payload(db, org_id, payload)
    incident = await IncidentRepo.create(
        db,
        org_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
        priority=priority_result.priority,
        response_mode=priority_result.response_mode,
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
            await _esc_kickoff.start_chain(
                db,
                org_id,
                incident_id=incident.id,
                chain_id=link.chain_id,
                mode=priority_result.response_mode,
            )
    return incident


@router.get(
    "",
    response_model=IncidentListResponse,
    summary="List incidents",
)
async def list_incidents(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await IncidentRepo.list_all(
        db, org_id, status=status_filter, limit=limit, offset=offset
    )
    # For total count we re-query without limit/offset.
    # A lightweight approach — fine for now, can optimise later.
    all_items = await IncidentRepo.list_all(
        db, org_id, status=status_filter, limit=10_000, offset=0
    )
    return IncidentListResponse(items=list(items), total=len(all_items))


@router.get(
    "/{incident_id}/sessions",
    response_model=SessionListResponse,
    summary="List sessions for an incident",
)
async def list_incident_sessions(
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
    return incident


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
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
