"""Named REST API token management."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import actor_label, get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    ApiTokenCreate,
    ApiTokenCreateResponse,
    ApiTokenListResponse,
    ApiTokenResponse,
)
from backend.auth.api_tokens import mint_api_token
from backend.db.models import User
from backend.db.repos import ApiTokenRepo, AuditEntryRepo

router = APIRouter(prefix="/api/v1/api-tokens", tags=["api-tokens"])


def _response(token, *, secret: str | None = None):
    base = ApiTokenResponse.model_validate(token)
    if secret is None:
        return base
    return ApiTokenCreateResponse(**base.model_dump(), token=secret)


async def _audit_token_change(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    tool_name: str,
    actor: str,
    token_name: str,
    token_role: str,
    token_id: uuid.UUID,
    extra: dict[str, Any] | None = None,
) -> None:
    await AuditEntryRepo.create(
        db,
        org_id,
        session_id=None,
        tier=0,
        entry_type="api_token_change",
        tool_name=tool_name,
        tool_parameters={
            "actor": actor,
            "token_id": str(token_id),
            "name": token_name,
            "role": token_role,
            **(extra or {}),
        },
        result={"ok": True},
    )


@router.get("", response_model=ApiTokenListResponse)
async def list_api_tokens(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    rows = await ApiTokenRepo.list_by_org(db, org_id)
    return ApiTokenListResponse(
        items=[ApiTokenResponse.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.post(
    "",
    response_model=ApiTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_token(
    body: ApiTokenCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Token name cannot be blank",
        )
    secret, prefix, token_hash = mint_api_token()
    try:
        row = await ApiTokenRepo.create(
            db,
            org_id,
            name=name,
            token_prefix=prefix,
            token_hash=token_hash,
            role=body.role,
            created_by=user.id,
        )
        await _audit_token_change(
            db,
            org_id,
            tool_name="api_token_create",
            actor=actor_label(user),
            token_name=row.name,
            token_role=row.role,
            token_id=row.id,
            extra={"token_prefix": row.token_prefix},
        )
        await db.commit()
        await db.refresh(row)
        return _response(row, secret=secret)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="API token name already exists",
        ) from exc


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    row = await ApiTokenRepo.get_by_id(db, org_id, token_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API token not found",
        )
    if row.revoked_at is None:
        await ApiTokenRepo.revoke(db, row)
        await _audit_token_change(
            db,
            org_id,
            tool_name="api_token_revoke",
            actor=actor_label(user),
            token_name=row.name,
            token_role=row.role,
            token_id=row.id,
        )
    await db.commit()
    return None
