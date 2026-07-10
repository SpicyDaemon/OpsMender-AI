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

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.session_runner import stop_incident_sessions
from backend.api.schemas import (
    IngestLearnPreview,
    IngestProviderListResponse,
    IngestProviderResponse,
    IngestResponse,
    IngestTokenCreate,
    IngestTokenCreatedResponse,
    IngestTokenLearnShapeRequest,
    IngestTokenLearnShapeResponse,
    IngestTokenListResponse,
    IngestTokenResponse,
)
from backend.db.models import IngestToken, User
from backend.db.repos import IngestTokenRepo, ServiceRepo
from backend.ingest.llm_extractor import (
    apply_shape_cache,
    compute_shape_hash,
    parse_with_paths,
)
from backend.ingest.rate_limiter import IngestRateLimiter
from backend.ingest.registry import list_providers
from backend.ingest.service import (
    authenticate_token,
    generate_token,
    hash_token,
    ingest_incident,
)
from backend.services.incident_events import dispatch_incident_created

router = APIRouter(tags=["ingest"])
webhook_router = APIRouter(tags=["ingest"])


async def _dispatch_created(app, *, org_id: uuid.UUID, result) -> None:
    if result.incident_id is None or result.dedup_action != "created":
        return
    try:
        await dispatch_incident_created(
            app,
            org_id=org_id,
            incident_id=result.incident_id,
            auto_start_tier=result.auto_start_tier,
        )
    except Exception:  # noqa: BLE001 - intake is durable before bus publication
        import logging

        logging.getLogger(__name__).exception(
            "incident.created publication failed incident=%s",
            result.incident_id,
        )


async def _stop_sessions_on_resolve(
    app, db: AsyncSession, *, org_id: uuid.UUID, result
) -> None:
    """Stop in-progress AI sessions when a clearing alert resolved an incident.

    Mirrors the operator resolve path — a resolved incident has nothing left
    for the agent to work. Commits the session-stop separately from the ingest
    transaction (which is already committed by the caller)."""
    if not getattr(result, "resolved_existing", False) or result.incident_id is None:
        return
    stopped = await stop_incident_sessions(
        app,
        db,
        org_id,
        result.incident_id,
        reason="Incident resolved by clearing alert",
    )
    if stopped:
        await db.commit()


def _to_token_response(tok: IngestToken) -> IngestTokenResponse:
    """Build a token response including the shape-cache size."""
    cache = tok.shape_cache or {}
    return IngestTokenResponse(
        id=tok.id,
        name=tok.name,
        provider=tok.provider,
        is_active=tok.is_active,
        created_at=tok.created_at,
        last_used_at=tok.last_used_at,
        shape_cache_size=len(cache) if isinstance(cache, dict) else 0,
    )


# ---------------------------------------------------------------------------
# Webhook endpoint — token-authed, NOT JWT
# ---------------------------------------------------------------------------


@webhook_router.post(
    "/api/v1/intake/{service_token}",
    response_model=IngestResponse,
    summary="Ingest an incident through a service intake endpoint",
    status_code=status.HTTP_200_OK,
)
async def service_intake_webhook(
    service_token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept a JSON alert through the service-owned intake URL.

    The unguessable token is embedded in the URL so external monitors can
    POST directly without managing an extra auth header.
    """

    service = await ServiceRepo.get_by_intake_token(db, service_token)
    if service is None or not service.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service intake endpoint not found",
        )

    token = await IngestTokenRepo.get_active_for_service(db, service.org_id, service.id)
    if token is None:
        token = await IngestTokenRepo.create(
            db,
            service.org_id,
            name=f"service:{service.id}",
            provider="auto",
            token_hash=hash_token(service_token),
            service_id=service.id,
        )
        await db.flush()

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

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must be valid JSON",
        )

    result = await ingest_incident(
        db,
        token=token,
        payload=payload,
        config=request.app.state.config,
    )

    if not result.success:
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

    # Persist the incident before background AI provisioning reads it. Intake
    # does not wait for session creation or workflow/model execution.
    await db.commit()
    await _stop_sessions_on_resolve(
        request.app, db, org_id=service.org_id, result=result
    )
    await _dispatch_created(request.app, org_id=service.org_id, result=result)
    return IngestResponse(
        success=result.success,
        incident_id=result.incident_id,
        dedup_action=result.dedup_action,
        error=result.error,
    )


@webhook_router.post(
    "/incidents/ingest",
    response_model=IngestResponse,
    summary="Ingest an incident from an external source",
    status_code=status.HTTP_200_OK,
)
async def ingest_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None),
    x_opsmender_token: str | None = Header(None, alias="X-OpsMender-Token"),
):
    """Accept a JSON payload from an external alerting system.

    Authentication via either:
    - ``Authorization: Bearer <ingest-token>``
    - ``X-OpsMender-Token: <ingest-token>``
    """
    # Extract raw token
    raw_token: str | None = None
    if x_opsmender_token:
        raw_token = x_opsmender_token
    elif authorization and authorization.startswith("Bearer "):
        raw_token = authorization[7:]

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing ingest token — provide X-OpsMender-Token header or Authorization: Bearer <token>",
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

    await db.commit()
    await _stop_sessions_on_resolve(request.app, db, org_id=token.org_id, result=result)
    await _dispatch_created(request.app, org_id=token.org_id, result=result)
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
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    tokens = await IngestTokenRepo.list_all(db, org_id)
    return IngestTokenListResponse(
        items=[_to_token_response(t) for t in tokens],
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    # Check name uniqueness
    existing = await IngestTokenRepo.get_by_name(db, org_id, body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ingest token named '{body.name}' already exists",
        )

    raw = generate_token()
    shape_cache: dict[str, dict[str, str]] | None = None

    # Pre-train the token on a sample payload when provided — so the
    # first real webhook of this shape doesn't pay the LLM tax.
    if body.sample_payload and body.provider == "auto":
        from backend.ingest.adapters.universal import UniversalAdapter
        from backend.ingest.llm_extractor import extract_paths_via_llm

        parsed = UniversalAdapter().parse(body.sample_payload)
        paths = parsed.extracted_paths or {}
        if parsed.needs_llm:
            llm_paths = await extract_paths_via_llm(
                db,
                org_id,
                payload=body.sample_payload,
                config=request.app.state.config,
            )
            if llm_paths:
                paths = llm_paths
        if paths:
            shape = compute_shape_hash(body.sample_payload)
            shape_cache = {shape: paths}

    if body.service_id is not None:
        from backend.db.repos import ServiceRepo

        if await ServiceRepo.get_by_id(db, org_id, body.service_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Service not found",
            )

    token_obj = await IngestTokenRepo.create(
        db,
        org_id,
        name=body.name,
        provider=body.provider,
        token_hash=hash_token(raw),
        shape_cache=shape_cache,
        service_id=body.service_id,
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
    "/ingest-tokens/{token_id}/learn-shape",
    response_model=IngestTokenLearnShapeResponse,
    summary="Train a token on a sample payload (auto-provider only)",
)
async def learn_ingest_token_shape(
    token_id: uuid.UUID,
    body: IngestTokenLearnShapeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    """Run the heuristic+LLM extractor on a sample payload and save the paths.

    Returns the resolved paths and a preview of the incident that would
    be created. Safe to call repeatedly — idempotent per payload shape.
    """
    tok = await IngestTokenRepo.get_by_id(db, org_id, token_id)
    if tok is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingest token not found",
        )
    if tok.provider != "auto":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shape learning only applies to tokens with provider='auto'",
        )

    paths, cache_hit = await apply_shape_cache(
        db,
        org_id,
        token=tok,
        payload=body.payload,
        config=request.app.state.config,
    )
    # apply_shape_cache persists paths on LLM success; it does not persist when the
    # payload matched heuristics directly, so we also save the adapter-derived paths.
    if not paths:
        from backend.ingest.adapters.universal import UniversalAdapter

        adapter_parsed = UniversalAdapter().parse(body.payload)
        paths = adapter_parsed.extracted_paths or {}
        if paths:
            shape = compute_shape_hash(body.payload)
            next_cache = dict(tok.shape_cache or {})
            next_cache[shape] = paths
            await IngestTokenRepo.update_shape_cache(db, org_id, tok.id, next_cache)
            tok.shape_cache = next_cache

    # Build a preview of what the incident would look like
    parsed_preview = parse_with_paths(body.payload, paths)
    return IngestTokenLearnShapeResponse(
        shape_hash=compute_shape_hash(body.payload),
        paths=paths or {},
        cache_hit=cache_hit,
        preview=IngestLearnPreview(
            title=parsed_preview.title,
            description=parsed_preview.description,
            severity=parsed_preview.severity,
            external_id=parsed_preview.external_id,
            status=parsed_preview.status,
        ),
    )


@router.post(
    "/ingest-tokens/{token_id}/revoke",
    response_model=IngestTokenResponse,
    summary="Revoke (deactivate) an ingest token",
)
async def revoke_ingest_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    tok = await IngestTokenRepo.get_by_id(db, org_id, token_id)
    if tok is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingest token not found",
        )

    await IngestTokenRepo.revoke(db, org_id, token_id)
    # Re-fetch after update
    tok = await IngestTokenRepo.get_by_id(db, org_id, token_id)
    return _to_token_response(tok)


@router.delete(
    "/ingest-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an ingest token permanently",
)
async def delete_ingest_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await IngestTokenRepo.delete(db, org_id, token_id)
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
