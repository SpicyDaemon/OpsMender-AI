"""Shared approval service for Tier 1 destructive actions."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import ApprovalRequest, Session
from backend.db.repos import ApprovalRequestRepo, SessionRepo
from backend.notifications import (
    CATEGORY_APPROVAL,
    emit_to_users,
    org_user_ids_with_roles,
)

# Statuses a person set by stopping or cancelling the session.
SESSION_ENDED_BY_A_PERSON = frozenset({"stopped", "cancelled"})


def session_end_reason(session: Session | None, tier: int | None = None) -> str | None:
    """Why a run may no longer act for *session*, or ``None`` while it may.

    A person ended the session (Stop, or a queued session cancelled), or, for
    a run that drives the session at *tier*, an Override moved it to another
    tier and the app runs it from there (M1-105)."""
    if session is None:
        return None
    if session.status in SESSION_ENDED_BY_A_PERSON:
        return f"the session was {session.status}"
    if tier is not None and int(session.tier) != tier:
        return f"the session was overridden to Tier {int(session.tier)}"
    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _action_summary(action: dict[str, Any]) -> str:
    """Short human label for an approval action dict (notification body).
    A session's action and a workflow step name the tool as ``tool_name``
    (M1-169); the other keys are older shapes. The name is the model's
    text: it is kept to one line of at most 200 characters."""
    if not isinstance(action, dict):
        return "Review the proposed action."
    for key in ("tool_name", "tool", "name", "action"):
        tool = action.get(key)
        if isinstance(tool, str) and tool.strip():
            return f"Tool: {' '.join(tool.split())[:200]}"
    return "Review the proposed action."


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ApprovalNotAsked(Exception):
    """An approval service declined to ask, because the session it belongs to
    has ended or was overridden (M1-105). No request was left pending. The
    tier gate and a workflow step block the action with this exception's text
    as the reason and go on, so what the same step already recorded is kept."""


@dataclass
class ApprovalResolution:
    """Final resolution for one approval request.

    ``guidance`` carries the operator's free-text steering when the request was
    resolved with the Tier 1 ``redirected`` decision; ``None`` otherwise.
    """

    request: ApprovalRequest
    block_reason: str | None = None
    guidance: str | None = None


class ApprovalService:
    """Create, publish, and wait on approval requests."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        org_id: uuid.UUID,
        timeout_seconds: int = 900,
        poll_interval_seconds: float = 1.0,
        publisher: Callable[[uuid.UUID, dict[str, Any]], Awaitable[None]] | None = None,
        status_notifier: Callable[[uuid.UUID, str], None] | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        session_tier: int | None = None,
    ) -> None:
        # ``session_tier`` binds the service to a run that drives its session
        # at that tier (the CLI's): once an Override moves the session to
        # another tier, the service neither asks nor writes the session.
        self._session_factory = session_factory
        self._org_id = org_id
        self._session_tier = session_tier
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._publisher = publisher
        self._status_notifier = status_notifier
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn

    async def request_and_wait(
        self,
        *,
        session_id: uuid.UUID,
        action: dict[str, Any],
        justification: str | None = None,
    ) -> ApprovalResolution:
        """Create an approval request and wait until it resolves."""
        expires_at = self._now_fn() + timedelta(seconds=self._timeout_seconds)

        async with self._session_factory() as db:
            request = await ApprovalRequestRepo.create(
                db,
                self._org_id,
                session_id=session_id,
                action=action,
                justification=justification,
                expires_at=expires_at,
            )
            # One conditional write, so a Stop that committed after the
            # caller last read the session is never turned back into
            # awaiting_approval; then nobody is asked (M1-105).
            if not await self._set_session_status(
                db, session_id, status="awaiting_approval"
            ):
                await db.rollback()
                session = await SessionRepo.get_by_id(db, self._org_id, session_id)
                reason = session_end_reason(session, self._session_tier)
                raise ApprovalNotAsked(
                    f"{reason or 'the session has ended'}; the approval was not asked"
                )
            # Notify everyone who can act on it (admins + operators) that a
            # Tier 1 action is waiting. Best-effort; never blocks the approval.
            session = await SessionRepo.get_by_id(db, self._org_id, session_id)
            incident_id = session.incident_id if session else None
            approver_ids = await org_user_ids_with_roles(
                db, self._org_id, ("admin", "operator")
            )
            await emit_to_users(
                db,
                self._org_id,
                approver_ids,
                event_type="approval.requested",
                category=CATEGORY_APPROVAL,
                title="Approval needed for an AI action",
                body=justification or _action_summary(action),
                link=(f"/dashboard/incidents/{incident_id}" if incident_id else None),
                incident_id=incident_id,
                session_id=session_id,
            )
            await db.commit()
            persisted_request = await ApprovalRequestRepo.get_by_id(
                db, self._org_id, request.id
            )
            if persisted_request is None:
                raise RuntimeError(
                    f"Approval request not found after create: {request.id}"
                )
            request = persisted_request
        self._notify_session_status(session_id, "awaiting_approval")

        await self._publish(
            session_id,
            {
                "type": "approval_requested",
                "data": self._request_payload(request),
            },
        )

        while True:
            async with self._session_factory() as db:
                request = await ApprovalRequestRepo.get_by_id(
                    db, self._org_id, request.id
                )
                if request is None:
                    raise RuntimeError(f"Approval request not found: {request.id}")

                if request.status != "pending":
                    next_status = await self._sync_session_status(db, request)
                    await db.commit()
                    persisted_request = await ApprovalRequestRepo.get_by_id(
                        db, self._org_id, request.id
                    )
                    if persisted_request is None:
                        raise RuntimeError(
                            f"Approval request not found after resolve: {request.id}"
                        )
                    request = persisted_request
                    self._notify_session_status(session_id, next_status)
                    await self._publish(
                        session_id,
                        {
                            "type": "approval_resolved",
                            "data": self._request_payload(request),
                        },
                    )
                    if next_status is None and request.status == "expired":
                        session = await SessionRepo.get_by_id(
                            db, self._org_id, session_id
                        )
                        ended = session_end_reason(session, self._session_tier)
                        if ended is not None:
                            return self._ended_resolution(request, ended)
                    return ApprovalResolution(
                        request=request,
                        block_reason=self._block_reason(request.status),
                        guidance=(
                            request.resolution_note
                            if request.status == "redirected"
                            else None
                        ),
                    )

                # A Stop or an Override expires the pending requests it can
                # see; one created as it committed is expired here, so the
                # wait never outlives the session (M1-105). The next pass
                # reports it (an answer that landed first is kept).
                session = await SessionRepo.get_by_id(db, self._org_id, session_id)
                if session_end_reason(session, self._session_tier) is not None:
                    await ApprovalRequestRepo.resolve(
                        db, self._org_id, request.id, status="expired"
                    )
                    await db.commit()
                    continue

                if self._now_fn() >= _as_utc(request.expires_at):
                    await ApprovalRequestRepo.resolve(
                        db, self._org_id, request.id, status="expired"
                    )
                    expired = await ApprovalRequestRepo.get_by_id(
                        db, self._org_id, request.id
                    )
                    if expired is None:
                        raise RuntimeError(f"Approval request not found: {request.id}")
                    next_status = await self._sync_session_status(db, expired)
                    await db.commit()
                    persisted_expired = await ApprovalRequestRepo.get_by_id(
                        db, self._org_id, expired.id
                    )
                    if persisted_expired is None:
                        raise RuntimeError(
                            f"Approval request not found after expiry: {expired.id}"
                        )
                    expired = persisted_expired
                    self._notify_session_status(session_id, next_status)
                    await self._publish(
                        session_id,
                        {
                            "type": "approval_resolved",
                            "data": self._request_payload(expired),
                        },
                    )
                    return ApprovalResolution(
                        request=expired,
                        block_reason=self._block_reason("expired"),
                    )

            await self._sleep_fn(self._poll_interval_seconds)

    async def _set_session_status(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        *,
        status: str,
        ended_at: datetime | None = None,
    ) -> bool:
        """Write the session's status unless a person ended it or, for a
        service bound to a tier, an Override moved it. A single conditional
        UPDATE, so a Stop that commits in between is never overwritten
        (M1-105). True when the row was written."""
        values: dict[str, Any] = {"status": status}
        if ended_at is not None:
            values["ended_at"] = ended_at
        stmt = (
            update(Session)
            .where(Session.org_id == self._org_id)
            .where(Session.id == session_id)
            .where(Session.status.notin_(SESSION_ENDED_BY_A_PERSON))
            .values(**values)
        )
        if self._session_tier is not None:
            stmt = stmt.where(Session.tier == self._session_tier)
        result = await db.execute(stmt)
        return bool(result.rowcount)

    async def _sync_session_status(
        self,
        db: AsyncSession,
        request: ApprovalRequest,
    ) -> str | None:
        # A person who stopped the session ended it: the approval the stop
        # expired, or one answered as it stopped, does not bring it back as
        # timed out or active, and no new status is announced (M1-105).
        if request.status == "expired":
            written = await self._set_session_status(
                db,
                request.session_id,
                status="timed_out",
                ended_at=self._now_fn(),
            )
            return "timed_out" if written else None

        # approved / rejected / redirected all return the session to "active":
        # the workflow keeps running (execute the approved action, skip the
        # rejected one, or loop back to plan with redirect guidance).
        written = await self._set_session_status(
            db, request.session_id, status="active"
        )
        return "active" if written else None

    def _ended_resolution(
        self, request: ApprovalRequest, ended: str
    ) -> ApprovalResolution:
        """The resolution of a wait the session's end cut short."""
        return ApprovalResolution(
            request=request,
            block_reason=f"{ended} during the approval wait",
        )

    def _notify_session_status(self, session_id: uuid.UUID, status: str | None) -> None:
        if self._status_notifier is not None and status is not None:
            self._status_notifier(session_id, status)

    async def _publish(self, session_id: uuid.UUID, event: dict[str, Any]) -> None:
        if self._publisher is not None:
            await self._publisher(session_id, event)

    def _request_payload(self, request: ApprovalRequest) -> dict[str, Any]:
        return {
            "id": str(request.id),
            "session_id": str(request.session_id),
            "action": request.action,
            "justification": request.justification,
            "status": request.status,
            "resolution_note": request.resolution_note,
            "requested_at": request.requested_at.isoformat(),
            "resolved_at": (
                request.resolved_at.isoformat() if request.resolved_at else None
            ),
            "resolved_by": str(request.resolved_by) if request.resolved_by else None,
            "expires_at": _as_utc(request.expires_at).isoformat(),
            "extension_count": request.extension_count,
            "extension_notified_at": (
                _as_utc(request.extension_notified_at).isoformat()
                if request.extension_notified_at
                else None
            ),
        }

    @staticmethod
    def _block_reason(status: str) -> str | None:
        if status == "rejected":
            return "Approval rejected by human operator"
        if status == "expired":
            return "Approval timed out before human response"
        # "redirected" is not a block - the workflow re-plans with guidance.
        return None
