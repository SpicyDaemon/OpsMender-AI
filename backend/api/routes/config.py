"""Config endpoints.

GET  /config — read current system configuration
PUT  /config — update config (admin only)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    ConfigResponse,
    ConfigUpdate,
    ModelConfigResponse,
    ModelConfigSaveResponse,
    ModelConfigValidationIssue,
    ModelConfigUpdate,
)
from backend.config_loader import Config, set_env_path
from backend.db.models import User
from backend.db.repos import ModelConfigRepo, RuntimeConfigRepo
from backend.llm import ProviderRegistry

router = APIRouter(prefix="/config", tags=["config"])


def set_config_path(path) -> None:
    """Backward-compatible test helper: point config loading at a .env file."""
    set_env_path(path)


def _config_to_response(
    cfg: Config,
    *,
    tier: int,
    logging_level: str,
    ingest_auto_start_enabled: bool,
    ingest_auto_start_min_severity: str,
    ingest_auto_start_source: str | None,
) -> ConfigResponse:
    servers = []
    for server in cfg.mcp_servers:
        entry: dict = {"name": server.name, "transport": server.transport}
        if server.url:
            entry["url"] = server.url
        if server.command:
            entry["command"] = server.command
        if server.args:
            entry["args"] = server.args
        servers.append(entry)

    return ConfigResponse(
        tier=tier,
        mcp_servers=servers,
        audit_output=cfg.audit.output,
        logging_level=logging_level,
        ingest_auto_start_enabled=ingest_auto_start_enabled,
        ingest_auto_start_min_severity=ingest_auto_start_min_severity,
        ingest_auto_start_source=ingest_auto_start_source,
    )


async def _read_runtime_config(db: AsyncSession) -> dict[str, str]:
    return await RuntimeConfigRepo.get_many(
        db,
        [
            "tier",
            "logging_level",
            "ingest_auto_start_enabled",
            "ingest_auto_start_min_severity",
            "ingest_auto_start_source",
        ],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=ConfigResponse,
    summary="Get current system config",
)
async def get_config(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    try:
        cfg = Config.load()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    overrides = await _read_runtime_config(db)
    tier = int(overrides.get("tier", cfg.tiers.get("default", 2)))
    logging_level = overrides.get("logging_level", cfg.logging.get("level", "INFO"))
    ingest_auto_start_enabled = (
        overrides.get("ingest_auto_start_enabled", str(cfg.ingest.auto_start_enabled))
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    ingest_auto_start_min_severity = overrides.get(
        "ingest_auto_start_min_severity",
        cfg.ingest.auto_start_min_severity,
    )
    ingest_auto_start_source = (
        overrides.get("ingest_auto_start_source", cfg.ingest.auto_start_source or "")
        .strip()
        .lower()
        or None
    )
    return _config_to_response(
        cfg,
        tier=tier,
        logging_level=logging_level,
        ingest_auto_start_enabled=ingest_auto_start_enabled,
        ingest_auto_start_min_severity=ingest_auto_start_min_severity,
        ingest_auto_start_source=ingest_auto_start_source,
    )


@router.put(
    "",
    response_model=ConfigResponse,
    summary="Update system config (admin only)",
)
async def update_config(
    body: ConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    if body.tier is not None:
        await RuntimeConfigRepo.set(db, key="tier", value=str(body.tier))
    if body.logging_level is not None:
        await RuntimeConfigRepo.set(
            db,
            key="logging_level",
            value=body.logging_level,
        )
    if body.ingest_auto_start_enabled is not None:
        await RuntimeConfigRepo.set(
            db,
            key="ingest_auto_start_enabled",
            value="true" if body.ingest_auto_start_enabled else "false",
        )
    if body.ingest_auto_start_min_severity is not None:
        await RuntimeConfigRepo.set(
            db,
            key="ingest_auto_start_min_severity",
            value=body.ingest_auto_start_min_severity,
        )
    if body.ingest_auto_start_source is not None:
        await RuntimeConfigRepo.set(
            db,
            key="ingest_auto_start_source",
            value=body.ingest_auto_start_source.strip().lower(),
        )
    await db.commit()

    try:
        cfg = Config.load()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    overrides = await _read_runtime_config(db)
    tier = int(overrides.get("tier", cfg.tiers.get("default", 2)))
    logging_level = overrides.get("logging_level", cfg.logging.get("level", "INFO"))
    ingest_auto_start_enabled = (
        overrides.get("ingest_auto_start_enabled", str(cfg.ingest.auto_start_enabled))
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    ingest_auto_start_min_severity = overrides.get(
        "ingest_auto_start_min_severity",
        cfg.ingest.auto_start_min_severity,
    )
    ingest_auto_start_source = (
        overrides.get("ingest_auto_start_source", cfg.ingest.auto_start_source or "")
        .strip()
        .lower()
        or None
    )
    return _config_to_response(
        cfg,
        tier=tier,
        logging_level=logging_level,
        ingest_auto_start_enabled=ingest_auto_start_enabled,
        ingest_auto_start_min_severity=ingest_auto_start_min_severity,
        ingest_auto_start_source=ingest_auto_start_source,
    )


@router.put(
    "/model",
    response_model=ModelConfigSaveResponse,
    summary="Set default model configuration",
)
async def update_model_config(
    body: ModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
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
            allow_unverified=True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    name = body.name or f"{body.provider}:{body.model_id}"
    cfg = await ModelConfigRepo.upsert(
        db,
        name=name,
        provider=body.provider,
        model_id=body.model_id,
        api_key_env_var=body.api_key_env_var,
        base_url=body.base_url,
        api_version=body.api_version,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
    )
    await ModelConfigRepo.set_default(db, cfg.id)
    refreshed = await ModelConfigRepo.get_by_id(db, cfg.id)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Model config could not be reloaded",
        )
    return ModelConfigSaveResponse(
        config=ModelConfigResponse.model_validate(refreshed),
        warnings=[
            ModelConfigValidationIssue(code=warning.code, message=warning.message)
            for warning in validation.warnings
        ],
    )
