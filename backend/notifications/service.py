"""In-app notification center service (v1.2 — the bell).

Central entry point for raising a per-user in-app notification. It:

- persists the record so it survives reloads (the bell / center reads it back),
- respects the user's per-category **mute** and **quiet-hours** preferences,
- pushes a live ``notification`` event over the per-user WebSocket so an open
  bell updates immediately.

Design notes:

- **Never raises into the caller.** A notification is a side effect; a failure
  to record one must not break an incident transition or an approval.
- **Mute** suppresses the notification entirely (the user opted out).
- **Quiet hours** suppress only the *live push* — the row is still stored so
  the user catches up when they next open the bell.
- The caller owns the transaction boundary (emit only flushes), matching the
  rest of the repo layer.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import InAppNotification
from backend.db.repos import InAppNotificationRepo, UserNotificationPrefRepo

logger = logging.getLogger(__name__)


# Coarse categories — the unit a user mutes against. Fine-grained
# ``event_type`` strings (e.g. "incident.assigned") map onto one of these.
CATEGORY_INCIDENT = "incident"
CATEGORY_APPROVAL = "approval"
CATEGORY_SESSION = "session"
CATEGORY_MENTION = "mention"
CATEGORY_RELIABILITY = "reliability"
CATEGORY_ACCOUNT = "account"

ALL_CATEGORIES = (
    CATEGORY_INCIDENT,
    CATEGORY_APPROVAL,
    CATEGORY_SESSION,
    CATEGORY_MENTION,
    CATEGORY_RELIABILITY,
    CATEGORY_ACCOUNT,
)


def _in_quiet_hours(quiet_hours: dict | None, now: datetime) -> bool:
    """True if *now* falls inside the user's configured quiet-hours window.

    Shape: ``{"enabled": bool, "start": "HH:MM", "end": "HH:MM", "tz": "IANA"}``.
    Handles windows that wrap past midnight (e.g. 22:00 → 07:00). Any malformed
    value disables the window rather than raising.
    """
    if not quiet_hours or not quiet_hours.get("enabled"):
        return False
    start_s = quiet_hours.get("start")
    end_s = quiet_hours.get("end")
    if not start_s or not end_s:
        return False
    try:
        tz = ZoneInfo(str(quiet_hours.get("tz") or "UTC"))
    except Exception:
        tz = timezone.utc
    try:
        sh, sm = (int(x) for x in str(start_s).split(":"))
        eh, em = (int(x) for x in str(end_s).split(":"))
        start, end = time(sh, sm), time(eh, em)
    except (ValueError, TypeError):
        return False
    local_now = now.astimezone(tz).time()
    if start <= end:
        return start <= local_now < end
    return local_now >= start or local_now < end


def _muted_categories(routing: dict | None) -> set[str]:
    """Categories the user has muted, read from ``prefs.routing['in_app']``."""
    if not routing:
        return set()
    in_app = routing.get("in_app")
    if not isinstance(in_app, dict):
        return set()
    muted = in_app.get("muted_categories")
    if isinstance(muted, list):
        return {str(c) for c in muted}
    return set()


async def _push(user_id: uuid.UUID, notification: InAppNotification) -> None:
    """Best-effort live push of *notification* to the user's open tabs."""
    # Imported lazily to avoid a route<->service import cycle at module load.
    from backend.api.routes.ws import publish_user
    from backend.api.schemas import WSMessage

    await publish_user(
        user_id,
        WSMessage(
            type="notification",
            data={
                "id": str(notification.id),
                "event_type": notification.event_type,
                "category": notification.category,
                "title": notification.title,
                "body": notification.body,
                "link": notification.link,
                "incident_id": (
                    str(notification.incident_id)
                    if notification.incident_id
                    else None
                ),
                "session_id": (
                    str(notification.session_id)
                    if notification.session_id
                    else None
                ),
                "read_at": None,
                "created_at": notification.created_at.isoformat(),
            },
        ),
    )


async def emit_notification(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    event_type: str,
    category: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    incident_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
) -> InAppNotification | None:
    """Persist + (best-effort) live-push one in-app notification.

    Returns the created row, or ``None`` if the user muted this category or an
    error was swallowed. Quiet hours suppress only the live push.
    """
    try:
        pref = await UserNotificationPrefRepo.get_for_user(db, org_id, user_id)
        if pref is not None and category in _muted_categories(pref.routing):
            return None

        notification = await InAppNotificationRepo.create(
            db,
            org_id,
            user_id,
            event_type=event_type,
            category=category,
            title=title,
            body=body,
            link=link,
            incident_id=incident_id,
            session_id=session_id,
        )

        quiet = pref is not None and _in_quiet_hours(
            pref.quiet_hours, datetime.now(timezone.utc)
        )
        if not quiet:
            await _push(user_id, notification)
        return notification
    except Exception:
        logger.exception(
            "Failed to emit in-app notification (%s) for user %s", event_type, user_id
        )
        return None


async def emit_to_users(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_ids: Iterable[uuid.UUID | None],
    *,
    event_type: str,
    category: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    incident_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
) -> None:
    """Emit the same notification to several users, de-duplicating ids."""
    seen: set[uuid.UUID] = set()
    for uid in user_ids:
        if uid is None or uid in seen:
            continue
        seen.add(uid)
        await emit_notification(
            db,
            org_id,
            uid,
            event_type=event_type,
            category=category,
            title=title,
            body=body,
            link=link,
            incident_id=incident_id,
            session_id=session_id,
        )
