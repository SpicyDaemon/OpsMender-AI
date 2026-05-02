"""External chat bot connector management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    BotConnectorListResponse,
    BotConnectorResponse,
    BotConnectorTestResponse,
    BotConnectorUpsert,
    BotUserLinkCreate,
    BotUserLinkListResponse,
    BotUserLinkResponse,
)
from backend.db.models import BotConnector, User
from backend.db.repos import BotConnectorRepo, BotUserLinkRepo, UserRepo

router = APIRouter(prefix="/bot-connectors", tags=["bot-connectors"])

ALLOWED_CAPABILITIES = {
    "incident_lookup",
    "session_status",
    "approvals",
    "copilot_chat",
    "notifications",
}

REQUIRED_CREDENTIAL_KEYS = {
    "telegram": ("bot_token",),
    "signal": ("service_url", "bot_number", "webhook_secret"),
    "whatsapp": ("access_token", "phone_number_id"),
    "custom": (),
}


def _validate_capabilities(capabilities: list[str]) -> list[str]:
    cleaned = sorted({item.strip() for item in capabilities if item and item.strip()})
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one capability is required",
        )

    invalid = [item for item in cleaned if item not in ALLOWED_CAPABILITIES]
    if invalid:
        allowed = ", ".join(sorted(ALLOWED_CAPABILITIES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported capabilities: {', '.join(invalid)}. Allowed: {allowed}",
        )
    return cleaned


def _resolve_credentials(
    body: BotConnectorUpsert,
    existing: BotConnector | None = None,
) -> dict | None:
    if body.clear_credentials:
        return None
    if body.credentials is None:
        return None if existing is None else existing.credentials
    return body.credentials


def _to_response(connector: BotConnector) -> BotConnectorResponse:
    credentials = connector.credentials or {}
    return BotConnectorResponse(
        id=connector.id,
        name=connector.name,
        platform=connector.platform,
        config=connector.config,
        allowed_capabilities=list(connector.allowed_capabilities or []),
        status=connector.status,
        is_enabled=connector.is_enabled,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
        last_checked_at=connector.last_checked_at,
        last_error=connector.last_error,
        credential_keys=sorted(credentials.keys()),
        has_credentials=bool(credentials),
    )


def _test_connector_configuration(
    connector: BotConnector,
) -> tuple[bool, str, str, str | None]:
    if not connector.is_enabled:
        return False, "Connector is disabled.", "disabled", "Connector is disabled."

    credentials = connector.credentials or {}
    required_keys = REQUIRED_CREDENTIAL_KEYS.get(connector.platform, ())
    missing = [key for key in required_keys if not credentials.get(key)]
    if missing:
        detail = f"Missing required credential keys: {', '.join(missing)}."
        return False, detail, "not_configured", detail

    if not connector.allowed_capabilities:
        detail = "At least one allowed capability is required."
        return False, detail, "error", detail

    return (
        True,
        "Connector configuration looks ready. Platform-specific delivery checks will run once that connector is implemented.",
        "healthy",
        None,
    )


@router.get(
    "",
    response_model=BotConnectorListResponse,
    summary="List external chat bot connectors",
)
async def list_bot_connectors(
    platform: str | None = Query(default=None),
    enabled_only: bool = False,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    items = await BotConnectorRepo.list_all(
        db,
        org_id,
        platform=platform,
        enabled_only=enabled_only,
    )
    return BotConnectorListResponse(
        items=[_to_response(item) for item in items],
        total=len(items),
    )


@router.post(
    "",
    response_model=BotConnectorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an external chat bot connector",
)
async def create_bot_connector(
    body: BotConnectorUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    try:
        connector = await BotConnectorRepo.create(
            db,
            org_id,
            name=body.name,
            platform=body.platform,
            config=body.config,
            credentials=_resolve_credentials(body),
            allowed_capabilities=_validate_capabilities(body.allowed_capabilities),
            status="configured" if body.credentials else body.status,
            is_enabled=body.is_enabled,
        )
        await db.commit()
        await db.refresh(connector)
        return _to_response(connector)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bot connector name already exists",
        ) from exc


@router.put(
    "/{connector_id}",
    response_model=BotConnectorResponse,
    summary="Update an external chat bot connector",
)
async def update_bot_connector(
    connector_id: uuid.UUID,
    body: BotConnectorUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )

    credentials = _resolve_credentials(body, existing)
    try:
        updated = await BotConnectorRepo.update(
            db,
            org_id,
            connector_id,
            name=body.name,
            platform=body.platform,
            config=body.config,
            credentials=credentials,
            allowed_capabilities=_validate_capabilities(body.allowed_capabilities),
            status=(
                "configured"
                if credentials and body.status == "not_configured"
                else body.status
            ),
            is_enabled=body.is_enabled,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bot connector not found",
            )
        await db.refresh(updated)
        return _to_response(updated)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bot connector name already exists",
        ) from exc


@router.post(
    "/{connector_id}/test",
    response_model=BotConnectorTestResponse,
    summary="Validate an external chat bot connector configuration",
)
async def test_bot_connector(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    connector = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )

    success, detail, next_status, error = _test_connector_configuration(connector)
    await BotConnectorRepo.mark_status(
        db,
        org_id,
        connector_id,
        status=next_status,
        error=error,
    )
    await db.commit()
    return BotConnectorTestResponse(
        success=success,
        detail=detail,
        status=next_status,
    )


@router.delete(
    "/{connector_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an external chat bot connector",
)
async def delete_bot_connector(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await BotConnectorRepo.delete(db, org_id, connector_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Bot user links — map external chat-platform identities to AIM users
# ---------------------------------------------------------------------------


async def _link_to_response(db: AsyncSession, link) -> BotUserLinkResponse:
    aim_user = await UserRepo.get_by_id(db, link.aim_user_id)
    return BotUserLinkResponse(
        id=link.id,
        connector_id=link.connector_id,
        platform_user_id=link.platform_user_id,
        aim_user_id=link.aim_user_id,
        aim_username=aim_user.username if aim_user else "(deleted)",
        aim_role=aim_user.role if aim_user else "viewer",
        created_at=link.created_at,
    )


@router.get(
    "/{connector_id}/user-links",
    response_model=BotUserLinkListResponse,
    summary="List chat-platform identity mappings for a connector",
)
async def list_bot_user_links(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    connector = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )
    links = await BotUserLinkRepo.list_by_connector(db, org_id, connector_id)
    items = [await _link_to_response(db, link) for link in links]
    return BotUserLinkListResponse(items=items, total=len(items))


@router.post(
    "/{connector_id}/user-links",
    response_model=BotUserLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Map a chat-platform user to an AIM user",
)
async def create_bot_user_link(
    connector_id: uuid.UUID,
    body: BotUserLinkCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    connector = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )

    aim_user = await UserRepo.get_by_id(db, body.aim_user_id)
    if aim_user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AIM user not found",
        )

    platform_user_id = body.platform_user_id.strip()
    if not platform_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="platform_user_id is required",
        )

    existing = await BotUserLinkRepo.get_by_platform_user(
        db,
        org_id,
        connector_id=connector_id,
        platform_user_id=platform_user_id,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This platform user is already linked on this connector",
        )

    link = await BotUserLinkRepo.create(
        db,
        org_id,
        connector_id=connector_id,
        platform_user_id=platform_user_id,
        aim_user_id=body.aim_user_id,
        created_by=user.id,
    )
    await db.commit()
    await db.refresh(link)
    return await _link_to_response(db, link)


@router.delete(
    "/{connector_id}/user-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a chat-platform identity mapping",
)
async def delete_bot_user_link(
    connector_id: uuid.UUID,
    link_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    link = await BotUserLinkRepo.get_by_id(db, org_id, link_id)
    if link is None or link.connector_id != connector_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Link not found",
        )
    await BotUserLinkRepo.delete(db, org_id, link_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
