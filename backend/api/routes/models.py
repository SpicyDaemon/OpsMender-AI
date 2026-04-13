"""Provider discovery endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.auth import get_current_user
from backend.api.schemas import ProviderModelsListResponse
from backend.db.models import User
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
