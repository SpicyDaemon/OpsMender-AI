"""Approval endpoints for Tier 1 human-in-the-loop execution."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import ApprovalListResponse, ApprovalRequestResponse, WSMessage
from backend.api.routes.ws import publish
from backend.db.models import User
from backend.db.repos import ApprovalRequestRepo, SessionRepo

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_ws_message(request) -> WSMessage:
    return WSMessage(
        type="approval_resolved" if request.status != "pending" else "approval_requested",
        data={
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
        },
    )


async def _resolve_request(
    db: AsyncSession,
    request_id: uuid.UUID,
    *,
    decision: str,
    resolver: User,
):
    request = await ApprovalRequestRepo.get_by_id(db, request_id)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found",
        )

    if request.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval request is already {request.status}",
        )

    if _utcnow() >= _as_utc(request.expires_at):
        await ApprovalRequestRepo.resolve(db, request.id, status="expired")
        await SessionRepo.set_status(
            db,
            request.session_id,
            status="timed_out",
            ended_at=_utcnow(),
        )
        await db.commit()
        expired = await ApprovalRequestRepo.get_by_id(db, request.id)
        if expired is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Approval request not found",
            )
        await publish(expired.session_id, _to_ws_message(expired))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval request expired before it could be resolved",
        )

    updated = await ApprovalRequestRepo.resolve(
        db,
        request.id,
        status=decision,
        resolved_by=resolver.id,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval request could not be resolved",
        )

    await SessionRepo.set_status(db, request.session_id, status="active")
    await db.commit()

    resolved = await ApprovalRequestRepo.get_by_id(db, request.id)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found",
        )

    await publish(resolved.session_id, _to_ws_message(resolved))
    return resolved


@router.get("", response_model=ApprovalListResponse, summary="List approval requests")
async def list_approvals(
    status_filter: str | None = Query(default=None, alias="status"),
    session_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = await ApprovalRequestRepo.list(
        db,
        status=status_filter,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    return ApprovalListResponse(items=list(items), total=len(items))


@router.post(
    "/{request_id}/approve",
    response_model=ApprovalRequestResponse,
    summary="Approve a pending request",
)
async def approve_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    return await _resolve_request(db, request_id, decision="approved", resolver=user)


@router.post(
    "/{request_id}/reject",
    response_model=ApprovalRequestResponse,
    summary="Reject a pending request",
)
async def reject_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    return await _resolve_request(db, request_id, decision="rejected", resolver=user)
