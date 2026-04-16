"""Session endpoints.

POST /sessions                      — start a new incident response session
GET  /sessions/{id}                 — get session details
GET  /sessions/{id}/messages        — list co-pilot chat messages
POST /sessions/{id}/messages        — append a user message + fire async reply
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user, require_role
from backend.api.deps import get_current_session_factory, get_db
from backend.api.routes.ws import publish
from backend.api.schemas import (
    SessionCreate,
    SessionMessageCreate,
    SessionMessageListResponse,
    SessionMessageResponse,
    SessionResponse,
    WSMessage,
)
from backend.chat import respond_to_user_message
from backend.db.models import User
from backend.db.repos import IncidentRepo, SessionMessageRepo, SessionRepo

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

    briefing = (body.initial_briefing or "").strip()
    briefing_message_id: uuid.UUID | None = None
    if briefing:
        msg = await SessionMessageRepo.create(
            db,
            session_id=session.id,
            role="user",
            content=briefing,
            node_context="initial_briefing",
        )
        briefing_message_id = msg.id

    await db.commit()
    await db.refresh(session)

    # If a briefing was provided, fire the responder so the chat has an
    # assistant reply waiting by the time the UI connects.
    if briefing_message_id is not None:
        factory = get_current_session_factory()
        asyncio.create_task(
            respond_to_user_message(
                factory,
                session_id=session.id,
                user_message_id=briefing_message_id,
            )
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
    user: User = Depends(get_current_user),
):
    session = await SessionRepo.get_by_id(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    items = await SessionMessageRepo.list_by_session(db, session_id)
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
    user: User = Depends(require_role("admin", "operator")),
):
    session = await SessionRepo.get_by_id(db, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    message = await SessionMessageRepo.create(
        db,
        session_id=session_id,
        role="user",
        content=body.content,
    )
    await db.commit()
    await db.refresh(message)

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
            session_id=session_id,
            user_message_id=message.id,
        )
    )

    return message
