"""In-app notification center (v1.2 — the per-user bell).

Public surface is :func:`emit_notification` / :func:`emit_to_users` plus the
category constants. Callers raise notifications; persistence, per-category
mute, quiet-hours, and live WebSocket push are handled here.
"""

from backend.notifications.service import (
    ALL_CATEGORIES,
    CATEGORY_ACCOUNT,
    CATEGORY_APPROVAL,
    CATEGORY_INCIDENT,
    CATEGORY_MENTION,
    CATEGORY_RELIABILITY,
    CATEGORY_SESSION,
    emit_notification,
    emit_to_users,
)

__all__ = [
    "ALL_CATEGORIES",
    "CATEGORY_ACCOUNT",
    "CATEGORY_APPROVAL",
    "CATEGORY_INCIDENT",
    "CATEGORY_MENTION",
    "CATEGORY_RELIABILITY",
    "CATEGORY_SESSION",
    "emit_notification",
    "emit_to_users",
]
