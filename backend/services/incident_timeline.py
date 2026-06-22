"""Automatic incident-comment timeline (v2 Phase 4).

Lifecycle actions — acknowledge / resolve / escalate / AI-session started/ended —
record an incident comment with source ``lifecycle`` so the incident timeline
reads as one human-legible narrative regardless of where the action originated
(web UI, chat, slash command, or the AI session runner).

These comments are advisory context only — like operator notes, they never affect
enforcement or the AI workflow. They are written inside the caller's existing
transaction so they commit atomically with the action they describe.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.repos import IncidentCommentRepo

log = logging.getLogger(__name__)

#: ``IncidentComment.source`` marker for auto-generated lifecycle entries.
LIFECYCLE_SOURCE = "lifecycle"


async def record_lifecycle_comment(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID | None,
    body: str,
    author_user_id: uuid.UUID | None = None,
):
    """Append a ``lifecycle`` incident comment. No-op when there is no incident.

    Best-effort: a failure here must never break the lifecycle action it
    annotates, so it is logged and swallowed.
    """
    if incident_id is None:
        return None
    try:
        return await IncidentCommentRepo.create(
            db,
            org_id,
            incident_id=incident_id,
            body=body,
            author_user_id=author_user_id,
            source=LIFECYCLE_SOURCE,
        )
    except Exception:  # pragma: no cover - advisory side-effect only
        log.warning(
            "lifecycle comment failed for incident %s", incident_id, exc_info=True
        )
        return None
