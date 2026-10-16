"""Per-user in-app notification center (v1.2 - the bell).

Every endpoint is scoped to the authenticated user within the current org, so
a user only ever sees and mutates their own notifications. No role gate: any
signed-in user has a notification center.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, reject_api_tokens
from backend.api.deps import get_db
from backend.api.schemas import (
    InAppNotificationListResponse,
    MarkReadResponse,
    NotificationPreferencesResponse,
    NotificationPreferencesUpdate,
    UnreadCountResponse,
    is_clock,
)
from backend.db.models import User
from backend.db.repos import InAppNotificationRepo, UserNotificationPrefRepo
from backend.notifications import ALL_CATEGORIES

# The Inbox and its settings are personal: people sign in for them, and API
# tokens are refused like every other self-service route.
router = APIRouter(prefix="/notifications", tags=["notifications"])


def _validate_quiet_hours(qh) -> None:
    """Reject malformed Inbox quiet hours so the stored shape stays clean.

    The Inbox preferences page shows the detail under its form, so it names
    the setting the way the page does: My Routing has its own quiet hours.
    """
    if qh is None or not qh.enabled:
        return
    for label, value in (("start", qh.start), ("end", qh.end)):
        if not value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Inbox quiet hours {label} is required when enabled",
            )
        if not is_clock(value):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Inbox quiet hours {label} must be HH:MM",
            )


@router.get(
    "", response_model=InAppNotificationListResponse, summary="List notifications"
)
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(reject_api_tokens),
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
    user: User = Depends(reject_api_tokens),
) -> UnreadCountResponse:
    unread = await InAppNotificationRepo.count_for_user(
        db, org_id, user.id, unread_only=True
    )
    return UnreadCountResponse(unread=unread)


@router.get(
    "/preferences",
    response_model=NotificationPreferencesResponse,
    summary="Get the user's Inbox preferences",
)
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(reject_api_tokens),
) -> NotificationPreferencesResponse:
    pref = await UserNotificationPrefRepo.get_for_user(db, org_id, user.id)
    muted: list[str] = []
    quiet = None
    if pref is not None:
        # Both Inbox settings live under routing.in_app. The quiet_hours
        # column is My Routing's page quiet hours, a separate setting.
        in_app = (pref.routing or {}).get("in_app") or {}
        muted = list(in_app.get("muted_categories") or [])
        quiet = in_app.get("quiet_hours")
    return NotificationPreferencesResponse(
        muted_categories=muted,
        quiet_hours=quiet,
        categories=list(ALL_CATEGORIES),
    )


@router.put(
    "/preferences",
    response_model=NotificationPreferencesResponse,
    summary="Update Inbox category mutes and Inbox quiet hours",
)
async def update_preferences(
    body: NotificationPreferencesUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(reject_api_tokens),
) -> NotificationPreferencesResponse:
    existing = await UserNotificationPrefRepo.get_for_user(db, org_id, user.id)
    routing = dict(existing.routing) if existing and existing.routing else {}
    in_app = dict(routing.get("in_app") or {})

    if body.muted_categories is not None:
        invalid = set(body.muted_categories) - set(ALL_CATEGORIES)
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown categories: {sorted(invalid)}",
            )
        # de-dupe while preserving the canonical category order
        in_app["muted_categories"] = [
            c for c in ALL_CATEGORIES if c in set(body.muted_categories)
        ]

    if body.quiet_hours is not None:
        _validate_quiet_hours(body.quiet_hours)
        in_app["quiet_hours"] = body.quiet_hours.model_dump()

    # Inbox quiet hours sit beside the mutes. This page never writes the
    # quiet_hours column: that is My Routing's, and it holds pages.
    routing["in_app"] = in_app
    await UserNotificationPrefRepo.upsert(db, org_id, user.id, routing=routing)
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
    user: User = Depends(reject_api_tokens),
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
    user: User = Depends(reject_api_tokens),
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
    user: User = Depends(reject_api_tokens),
) -> Response:
    ok = await InAppNotificationRepo.delete(db, org_id, user.id, notification_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
