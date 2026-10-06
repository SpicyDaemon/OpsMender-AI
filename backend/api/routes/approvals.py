"""Approval endpoints for Tier 1 human-in-the-loop execution."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    ApprovalListResponse,
    ApprovalRedirectRequest,
    ApprovalRequestResponse,
    WSMessage,
)
from backend.api.routes.ws import publish
from backend.approvals.decisions import can_decide, decide
from backend.db.models import ApprovalRequest, Session as SessionModel, User
from backend.db.repos import ApprovalRequestRepo
from backend.paging.reassign import APPROVAL_FORBIDDEN

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_ws_message(request) -> WSMessage:
    return WSMessage(
        type="approval_resolved"
        if request.status != "pending"
        else "approval_requested",
        data={
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
        },
    )


async def _session_tiers(
    db: AsyncSession,
    org_id: uuid.UUID,
    session_ids: set[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not session_ids:
        return {}
    result = await db.execute(
        select(SessionModel.id, SessionModel.tier)
        .where(SessionModel.org_id == org_id)
        .where(SessionModel.id.in_(session_ids))
    )
    return {row.id: row.tier for row in result}


def _approval_response(
    request: ApprovalRequest,
    *,
    session_tier: int | None,
) -> ApprovalRequestResponse:
    return ApprovalRequestResponse.model_validate(request).model_copy(
        update={"session_tier": session_tier}
    )


async def _resolve_request(
    db: AsyncSession,
    org_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    app_request: Request,
    decision: str,
    resolver: User,
    resolution_note: str | None = None,
):
    result = await decide(
        db,
        org_id,
        request_id,
        decision=decision,
        resolver=resolver,
        resolution_note=resolution_note,
    )
    if result.outcome == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found",
        )
    if result.outcome == "not_pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval request is already {result.request.status}",
        )
    if result.outcome == "forbidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=APPROVAL_FORBIDDEN
        )
    if result.outcome == "expired":
        from backend.services.session_orchestration import schedule_queue_drain

        schedule_queue_drain(app_request.app, org_id=org_id)
        if result.request is not None:
            await publish(result.request.session_id, _to_ws_message(result.request))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval request expired before it could be resolved",
        )
    if result.outcome == "failed" or result.request is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval request could not be resolved",
        )

    resolved = result.request
    await publish(resolved.session_id, _to_ws_message(resolved))
    tiers = await _session_tiers(db, org_id, {resolved.session_id})
    return _approval_response(
        resolved,
        session_tier=tiers.get(resolved.session_id),
    )


@router.get("", response_model=ApprovalListResponse, summary="List approval requests")
async def list_approvals(
    status_filter: str | None = Query(default=None, alias="status"),
    session_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await ApprovalRequestRepo.list(
        db,
        org_id,
        status=status_filter,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )
    tiers = await _session_tiers(db, org_id, {item.session_id for item in items})
    status_counts = await ApprovalRequestRepo.count_by_status(
        db, org_id, session_id=session_id
    )
    return ApprovalListResponse(
        items=[
            _approval_response(
                item,
                session_tier=tiers.get(item.session_id),
            )
            for item in items
        ],
        total=len(items),
        status_counts=status_counts,
    )


@router.post(
    "/{request_id}/approve",
    response_model=ApprovalRequestResponse,
    summary="Approve a pending request",
)
async def approve_request(
    request_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    return await _resolve_request(
        db,
        org_id,
        request_id,
        app_request=request,
        decision="approved",
        resolver=user,
    )


@router.post(
    "/{request_id}/reject",
    response_model=ApprovalRequestResponse,
    summary="Reject a pending request",
)
async def reject_request(
    request_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    return await _resolve_request(
        db,
        org_id,
        request_id,
        app_request=request,
        decision="rejected",
        resolver=user,
    )


@router.post(
    "/{request_id}/redirect",
    response_model=ApprovalRequestResponse,
    summary="Redirect a pending request with free-text steering (Tier 1)",
)
async def redirect_request(
    request_id: uuid.UUID,
    body: ApprovalRedirectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Decline the proposed action and steer the AI.

    The operator's ``guidance`` is stored on the request and fed back into the
    workflow, which loops to the plan node and re-proposes with the steering in
    context (the Tier 1 interactive co-pilot loop).
    """
    return await _resolve_request(
        db,
        org_id,
        request_id,
        app_request=request,
        decision="redirected",
        resolver=user,
        resolution_note=body.guidance.strip(),
    )


@router.post(
    "/{request_id}/extend",
    response_model=ApprovalRequestResponse,
    summary="Extend a pending approval's model-slot hold",
)
async def extend_request(
    request_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    approval = await ApprovalRequestRepo.get_by_id(db, org_id, request_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if not await can_decide(db, org_id, approval, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=APPROVAL_FORBIDDEN
        )
    if approval.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Approval request is already {approval.status}",
        )
    now = _utcnow()
    if now >= _as_utc(approval.expires_at):
        raise HTTPException(status_code=409, detail="Approval request has expired")
    expires_at = now + timedelta(
        seconds=request.app.state.config.sessions.approval_hold_ttl_seconds
    )
    extended = await ApprovalRequestRepo.extend(
        db,
        org_id,
        request_id,
        expires_at=expires_at,
    )
    if not extended:
        raise HTTPException(
            status_code=409,
            detail="Approval request could not be extended",
        )
    await db.commit()
    updated = await ApprovalRequestRepo.get_by_id(db, org_id, request_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    await publish(updated.session_id, _to_ws_message(updated))
    tiers = await _session_tiers(db, org_id, {updated.session_id})
    return _approval_response(
        updated,
        session_tier=tiers.get(updated.session_id),
    )
