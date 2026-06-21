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
    NotificationPreferencesResponse,
    NotificationPreferencesUpdate,
    UnreadCountResponse,
)
from backend.db.models import User
from backend.db.repos import InAppNotificationRepo, UserNotificationPrefRepo
from backend.notifications import ALL_CATEGORIES

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _validate_quiet_hours(qh) -> None:
    """Reject malformed quiet-hours times so the stored shape stays clean."""
    if qh is None or not qh.enabled:
        return
    for label, value in (("start", qh.start), ("end", qh.end)):
        if not value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"quiet_hours.{label} is required when enabled",
            )
        try:
            h, m = (int(x) for x in str(value).split(":"))
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"quiet_hours.{label} must be HH:MM",
            )


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


@router.get(
    "/preferences",
    response_model=NotificationPreferencesResponse,
    summary="Get the user's in-app notification preferences",
)
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
) -> NotificationPreferencesResponse:
    pref = await UserNotificationPrefRepo.get_for_user(db, org_id, user.id)
    muted: list[str] = []
    quiet = None
    if pref is not None:
        in_app = (pref.routing or {}).get("in_app") or {}
        muted = list(in_app.get("muted_categories") or [])
        quiet = pref.quiet_hours
    return NotificationPreferencesResponse(
        muted_categories=muted,
        quiet_hours=quiet,
        categories=list(ALL_CATEGORIES),
    )


@router.put(
    "/preferences",
    response_model=NotificationPreferencesResponse,
    summary="Update in-app notification mute + quiet-hours",
)
async def update_preferences(
    body: NotificationPreferencesUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
) -> NotificationPreferencesResponse:
    existing = await UserNotificationPrefRepo.get_for_user(db, org_id, user.id)
    routing = dict(existing.routing) if existing and existing.routing else {}

    if body.muted_categories is not None:
        invalid = set(body.muted_categories) - set(ALL_CATEGORIES)
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown categories: {sorted(invalid)}",
            )
        in_app = dict(routing.get("in_app") or {})
        # de-dupe while preserving the canonical category order
        in_app["muted_categories"] = [
            c for c in ALL_CATEGORIES if c in set(body.muted_categories)
        ]
        routing["in_app"] = in_app

    quiet_provided = body.quiet_hours is not None
    if quiet_provided:
        _validate_quiet_hours(body.quiet_hours)

    await UserNotificationPrefRepo.upsert(
        db,
        org_id,
        user.id,
        routing=routing if body.muted_categories is not None else None,
        quiet_hours=(
            body.quiet_hours.model_dump() if body.quiet_hours is not None else None
        ),
        quiet_hours_provided=quiet_provided,
    )
    await db.commit()
    return await get_preferences(db=db, org_id=org_id, user=user)


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
