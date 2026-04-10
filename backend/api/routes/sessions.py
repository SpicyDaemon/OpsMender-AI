"""Session endpoints.

POST /sessions        — start a new incident response session
GET  /sessions/{id}   — get session details
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import SessionCreate, SessionResponse
from backend.db.models import User
from backend.db.repos import IncidentRepo, SessionRepo

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new session",
)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    # Validate linked incident exists (if provided)
    if body.incident_id is not None:
        incident = await IncidentRepo.get_by_id(db, body.incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Incident not found",
            )

    session = await SessionRepo.create(
        db,
        tier=body.tier,
        incident_id=body.incident_id,
        model_provider=body.model_provider,
        model_id=body.model_id,
    )
    return session


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get session details",
)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await SessionRepo.get_by_id(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return session
