"""Sprint 45 Step 6 — AI incident memory CRUD + feedback API.

All endpoints are org-scoped via :func:`get_current_org`. Memory is operator-
owned the same way SKILL.md is: read is broad, write is operator-or-admin,
delete + hide are admin-only.

Routes (prefix ``/memories``):
- ``GET    /memories``                — list (filter by service_id, include_hidden)
- ``GET    /memories/{id}``           — detail
- ``POST   /memories``                — operator manual create
- ``PUT    /memories/{id}``           — operator manual edit
- ``DELETE /memories/{id}``           — admin only
- ``POST   /memories/{id}/feedback``  — thumbs up/down (operator + admin)
- ``POST   /memories/{id}/hide``      — admin hides without deleting

Also adds (no prefix mounting collision with `/sessions`):
- ``GET    /sessions/{id}/memories-used`` — surfaced memories for a session
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    IncidentMemoryCreate,
    IncidentMemoryFeedbackRequest,
    IncidentMemoryHideRequest,
    IncidentMemoryListResponse,
    IncidentMemoryResponse,
    IncidentMemoryUpdate,
    SessionMemoriesUsedItem,
    SessionMemoriesUsedResponse,
)
from backend.db.models import IncidentMemory, User
from backend.db.repos import (
    IncidentMemoryRecallLogRepo,
    IncidentMemoryRepo,
    ServiceRepo,
    SessionRepo,
)

router = APIRouter(prefix="/memories", tags=["memories"])
sessions_memory_router = APIRouter(prefix="/sessions", tags=["memories"])


def _to_response(memory: IncidentMemory) -> IncidentMemoryResponse:
    return IncidentMemoryResponse(
        id=memory.id,
        org_id=memory.org_id,
        service_id=memory.service_id,
        source_incident_id=memory.source_incident_id,
        title=memory.title,
        summary_md=memory.summary_md,
        tags=list(memory.tags or []),
        helpful_count=memory.helpful_count or 0,
        unhelpful_count=memory.unhelpful_count or 0,
        is_hidden=bool(memory.is_hidden),
        created_by_user_id=memory.created_by_user_id,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        last_used_at=memory.last_used_at,
    )


async def _validate_service(
    db: AsyncSession, org_id: uuid.UUID, service_id: uuid.UUID | None
) -> None:
    if service_id is None:
        return
    service = await ServiceRepo.get_by_id(db, org_id, service_id)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Service {service_id} not found",
        )


@router.get(
    "",
    response_model=IncidentMemoryListResponse,
    summary="List incident memories for the active org",
)
async def list_memories(
    service_id: uuid.UUID | None = None,
    include_hidden: bool = False,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await IncidentMemoryRepo.list_for_org(
        db,
        org_id,
        service_id=service_id,
        include_hidden=include_hidden,
    )
    return IncidentMemoryListResponse(
        items=[_to_response(item) for item in items],
        total=len(items),
    )


@router.get(
    "/{memory_id}",
    response_model=IncidentMemoryResponse,
    summary="Get a single memory",
)
async def get_memory(
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    memory = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    return _to_response(memory)


@router.post(
    "",
    response_model=IncidentMemoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a memory by hand (operator-authored)",
)
async def create_memory(
    body: IncidentMemoryCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    await _validate_service(db, org_id, body.service_id)
    memory = await IncidentMemoryRepo.create(
        db,
        org_id=org_id,
        service_id=body.service_id,
        title=body.title.strip(),
        summary_md=body.summary_md,
        tags=[t.strip().lower() for t in body.tags if t and t.strip()],
        created_by_user_id=user.id,
    )
    await db.commit()
    await db.refresh(memory)
    return _to_response(memory)


@router.put(
    "/{memory_id}",
    response_model=IncidentMemoryResponse,
    summary="Update a memory",
)
async def update_memory(
    memory_id: uuid.UUID,
    body: IncidentMemoryUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    if body.service_id_set:
        await _validate_service(db, org_id, body.service_id)

    tags: list[str] | None = None
    if body.tags is not None:
        tags = [t.strip().lower() for t in body.tags if t and t.strip()]

    updated = await IncidentMemoryRepo.update(
        db,
        memory_id=memory_id,
        org_id=org_id,
        title=body.title.strip() if body.title is not None else None,
        summary_md=body.summary_md,
        tags=tags,
        service_id=body.service_id,
        service_id_set=body.service_id_set,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    await db.commit()
    await db.refresh(updated)
    return _to_response(updated)


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory (admin only)",
)
async def delete_memory(
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await IncidentMemoryRepo.delete(
        db, memory_id=memory_id, org_id=org_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{memory_id}/feedback",
    response_model=IncidentMemoryResponse,
    summary="Record operator thumbs up/down on a memory",
)
async def memory_feedback(
    memory_id: uuid.UUID,
    body: IncidentMemoryFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    updated = await IncidentMemoryRepo.record_feedback(
        db,
        memory_id=memory_id,
        org_id=org_id,
        helpful=body.helpful,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    await db.commit()
    await db.refresh(updated)
    return _to_response(updated)


@router.post(
    "/{memory_id}/hide",
    response_model=IncidentMemoryResponse,
    summary="Hide or unhide a memory (admin only)",
)
async def memory_hide(
    memory_id: uuid.UUID,
    body: IncidentMemoryHideRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    updated = await IncidentMemoryRepo.set_hidden(
        db,
        memory_id=memory_id,
        org_id=org_id,
        hidden=body.hidden,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    await db.commit()
    await db.refresh(updated)
    return _to_response(updated)


@sessions_memory_router.get(
    "/{session_id}/memories-used",
    response_model=SessionMemoriesUsedResponse,
    summary="Memories surfaced for a session by the recall node",
)
async def session_memories_used(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    # Enforce org boundary on the session itself before exposing recall log.
    session = await SessionRepo.get_by_id(db, org_id, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    logs = await IncidentMemoryRecallLogRepo.list_for_session(db, session_id)

    items: list[SessionMemoriesUsedItem] = []
    for log in logs:
        memory = await IncidentMemoryRepo.get_by_id(db, log.memory_id, org_id)
        if memory is None:
            # Memory deleted after recall — skip rather than leaking a stale row.
            continue
        items.append(
            SessionMemoriesUsedItem(
                memory=_to_response(memory),
                surfaced_at=log.surfaced_at,
                score=float(log.score) if log.score is not None else None,
            )
        )

    return SessionMemoriesUsedResponse(items=items, total=len(items))
