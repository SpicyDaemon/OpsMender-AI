"""Provider discovery endpoints."""

from __future__ import annotations

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
    ModelConfigValidationIssue,
    ModelConfigUpdate,
    ProviderModelsListResponse,
)
from backend.db.models import User
from backend.db.repos import ModelConfigRepo
from backend.llm import ProviderRegistry

router = APIRouter(prefix="/models", tags=["models"])


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
    deleted = await ModelConfigRepo.delete(db, org_id, config_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
