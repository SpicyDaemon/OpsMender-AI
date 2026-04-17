"""Ingest endpoints — external incident ingestion (Sprint 14).

POST /incidents/ingest       — webhook endpoint (token-authed, not JWT)
GET  /ingest-tokens          — list all ingest tokens (admin)
POST /ingest-tokens          — create a new token (admin, returns raw token once)
POST /ingest-tokens/{id}/revoke — revoke / deactivate a token (admin)
DELETE /ingest-tokens/{id}   — hard-delete a token (admin)
GET  /ingest-providers       — list available provider adapters
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    IngestProviderListResponse,
    IngestProviderResponse,
    IngestResponse,
    IngestTokenCreate,
    IngestTokenCreatedResponse,
    IngestTokenListResponse,
    IngestTokenResponse,
)
from backend.db.models import User
from backend.db.repos import IngestTokenRepo
from backend.ingest.rate_limiter import IngestRateLimiter
from backend.ingest.registry import list_providers
from backend.ingest.service import (
    authenticate_token,
    generate_token,
    hash_token,
    ingest_incident,
)

router = APIRouter(tags=["ingest"])


# ---------------------------------------------------------------------------
# Webhook endpoint — token-authed, NOT JWT
# ---------------------------------------------------------------------------

@router.post(
    "/incidents/ingest",
    response_model=IngestResponse,
    summary="Ingest an incident from an external source",
    status_code=status.HTTP_200_OK,
)
async def ingest_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None),
    x_aim_token: str | None = Header(None, alias="X-AIM-Token"),
):
    """Accept a JSON payload from an external alerting system.

    Authentication via either:
    - ``Authorization: Bearer <ingest-token>``
    - ``X-AIM-Token: <ingest-token>``
    """
    # Extract raw token
    raw_token: str | None = None
    if x_aim_token:
        raw_token = x_aim_token
    elif authorization and authorization.startswith("Bearer "):
        raw_token = authorization[7:]

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing ingest token — provide X-AIM-Token header or Authorization: Bearer <token>",
        )

    # Validate token
    token = await authenticate_token(db, raw_token)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked ingest token",
        )

    # ── Rate limiting ──────────────────────────────────────────────────
    limiter: IngestRateLimiter = request.app.state.ingest_limiter
    rl = await limiter.check(token.id)

    if not rl.allowed:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded",
                "retry_after": round(rl.retry_after or 0, 1),
            },
            headers={
                "Retry-After": str(int(rl.retry_after or 1)),
                "X-RateLimit-Limit": str(rl.limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    # Parse body
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid JSON",
        )

    # Run ingest
    result = await ingest_incident(
        db,
        token=token,
        payload=payload,
        config=request.app.state.config,
    )

    if not result.success:
        # Commit the session so the audit log entry is preserved,
        # then return the error as a normal response.
        await db.commit()
        return JSONResponse(
            status_code=422,
            content=IngestResponse(
                success=False,
                incident_id=None,
                dedup_action=None,
                error=result.error or "Failed to parse payload",
            ).model_dump(mode="json"),
        )

    return IngestResponse(
        success=result.success,
        incident_id=result.incident_id,
        dedup_action=result.dedup_action,
        error=result.error,
    )


# ---------------------------------------------------------------------------
# Ingest token management — JWT-authed, admin only
# ---------------------------------------------------------------------------

@router.get(
    "/ingest-tokens",
    response_model=IngestTokenListResponse,
    summary="List all ingest tokens",
)
async def list_ingest_tokens(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    tokens = await IngestTokenRepo.list_all(db)
    return IngestTokenListResponse(
        items=[IngestTokenResponse.model_validate(t) for t in tokens],
        total=len(tokens),
    )


@router.post(
    "/ingest-tokens",
    response_model=IngestTokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new ingest token",
)
async def create_ingest_token(
    body: IngestTokenCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    # Check name uniqueness
    existing = await IngestTokenRepo.get_by_name(db, body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ingest token named '{body.name}' already exists",
        )

    raw = generate_token()
    token_obj = await IngestTokenRepo.create(
        db,
        name=body.name,
        provider=body.provider,
        token_hash=hash_token(raw),
    )

    return IngestTokenCreatedResponse(
        id=token_obj.id,
        name=token_obj.name,
        provider=token_obj.provider,
        token=raw,
        is_active=token_obj.is_active,
        created_at=token_obj.created_at,
    )


@router.post(
    "/ingest-tokens/{token_id}/revoke",
    response_model=IngestTokenResponse,
    summary="Revoke (deactivate) an ingest token",
)
async def revoke_ingest_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    tok = await IngestTokenRepo.get_by_id(db, token_id)
    if tok is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingest token not found",
        )

    await IngestTokenRepo.revoke(db, token_id)
    # Re-fetch after update
    tok = await IngestTokenRepo.get_by_id(db, token_id)
    return IngestTokenResponse.model_validate(tok)


@router.delete(
    "/ingest-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an ingest token permanently",
)
async def delete_ingest_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    deleted = await IngestTokenRepo.delete(db, token_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingest token not found",
        )


# ---------------------------------------------------------------------------
# Provider adapter listing
# ---------------------------------------------------------------------------

@router.get(
    "/ingest-providers",
    response_model=IngestProviderListResponse,
    summary="List available ingest provider adapters",
)
async def list_ingest_providers(
    user: User = Depends(get_current_user),
):
    providers = list_providers()
    return IngestProviderListResponse(
        items=[IngestProviderResponse(**p) for p in providers],
    )
