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
from backend.db.repos import IncidentRepo, SessionRepo

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
    incident = await IncidentRepo.create(
        db,
        org_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
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
