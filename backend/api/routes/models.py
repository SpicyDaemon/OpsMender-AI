"""Provider discovery endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    ModelConfigListResponse,
    ModelConfigResponse,
    ModelConfigUpdate,
    ProviderModelsListResponse,
)
from backend.db.models import User
from backend.db.repos import ModelConfigRepo
from backend.llm import ProviderRegistry

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ProviderModelsListResponse, summary="List available provider models")
async def list_models(
    provider: str | None = None,
    model_id: str | None = None,
    api_key_env_var: str | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    user: User = Depends(get_current_user),
):
    registry = ProviderRegistry()
    items = registry.discover_models(
        provider=provider,
        model_id=model_id,
        api_key_env_var=api_key_env_var,
        base_url=base_url,
        api_version=api_version,
    )
    return ProviderModelsListResponse(items=items, total=len(items))


@router.get(
    "/configs",
    response_model=ModelConfigListResponse,
    summary="List saved model configs",
)
async def list_model_configs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = await ModelConfigRepo.list_all(db)
    return ModelConfigListResponse(items=list(items), total=len(items))


@router.post(
    "/configs",
    response_model=ModelConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a saved model config",
)
async def create_model_config(
    body: ModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    registry = ProviderRegistry()
    try:
        registry.validate_model_config(
            provider=body.provider,
            model_id=body.model_id,
            api_key_env_var=body.api_key_env_var,
            base_url=body.base_url,
            api_version=body.api_version,
        )
        cfg = await ModelConfigRepo.create(
            db,
            name=body.name or f"{body.provider}:{body.model_id}",
            provider=body.provider,
            model_id=body.model_id,
            api_key_env_var=body.api_key_env_var,
            base_url=body.base_url,
            api_version=body.api_version,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
        await db.commit()
        await db.refresh(cfg)
        return cfg
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
    response_model=ModelConfigResponse,
    summary="Update a saved model config",
)
async def update_model_config(
    config_id: uuid.UUID,
    body: ModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    existing = await ModelConfigRepo.get_by_id(db, config_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )

    registry = ProviderRegistry()
    try:
        registry.validate_model_config(
            provider=body.provider,
            model_id=body.model_id,
            api_key_env_var=body.api_key_env_var,
            base_url=body.base_url,
            api_version=body.api_version,
        )
        updated = await ModelConfigRepo.update(
            db,
            config_id,
            name=body.name or existing.name,
            provider=body.provider,
            model_id=body.model_id,
            api_key_env_var=body.api_key_env_var,
            base_url=body.base_url,
            api_version=body.api_version,
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
        return updated
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
    user: User = Depends(require_role("admin")),
):
    deleted = await ModelConfigRepo.delete(db, config_id)
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
    user: User = Depends(require_role("admin")),
):
    existing = await ModelConfigRepo.get_by_id(db, config_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )
    await ModelConfigRepo.set_default(db, config_id)
    await db.commit()
    refreshed = await ModelConfigRepo.get_by_id(db, config_id)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model config not found",
        )
    return refreshed
