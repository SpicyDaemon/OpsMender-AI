"""Resolve the effective AI autonomy tier for a new session."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from yaml import YAMLError

from backend.config_loader import AppConfig
from backend.db.models import Incident, Service
from backend.db.repos import MCPServerRepo, RuntimeConfigRepo, ServiceRepo, SkillRepo
from backend.skills.parser import loads
from backend.tiers.enforcement import normalize_tier


def resolve_session_tier(
    *,
    requested_tier: int | None = None,
    service_tier: int | None = None,
    skill_default_tier: int | None = None,
    org_default_tier: int | None = None,
) -> int:
    """Apply the session-tier precedence contract and fail safe to Tier 2."""
    for candidate in (
        requested_tier,
        service_tier,
        skill_default_tier,
        org_default_tier,
    ):
        if candidate is not None:
            return normalize_tier(int(candidate))
    return 2


def _service_mcp_server_id(service: Service | None) -> uuid.UUID | None:
    if service is None:
        return None
    for raw in service.mcp_server_ids or []:
        try:
            return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
        except (TypeError, ValueError, YAMLError):
            continue
    return None


async def resolve_session_tier_for_incident(
    db: AsyncSession,
    org_id: uuid.UUID,
    config: AppConfig | None,
    *,
    incident: Incident | None = None,
    requested_tier: int | None = None,
) -> int:
    """Resolve a tier using incident service, effective skill, and org policy."""
    service: Service | None = None
    if incident is not None and incident.service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, incident.service_id)

    server_id = _service_mcp_server_id(service)
    if server_id is None:
        servers = await MCPServerRepo.list_all(db, org_id, active_only=True)
        server_id = servers[0].id if servers else None
    skill = await SkillRepo.get_for_mcp_server(
        db,
        org_id,
        server_id,
    )
    skill_default_tier = None
    if skill is not None:
        try:
            skill_default_tier = loads(skill.content_md).default_tier
        except (TypeError, ValueError):
            # A malformed structured policy must never select a more autonomous
            # tier through fallback configuration.
            skill_default_tier = 2

    overrides = await RuntimeConfigRepo.get_many(db, org_id, ["tier"])
    configured_default = 2 if config is None else config.tiers.get("default", 2)
    org_default_tier = int(overrides.get("tier", configured_default))

    return resolve_session_tier(
        requested_tier=requested_tier,
        service_tier=None if service is None else service.ai_default_tier,
        skill_default_tier=skill_default_tier,
        org_default_tier=org_default_tier,
    )
