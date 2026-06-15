"""Auto-start policy helpers for externally ingested incidents."""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config_loader import AppConfig
from backend.db.models import Incident
from backend.db.repos import RuntimeConfigRepo, ServiceRepo, SessionRepo
from backend.tiers.resolution import resolve_session_tier_for_incident

_SEVERITY_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

_ACTIVE_SESSION_STATUSES = {"active", "awaiting_approval"}


@dataclasses.dataclass(frozen=True)
class IngestAutoStartPolicy:
    enabled: bool
    min_severity: str
    source: str | None
    session_tier: int


def _to_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_severity(raw: str | None, default: str) -> str:
    value = (raw or default).strip().lower()
    return value if value in _SEVERITY_RANK else default


def _normalize_source(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    return value or None


async def load_auto_start_policy(
    db: AsyncSession,
    org_id: uuid.UUID,
    config: AppConfig,
    *,
    incident: Incident | None = None,
) -> IngestAutoStartPolicy:
    """Resolve the effective ingest auto-start policy from env + DB overrides."""
    overrides = await RuntimeConfigRepo.get_many(
        db,
        org_id,
        [
            "tier",
            "ingest_auto_start_enabled",
            "ingest_auto_start_min_severity",
            "ingest_auto_start_source",
        ],
    )

    enabled = _to_bool(
        overrides.get("ingest_auto_start_enabled"),
        config.ingest.auto_start_enabled,
    )
    if incident is not None and incident.service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, incident.service_id)
        if service is not None and service.ai_auto_start_enabled is not None:
            enabled = enabled and service.ai_auto_start_enabled

    return IngestAutoStartPolicy(
        enabled=enabled,
        min_severity=_normalize_severity(
            overrides.get("ingest_auto_start_min_severity"),
            config.ingest.auto_start_min_severity,
        ),
        source=_normalize_source(
            overrides.get("ingest_auto_start_source", config.ingest.auto_start_source)
        ),
        session_tier=await resolve_session_tier_for_incident(
            db,
            org_id,
            config,
            incident=incident,
        ),
    )


def should_auto_start_session(
    incident: Incident,
    *,
    dedup_action: str,
    policy: IngestAutoStartPolicy,
) -> bool:
    """Return True when an ingested incident should auto-create a session."""
    if not policy.enabled:
        return False
    if policy.session_tier != 0:
        return False
    if dedup_action != "created":
        return False
    if incident.status in {"resolved", "closed"}:
        return False

    severity = (incident.severity or "").strip().lower()
    if _SEVERITY_RANK.get(severity, 0) < _SEVERITY_RANK[policy.min_severity]:
        return False

    if policy.source is not None:
        source = (incident.external_source or "").strip().lower()
        if source != policy.source:
            return False

    return True


async def has_active_session_for_incident(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
) -> bool:
    """Return True when the incident already has a non-terminal session."""
    sessions = await SessionRepo.list_by_incident(db, org_id, incident_id)
    return any(session.status in _ACTIVE_SESSION_STATUSES for session in sessions)
