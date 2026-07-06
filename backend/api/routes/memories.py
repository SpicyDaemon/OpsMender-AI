"""AI incident memory CRUD, feedback, and recall-history API.

All endpoints are org-scoped via :func:`get_current_org`. Memory is operator-
and mutation is team-scoped for Operators. Admins may manage every memory.

Routes (prefix ``/memories``):
- ``GET    /memories``                — list (filter by service_id)
- ``GET    /memories/{id}``           — detail
- ``POST   /memories``                — operator manual create
- ``PUT    /memories/{id}``           — team-scoped operator edit
- ``DELETE /memories/{id}``           — team-scoped operator delete
- ``POST   /memories/bulk-delete``    — atomic bulk delete
- ``POST   /memories/{id}/feedback``  — thumbs up/down (operator + admin)

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
    IncidentMemoryBulkDeleteRequest,
    IncidentMemoryBulkDeleteResponse,
    IncidentMemoryCreate,
    IncidentMemoryFeedbackRequest,
    IncidentMemoryListResponse,
    IncidentMemoryResponse,
    IncidentMemoryUpdate,
    SessionMemoriesUsedItem,
    SessionMemoriesUsedResponse,
)
from backend.db.models import IncidentMemory, IncidentMemoryRecallLog, User
from backend.db.repos import (
    IncidentMemoryRecallLogRepo,
    IncidentMemoryRepo,
    ServiceRepo,
    SessionRepo,
    TeamRepo,
)
from backend.memory.tags import normalize_memory_tags

router = APIRouter(prefix="/memories", tags=["memories"])
sessions_memory_router = APIRouter(prefix="/sessions", tags=["memories"])


def _to_response(
    memory: IncidentMemory, *, can_manage: bool = False
) -> IncidentMemoryResponse:
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
        pinned=bool(memory.pinned),
        can_edit=can_manage,
        can_delete=can_manage,
        created_by_user_id=memory.created_by_user_id,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        last_used_at=memory.last_used_at,
    )


async def _manageable_memory_ids(
    db: AsyncSession,
    org_id: uuid.UUID,
    user: User,
    memories: list[IncidentMemory],
) -> set[uuid.UUID]:
    if user.role == "admin":
        return {memory.id for memory in memories}
    if user.role != "operator":
        return set()
    team_ids = await TeamRepo.team_ids_for_user(db, org_id, user.id)
    service_ids = {
        memory.service_id for memory in memories if memory.service_id is not None
    }
    services = {
        service.id: service
        for service in await ServiceRepo.list_all(db, org_id)
        if service.id in service_ids
    }
    return {
        memory.id
        for memory in memories
        if memory.service_id is not None
        and memory.service_id in services
        and services[memory.service_id].team_id in team_ids
    }


async def _visible_memory_ids(
    db: AsyncSession,
    org_id: uuid.UUID,
    user: User,
    memories: list[IncidentMemory],
) -> set[uuid.UUID]:
    """Return memories visible to the current user.

    Operators see global memories plus memories tied to services owned by one
    of their teams. Admins and viewers retain organization-wide read access.
    """
    if user.role != "operator":
        return {memory.id for memory in memories}
    team_ids = await TeamRepo.team_ids_for_user(db, org_id, user.id)
    service_ids = {
        memory.service_id for memory in memories if memory.service_id is not None
    }
    visible_service_ids = {
        service.id
        for service in await ServiceRepo.list_all(db, org_id)
        if service.id in service_ids and service.team_id in team_ids
    }
    return {
        memory.id
        for memory in memories
        if memory.service_id is None or memory.service_id in visible_service_ids
    }


async def _require_memory_visibility(
    db: AsyncSession,
    org_id: uuid.UUID,
    user: User,
    memory: IncidentMemory,
) -> None:
    visible = await _visible_memory_ids(db, org_id, user, [memory])
    if memory.id not in visible:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )


async def _require_memory_management(
    db: AsyncSession,
    org_id: uuid.UUID,
    user: User,
    memory: IncidentMemory,
) -> None:
    manageable = await _manageable_memory_ids(db, org_id, user, [memory])
    if memory.id not in manageable:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Operators can edit or delete only memories owned by their teams. "
                "Global memories require an Admin."
            ),
        )


async def _require_operator_service_access(
    db: AsyncSession,
    org_id: uuid.UUID,
    user: User,
    service_id: uuid.UUID | None,
) -> None:
    if user.role == "admin":
        return
    if service_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global memories require an Admin.",
        )
    service = await ServiceRepo.get_by_id(db, org_id, service_id)
    if service is None or not await TeamRepo.is_member(
        db, org_id, service.team_id, user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operators can assign memories only to services owned by their teams.",
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
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await IncidentMemoryRepo.list_for_org(
        db,
        org_id,
        service_id=service_id,
    )
    visible = await _visible_memory_ids(db, org_id, user, list(items))
    items = [item for item in items if item.id in visible]
    manageable = await _manageable_memory_ids(db, org_id, user, items)
    return IncidentMemoryListResponse(
        items=[
            _to_response(item, can_manage=item.id in manageable) for item in items
        ],
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
    await _require_memory_visibility(db, org_id, user, memory)
    manageable = await _manageable_memory_ids(db, org_id, user, [memory])
    return _to_response(memory, can_manage=memory.id in manageable)


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
    if user.role == "operator" and body.service_id is not None:
        await _require_operator_service_access(
            db, org_id, user, body.service_id
        )
    memory = await IncidentMemoryRepo.create(
        db,
        org_id=org_id,
        service_id=body.service_id,
        title=body.title.strip(),
        summary_md=body.summary_md,
        tags=normalize_memory_tags(body.tags),
        created_by_user_id=user.id,
    )
    await db.commit()
    await db.refresh(memory)
    manageable = await _manageable_memory_ids(db, org_id, user, [memory])
    return _to_response(memory, can_manage=memory.id in manageable)


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
    memory = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    await _require_memory_management(db, org_id, user, memory)
    if body.service_id_set:
        await _validate_service(db, org_id, body.service_id)
        await _require_operator_service_access(
            db, org_id, user, body.service_id
        )

    tags: list[str] | None = None
    if body.tags is not None:
        tags = normalize_memory_tags(body.tags)

    updated = await IncidentMemoryRepo.update(
        db,
        memory_id=memory_id,
        org_id=org_id,
        title=body.title.strip() if body.title is not None else None,
        summary_md=body.summary_md,
        tags=tags,
        service_id=body.service_id,
        service_id_set=body.service_id_set,
        pinned=body.pinned,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    await db.commit()
    await db.refresh(updated)
    manageable = await _manageable_memory_ids(db, org_id, user, [updated])
    return _to_response(updated, can_manage=updated.id in manageable)


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory",
)
async def delete_memory(
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    memory = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    await _require_memory_management(db, org_id, user, memory)
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
    "/bulk-delete",
    response_model=IncidentMemoryBulkDeleteResponse,
    summary="Delete multiple memories atomically",
)
async def bulk_delete_memories(
    body: IncidentMemoryBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    unique_ids = list(dict.fromkeys(body.memory_ids))
    memories: list[IncidentMemory] = []
    for memory_id in unique_ids:
        memory = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
        if memory is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory {memory_id} not found",
            )
        memories.append(memory)
    manageable = await _manageable_memory_ids(db, org_id, user, memories)
    unauthorized = [memory for memory in memories if memory.id not in manageable]
    if unauthorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Selection includes memories the Operator cannot delete. "
                "Operators can delete only memories owned by their teams; "
                "global memories require an Admin."
            ),
        )
    deleted = await IncidentMemoryRepo.delete_many(
        db, memory_ids=unique_ids, org_id=org_id
    )
    await db.commit()
    return IncidentMemoryBulkDeleteResponse(deleted=deleted)


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
    memory = await IncidentMemoryRepo.get_by_id(db, memory_id, org_id)
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    await _require_memory_visibility(db, org_id, user, memory)
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
    manageable = await _manageable_memory_ids(db, org_id, user, [updated])
    return _to_response(updated, can_manage=updated.id in manageable)


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

    memories: list[tuple[IncidentMemoryRecallLog, IncidentMemory]] = []
    for log in logs:
        memory = await IncidentMemoryRepo.get_by_id(db, log.memory_id, org_id)
        if memory is not None:
            memories.append((log, memory))
    visible = await _visible_memory_ids(
        db, org_id, user, [memory for _, memory in memories]
    )
    items: list[SessionMemoriesUsedItem] = []
    for log, memory in memories:
        if memory.id not in visible:
            continue
        items.append(
            SessionMemoriesUsedItem(
                memory=_to_response(memory),
                surfaced_at=log.surfaced_at,
                score=float(log.score) if log.score is not None else None,
            )
        )

    return SessionMemoriesUsedResponse(items=items, total=len(items))
