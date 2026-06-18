"""Per-user in-app notification center (v1.2 — the bell).

Every endpoint is scoped to the authenticated user within the current org, so
a user only ever sees and mutates their own notifications. No role gate: any
signed-in user has a notification center.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user
from backend.api.deps import get_db
from backend.api.schemas import (
    InAppNotificationListResponse,
    MarkReadResponse,
    UnreadCountResponse,
)
from backend.db.models import User
from backend.db.repos import InAppNotificationRepo

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=InAppNotificationListResponse, summary="List notifications")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
) -> InAppNotificationListResponse:
    items = await InAppNotificationRepo.list_for_user(
        db, org_id, user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    total = await InAppNotificationRepo.count_for_user(db, org_id, user.id)
    unread = await InAppNotificationRepo.count_for_user(
        db, org_id, user.id, unread_only=True
    )
    return InAppNotificationListResponse(items=items, total=total, unread=unread)


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Unread notification count (bell badge)",
)
async def unread_count(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
) -> UnreadCountResponse:
    unread = await InAppNotificationRepo.count_for_user(
        db, org_id, user.id, unread_only=True
    )
    return UnreadCountResponse(unread=unread)


@router.post(
    "/read-all",
    response_model=MarkReadResponse,
    summary="Mark every notification read",
)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
) -> MarkReadResponse:
    updated = await InAppNotificationRepo.mark_all_read(db, org_id, user.id)
    await db.commit()
    return MarkReadResponse(updated=updated)


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notification_id: uuid.UUID,
    read: bool = True,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
) -> Response:
    ok = await InAppNotificationRepo.mark_read(
        db, org_id, user.id, notification_id, read=read
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
) -> Response:
    ok = await InAppNotificationRepo.delete(db, org_id, user.id, notification_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
