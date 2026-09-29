"""One decision path for approval requests, from the web or from chat.

Both surfaces must refuse an expired request, record who decided, and count
the decision as the incident owner's activity (KI-046).
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ApprovalRequest
from backend.db.repos import ApprovalRequestRepo, AuditEntryRepo, SessionRepo


@dataclasses.dataclass
class Decision:
    # "decided" | "expired" | "not_found" | "not_pending" | "failed"
    outcome: str
    request: ApprovalRequest | None


def _as_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


async def decide(
    db: AsyncSession,
    org_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    decision: str,
    resolver_id: uuid.UUID,
    resolution_note: str | None = None,
) -> Decision:
    """Approve or reject a pending request, or expire it if it's too late."""
    request = await ApprovalRequestRepo.get_by_id(db, org_id, request_id)
    if request is None:
        return Decision("not_found", None)
    if request.status != "pending":
        return Decision("not_pending", request)

    now = datetime.now(timezone.utc)
    if now >= _as_utc(request.expires_at):
        await ApprovalRequestRepo.resolve(db, org_id, request.id, status="expired")
        await SessionRepo.set_status(
            db, org_id, request.session_id, status="timed_out", ended_at=now
        )
        await db.commit()
        return Decision(
            "expired", await ApprovalRequestRepo.get_by_id(db, org_id, request.id)
        )

    updated = await ApprovalRequestRepo.resolve(
        db,
        org_id,
        request.id,
        status=decision,
        resolved_by=resolver_id,
        resolution_note=resolution_note,
    )
    if not updated:
        return Decision("failed", request)

    await SessionRepo.set_status(db, org_id, request.session_id, status="active")
    session = await SessionRepo.get_by_id(db, org_id, request.session_id)
    if session is not None and session.incident_id is not None:
        from backend.paging.escalation import record_assignee_activity

        # An approval decision by the incident's owner is activity on the lock.
        await record_assignee_activity(
            db, org_id, incident_id=session.incident_id, actor_id=resolver_id
        )
    await AuditEntryRepo.create(
        db,
        org_id,
        session_id=request.session_id,
        tier=session.tier if session is not None else 0,
        entry_type="approval_decision",
        tool_name="approval.decide",
        tool_parameters={
            "actor_id": str(resolver_id),
            "approval_id": str(request.id),
            "route": db.sync_session.info.get("admin_audit", {}).get(
                "route", "interactive_action"
            ),
        },
        result={"before": {"status": "pending"}, "after": {"status": decision}},
    )
    await db.commit()
    return Decision(
        "decided", await ApprovalRequestRepo.get_by_id(db, org_id, request.id)
    )
