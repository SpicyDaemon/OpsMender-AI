"""Outbound webhook trigger management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import require_role
from backend.api.deps import get_current_session_factory, get_db
from backend.api.schemas import (
    WebhookTriggerListResponse,
    WebhookTriggerResponse,
    WebhookTriggerTestResponse,
    WebhookTriggerUpsert,
)
from backend.db.models import User, WebhookTrigger
from backend.db.repos import WebhookTriggerRepo
from backend.webhooks import SESSION_TRIGGER_EVENTS, deliver_test_event

router = APIRouter(prefix="/webhook-triggers", tags=["webhook-triggers"])


def _validate_event_types(event_types: list[str]) -> list[str]:
    cleaned = sorted({item.strip() for item in event_types if item and item.strip()})
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one event type is required",
        )
    invalid = [item for item in cleaned if item not in SESSION_TRIGGER_EVENTS]
    if invalid:
        allowed = ", ".join(sorted(SESSION_TRIGGER_EVENTS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported event types: {', '.join(invalid)}. Allowed: {allowed}",
        )
    return cleaned


def _resolve_token(
    body: WebhookTriggerUpsert,
    existing: WebhookTrigger | None = None,
) -> str | None:
    if body.clear_token:
        return None
    if body.token is None:
        return None if existing is None else existing.token
    if body.token == "":
        return None
    return body.token


def _resolve_headers(
    body: WebhookTriggerUpsert,
    existing: WebhookTrigger | None = None,
) -> dict[str, str] | None:
    if body.clear_headers:
        return None
    if body.headers is None:
        return None if existing is None else existing.headers
    return body.headers


def _to_response(trigger: WebhookTrigger) -> WebhookTriggerResponse:
    return WebhookTriggerResponse(
        id=trigger.id,
        name=trigger.name,
        url=trigger.url,
        format=trigger.format,
        event_types=list(trigger.event_types or []),
        is_active=trigger.is_active,
        created_at=trigger.created_at,
        updated_at=trigger.updated_at,
        last_triggered_at=trigger.last_triggered_at,
        last_error=trigger.last_error,
        header_names=sorted((trigger.headers or {}).keys()),
        has_token=bool(trigger.token),
    )


@router.get(
    "",
    response_model=WebhookTriggerListResponse,
    summary="List outbound webhook triggers",
)
async def list_webhook_triggers(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    items = await WebhookTriggerRepo.list_all(db)
    return WebhookTriggerListResponse(
        items=[_to_response(item) for item in items],
        total=len(items),
    )


@router.post(
    "",
    response_model=WebhookTriggerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an outbound webhook trigger",
)
async def create_webhook_trigger(
    body: WebhookTriggerUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    try:
        trigger = await WebhookTriggerRepo.create(
            db,
            name=body.name,
            url=body.url,
            format=body.format,
            event_types=_validate_event_types(body.event_types),
            headers=_resolve_headers(body),
            token=_resolve_token(body),
            is_active=body.is_active,
        )
        await db.commit()
        await db.refresh(trigger)
        return _to_response(trigger)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook trigger name already exists",
        ) from exc


@router.put(
    "/{trigger_id}",
    response_model=WebhookTriggerResponse,
    summary="Update an outbound webhook trigger",
)
async def update_webhook_trigger(
    trigger_id: uuid.UUID,
    body: WebhookTriggerUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    existing = await WebhookTriggerRepo.get_by_id(db, trigger_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook trigger not found",
        )

    try:
        updated = await WebhookTriggerRepo.update(
            db,
            trigger_id,
            name=body.name,
            url=body.url,
            format=body.format,
            event_types=_validate_event_types(body.event_types),
            headers=_resolve_headers(body, existing),
            token=_resolve_token(body, existing),
            is_active=body.is_active,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Webhook trigger not found",
            )
        await db.refresh(updated)
        return _to_response(updated)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook trigger name already exists",
        ) from exc


@router.delete(
    "/{trigger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an outbound webhook trigger",
)
async def delete_webhook_trigger(
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    deleted = await WebhookTriggerRepo.delete(db, trigger_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook trigger not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{trigger_id}/test",
    response_model=WebhookTriggerTestResponse,
    summary="Send a test payload to an outbound webhook trigger",
)
async def test_webhook_trigger(
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    existing = await WebhookTriggerRepo.get_by_id(db, trigger_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook trigger not found",
        )

    factory = get_current_session_factory()
    success, detail, status_code, event_type = await deliver_test_event(
        factory,
        trigger_id=trigger_id,
    )
    return WebhookTriggerTestResponse(
        success=success,
        detail=detail,
        status_code=status_code,
        event_type=event_type,
    )
