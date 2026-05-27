"""Config endpoints.

GET  /config — read current system configuration
PUT  /config — update config (admin only)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    ConfigResponse,
    ConfigUpdate,
    ModelConfigResponse,
    ModelConfigSaveResponse,
    ModelConfigValidationIssue,
    ModelConfigUpdate,
    SetupChecklistResponse,
)
from backend.config_loader import Config, set_env_path
from backend.db.models import User
from backend.db.repos import (
    IngestTokenRepo,
    MCPServerRepo,
    ModelConfigRepo,
    OrgSAMLConfigRepo,
    OrgSSOConfigRepo,
    RuntimeConfigRepo,
    ServiceRepo,
    SkillRepo,
)
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
    sso_configured: bool = False,
    saml_configured: bool = False,
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
        multi_org_enabled=cfg.people.multi_org_enabled,
        smtp_configured=cfg.smtp.configured,
        advanced_auth_enabled=cfg.people.advanced_auth_enabled,
        sso_configured=sso_configured,
        saml_configured=saml_configured,
    )


async def _read_runtime_config(
    db: AsyncSession, org_id: uuid.UUID
) -> dict[str, str]:
    return await RuntimeConfigRepo.get_many(
        db,
        org_id,
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
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    try:
        cfg = Config.load()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    overrides = await _read_runtime_config(db, org_id)
    tier = int(overrides.get("tier", cfg.tiers.get("default", 2)))
    logging_level = overrides.get("logging_level", cfg.logging.get("level", "INFO"))
    ingest_auto_start_enabled = overrides.get(
        "ingest_auto_start_enabled", str(cfg.ingest.auto_start_enabled)
    ).strip().lower() in {"1", "true", "yes", "on"}
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
    # Sprint 64: per-tenant lookup for the SSO/SAML "configured" booleans
    # so the frontend can keep settings visible for orgs that already
    # have a provider wired up, even when ``advanced_auth_enabled`` is
    # off. Existing providers keep working regardless of the flag.
    sso_row = await OrgSSOConfigRepo.get_for_org(db, org_id)
    saml_row = await OrgSAMLConfigRepo.get_for_org(db, org_id)
    return _config_to_response(
        cfg,
        tier=tier,
        logging_level=logging_level,
        ingest_auto_start_enabled=ingest_auto_start_enabled,
        ingest_auto_start_min_severity=ingest_auto_start_min_severity,
        ingest_auto_start_source=ingest_auto_start_source,
        sso_configured=sso_row is not None,
        saml_configured=saml_row is not None,
    )


@router.get(
    "/setup-checklist",
    response_model=SetupChecklistResponse,
    summary="First-run setup checklist state (Sprint 43 P0 #1)",
)
async def get_setup_checklist(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator", "viewer")),
):
    """Return whether the org has completed each first-run setup step.

    The frontend renders a checklist on the incidents page when
    ``all_complete`` is false, deep-linking each unchecked row into
    the relevant Config section.
    """

    models = await ModelConfigRepo.list_all(db, org_id)
    mcp_servers = await MCPServerRepo.list_all(db, org_id)
    skills = await SkillRepo.list_all(db, org_id)
    ingest_tokens = await IngestTokenRepo.list_all(db, org_id)
    services = await ServiceRepo.list_all(db, org_id)

    model_configured = len(models) > 0
    mcp_server_added = len(mcp_servers) > 0
    skill_defined = len(skills) > 0
    ingest_token_created = len(ingest_tokens) > 0
    paging_service_added = len(services) > 0

    all_complete = all(
        [
            model_configured,
            mcp_server_added,
            skill_defined,
            ingest_token_created,
            paging_service_added,
        ]
    )

    return SetupChecklistResponse(
        model_configured=model_configured,
        mcp_server_added=mcp_server_added,
        skill_defined=skill_defined,
        ingest_token_created=ingest_token_created,
        paging_service_added=paging_service_added,
        all_complete=all_complete,
    )


@router.put(
    "",
    response_model=ConfigResponse,
    summary="Update system config (admin only)",
)
async def update_config(
    body: ConfigUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if body.tier is not None:
        await RuntimeConfigRepo.set(db, org_id, key="tier", value=str(body.tier))
    if body.logging_level is not None:
        await RuntimeConfigRepo.set(
            db,
            org_id,
            key="logging_level",
            value=body.logging_level,
        )
    if body.ingest_auto_start_enabled is not None:
        await RuntimeConfigRepo.set(
            db,
            org_id,
            key="ingest_auto_start_enabled",
            value="true" if body.ingest_auto_start_enabled else "false",
        )
    if body.ingest_auto_start_min_severity is not None:
        await RuntimeConfigRepo.set(
            db,
            org_id,
            key="ingest_auto_start_min_severity",
            value=body.ingest_auto_start_min_severity,
        )
    if body.ingest_auto_start_source is not None:
        await RuntimeConfigRepo.set(
            db,
            org_id,
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
    overrides = await _read_runtime_config(db, org_id)
    tier = int(overrides.get("tier", cfg.tiers.get("default", 2)))
    logging_level = overrides.get("logging_level", cfg.logging.get("level", "INFO"))
    ingest_auto_start_enabled = overrides.get(
        "ingest_auto_start_enabled", str(cfg.ingest.auto_start_enabled)
    ).strip().lower() in {"1", "true", "yes", "on"}
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
    # Sprint 64: per-tenant lookup for the SSO/SAML "configured" booleans
    # so the frontend can keep settings visible for orgs that already
    # have a provider wired up, even when ``advanced_auth_enabled`` is
    # off. Existing providers keep working regardless of the flag.
    sso_row = await OrgSSOConfigRepo.get_for_org(db, org_id)
    saml_row = await OrgSAMLConfigRepo.get_for_org(db, org_id)
    return _config_to_response(
        cfg,
        tier=tier,
        logging_level=logging_level,
        ingest_auto_start_enabled=ingest_auto_start_enabled,
        ingest_auto_start_min_severity=ingest_auto_start_min_severity,
        ingest_auto_start_source=ingest_auto_start_source,
        sso_configured=sso_row is not None,
        saml_configured=saml_row is not None,
    )


@router.put(
    "/model",
    response_model=ModelConfigSaveResponse,
    summary="Set default model configuration",
)
async def update_model_config(
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
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    name = body.name or f"{body.provider}:{body.model_id}"
    cfg = await ModelConfigRepo.upsert(
        db,
        org_id,
        name=name,
        provider=body.provider,
        model_id=body.model_id,
        api_key_env_var=body.api_key_env_var,
        base_url=body.base_url,
        api_version=body.api_version,
        provider_meta=body.provider_meta,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
    )
    await ModelConfigRepo.set_default(db, org_id, cfg.id)
    refreshed = await ModelConfigRepo.get_by_id(db, org_id, cfg.id)
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
