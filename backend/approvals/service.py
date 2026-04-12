"""Shared approval service for Tier 1 destructive actions."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import ApprovalRequest
from backend.db.repos import ApprovalRequestRepo, SessionRepo


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class ApprovalResolution:
    """Final resolution for one approval request."""

    request: ApprovalRequest
    block_reason: str | None = None


class ApprovalService:
    """Create, publish, and wait on approval requests."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        timeout_seconds: int = 900,
        poll_interval_seconds: float = 1.0,
        publisher: Callable[[uuid.UUID, dict[str, Any]], Awaitable[None]] | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._publisher = publisher
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
                session_id=session_id,
                action=action,
                justification=justification,
                expires_at=expires_at,
            )
            await SessionRepo.set_status(db, session_id, status="awaiting_approval")
            await db.commit()
            await db.refresh(request)

        await self._publish(
            session_id,
            {
                "type": "approval_requested",
                "data": self._request_payload(request),
            },
        )

        while True:
            async with self._session_factory() as db:
                request = await ApprovalRequestRepo.get_by_id(db, request.id)
                if request is None:
                    raise RuntimeError(f"Approval request not found: {request.id}")

                if request.status != "pending":
                    await self._sync_session_status(db, request)
                    await db.commit()
                    await db.refresh(request)
                    await self._publish(
                        session_id,
                        {
                            "type": "approval_resolved",
                            "data": self._request_payload(request),
                        },
                    )
                    return ApprovalResolution(
                        request=request,
                        block_reason=self._block_reason(request.status),
                    )

                if self._now_fn() >= _as_utc(request.expires_at):
                    await ApprovalRequestRepo.resolve(db, request.id, status="expired")
                    expired = await ApprovalRequestRepo.get_by_id(db, request.id)
                    if expired is None:
                        raise RuntimeError(f"Approval request not found: {request.id}")
                    await self._sync_session_status(db, expired)
                    await db.commit()
                    await db.refresh(expired)
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

    async def _sync_session_status(
        self,
        db: AsyncSession,
        request: ApprovalRequest,
    ) -> None:
        if request.status == "expired":
            await SessionRepo.set_status(
                db,
                request.session_id,
                status="timed_out",
                ended_at=self._now_fn(),
            )
            return

        await SessionRepo.set_status(db, request.session_id, status="active")

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
            "requested_at": request.requested_at.isoformat(),
            "resolved_at": (
                request.resolved_at.isoformat() if request.resolved_at else None
            ),
            "resolved_by": str(request.resolved_by) if request.resolved_by else None,
            "expires_at": _as_utc(request.expires_at).isoformat(),
        }

    @staticmethod
    def _block_reason(status: str) -> str | None:
        if status == "rejected":
            return "Approval rejected by human operator"
        if status == "expired":
            return "Approval timed out before human response"
        return None
