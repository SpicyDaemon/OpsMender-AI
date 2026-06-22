"""Provider discovery endpoints."""

from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    ModelBootstrapStatusResponse,
    ModelConfigListResponse,
    ModelConfigResponse,
    ModelConfigSaveResponse,
    ModelConfigTestResponse,
    ModelConfigValidationIssue,
    ModelConfigUpdate,
    ProviderModelsListResponse,
)
from backend.db.models import User
from backend.db.repos import ModelConfigRepo, SessionRepo
from backend.llm import ProviderRegistry
from backend.llm.factory import create_provider

router = APIRouter(prefix="/models", tags=["models"])

# Hard ceiling for a live model connection test so a hung provider can't pin a
# request open. Local models can be slow on first token, so allow some room.
_MODEL_TEST_TIMEOUT_SECONDS = 20.0


def _save_response(config, warnings) -> ModelConfigSaveResponse:
    return ModelConfigSaveResponse(
        config=ModelConfigResponse.model_validate(config),
        warnings=[
            ModelConfigValidationIssue(code=warning.code, message=warning.message)
            for warning in warnings
        ],
    )


@router.get(
    "",
    response_model=ProviderModelsListResponse,
    summary="List available provider models",
)
async def list_models(
    provider: str | None = None,
    model_id: str | None = None,
    api_key_env_var: str | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    region: str | None = None,
    profile: str | None = None,
    project: str | None = None,
    location: str | None = None,
    user: User = Depends(get_current_user),
):
    registry = ProviderRegistry()
    provider_meta = {
        key: value
        for key, value in {
            "region": region,
            "profile": profile,
            "project": project,
            "location": location,
        }.items()
        if value
    }

    items = registry.discover_models(
        provider=provider,
        model_id=model_id,
        api_key_env_var=api_key_env_var,
        base_url=base_url,
        api_version=api_version,
        provider_meta=provider_meta or None,
    )
    return ProviderModelsListResponse(items=items, total=len(items))


@router.get(
    "/configs",
    response_model=ModelConfigListResponse,
    summary="List saved model configs",
)
async def list_model_configs(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await ModelConfigRepo.list_all(db, org_id)
    return ModelConfigListResponse(items=list(items), total=len(items))


@router.get(
    "/bootstrap",
    response_model=ModelBootstrapStatusResponse,
    summary="Get first-run model bootstrap status",
)
async def get_model_bootstrap_status(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = list(await ModelConfigRepo.list_all(db, org_id))
    default_cfg = next((item for item in items if item.is_default), None)
    return ModelBootstrapStatusResponse(
        needs_setup=default_cfg is None,
        has_configs=bool(items),
        has_default=default_cfg is not None,
        default_config=(
            None
            if default_cfg is None
            else ModelConfigResponse.model_validate(default_cfg)
        ),
    )


@router.post(
    "/configs",
    response_model=ModelConfigSaveResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a saved model config",
)
async def create_model_config(
    body: ModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    registry = ProviderRegistry()
    try:
        validation = registry.validate_model_config(
            provider=body.provider,
            model_id=body.model_id,
            api_key_env_var=body.api_key_env_var,
            base_url=body.base_url,
            api_version=body.api_version,
            provider_meta=body.provider_meta,
            allow_unverified=True,
        )
        cfg = await ModelConfigRepo.create(
            db,
            org_id,
            name=body.name or f"{body.provider}:{body.model_id}",
            provider=body.provider,
            model_id=body.model_id,
            api_key_env_var=body.api_key_env_var,
            base_url=body.base_url,
            api_version=body.api_version,
            provider_meta=body.provider_meta,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
            max_concurrent_sessions=body.max_concurrent_sessions,
        )
        await db.commit()
        await db.refresh(cfg)
        return _save_response(cfg, validation.warnings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Model config name already exists",
        ) from exc


@router.put(
    "/configs/{config_id}",
    response_model=ModelConfigSaveResponse,
    summary="Update a saved model config",
)
async def update_model_config(
    config_id: uuid.UUID,
    body: ModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await ModelConfigRepo.get_by_id(db, org_id, config_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )
    occupancy = await SessionRepo.active_occupancy_for_model_config(
        db, org_id, config_id
    )
    material_change = any(
        (
            body.provider != existing.provider,
            body.model_id != existing.model_id,
            body.api_key_env_var != existing.api_key_env_var,
            body.base_url != existing.base_url,
            body.api_version != existing.api_version,
            body.provider_meta != existing.provider_meta,
            body.max_tokens != existing.max_tokens,
            body.temperature != existing.temperature,
        )
    )
    if occupancy and material_change:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot change an occupied model config's provider or runtime "
                "settings. Wait for its active sessions to finish."
            ),
        )

    registry = ProviderRegistry()
    try:
        validation = registry.validate_model_config(
            provider=body.provider,
            model_id=body.model_id,
            api_key_env_var=body.api_key_env_var,
            base_url=body.base_url,
            api_version=body.api_version,
            provider_meta=body.provider_meta,
            allow_unverified=True,
        )
        updated = await ModelConfigRepo.update(
            db,
            org_id,
            config_id,
            name=body.name or existing.name,
            provider=body.provider,
            model_id=body.model_id,
            api_key_env_var=body.api_key_env_var,
            base_url=body.base_url,
            api_version=body.api_version,
            provider_meta=body.provider_meta,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
            max_concurrent_sessions=body.max_concurrent_sessions,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model config not found",
            )
        await db.refresh(updated)
        return _save_response(updated, validation.warnings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Model config name already exists",
        ) from exc


@router.delete(
    "/configs/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved model config",
)
async def delete_model_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    occupancy = await SessionRepo.active_occupancy_for_model_config(
        db, org_id, config_id
    )
    if occupancy:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a model config while it has active sessions.",
        )
    deleted = await ModelConfigRepo.delete(db, org_id, config_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/configs/{config_id}/test",
    response_model=ModelConfigTestResponse,
    summary="Live-test a saved model config's connection (admin/operator)",
)
async def test_model_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    """Actually exercise a saved model config: build the provider from its
    stored settings and send a tiny prompt. Surfaces real failures (bad
    base_url, missing API key, wrong model id, unreachable endpoint) that
    config validation can only warn about. Read-only — never mutates the config.
    """
    cfg = await ModelConfigRepo.get_by_id(db, org_id, config_id)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model config not found"
        )

    def _probe() -> str:
        provider = create_provider(
            provider=cfg.provider,
            model_id=cfg.model_id,
            max_tokens=16,
            api_key_env_var=cfg.api_key_env_var,
            base_url=cfg.base_url,
            api_version=cfg.api_version,
            provider_meta=cfg.provider_meta,
        )
        # A minimal real completion verifies auth + endpoint + model id end to end.
        return provider.complete("ping")

    start = time.monotonic()
    try:
        reply = await asyncio.wait_for(
            asyncio.to_thread(_probe), timeout=_MODEL_TEST_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return ModelConfigTestResponse(
            ok=False,
            error=(
                f"No response within {int(_MODEL_TEST_TIMEOUT_SECONDS)}s — the "
                "provider endpoint may be unreachable or overloaded."
            ),
        )
    except Exception as exc:  # noqa: BLE001 — surface any provider error to the operator
        return ModelConfigTestResponse(ok=False, error=str(exc))

    latency_ms = int((time.monotonic() - start) * 1000)
    head = (reply or "").strip().replace("\n", " ")
    detail = f"Model responded in {latency_ms}ms."
    if head:
        detail += f' Reply: "{head[:80]}".'
    return ModelConfigTestResponse(ok=True, latency_ms=latency_ms, detail=detail)


@router.post(
    "/configs/{config_id}/set-default",
    response_model=ModelConfigResponse,
    summary="Mark a saved model config as default",
)
async def set_default_model_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await ModelConfigRepo.get_by_id(db, org_id, config_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )
    await ModelConfigRepo.set_default(db, org_id, config_id)
    await db.commit()
    refreshed = await ModelConfigRepo.get_by_id(db, org_id, config_id)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )
    return refreshed


@router.post(
    "/configs/{config_id}/toggle-active",
    response_model=ModelConfigResponse,
    summary="Enable or disable a saved model config (admin only)",
)
async def toggle_model_config_active(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await ModelConfigRepo.get_by_id(db, org_id, config_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )
    if existing.is_active and await SessionRepo.active_occupancy_for_model_config(
        db, org_id, config_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot disable a model config while it has active sessions.",
        )
    # Safety: prevent disabling the only active config that is also the default.
    if existing.is_active and existing.is_default:
        # Check if any other active, non-default config exists.
        all_cfgs = await ModelConfigRepo.list_all(db, org_id)
        other_active = [c for c in all_cfgs if c.id != config_id and c.is_active]
        if not other_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot disable the only active model config. Enable another config first.",
            )
    existing.is_active = not existing.is_active
    await db.commit()
    await db.refresh(existing)
    return existing
