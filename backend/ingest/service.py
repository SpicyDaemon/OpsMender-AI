"""Ingest service — orchestrates token validation, adapter dispatch, dedup,
and audit logging for ``POST /incidents/ingest``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config_loader import AppConfig
from backend.db.models import Incident, IngestToken
from backend.db.repos import (
    IncidentRepo,
    IngestLogRepo,
    IngestTokenRepo,
    MaintenanceWindowRepo,
    SLATargetRepo,
    ServiceRepo,
    UptimeSampleRepo,
)
from backend.ingest.autostart import (
    auto_start_skip_reason,
    load_auto_start_policy,
)
from backend.ingest.llm_extractor import apply_shape_cache, parse_with_paths
from backend.ingest.registry import get_adapter

logger = logging.getLogger(__name__)


def generate_token() -> str:
    """Generate a secure random ingest token (returned once on creation)."""
    return f"opsmender_ingest_{secrets.token_urlsafe(32)}"


def hash_token(raw: str) -> str:
    """SHA-256 hash of the raw token for storage."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_token(raw: str, hashed: str) -> bool:
    """Check if a raw token matches its hash (constant-time)."""
    return secrets.compare_digest(hash_token(raw), hashed)


@dataclasses.dataclass
class IngestResult:
    """Outcome of a single ingest call."""

    success: bool
    incident_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    auto_start_tier: int | None = None
    dedup_action: str | None = None  # created | updated | skipped
    error: str | None = None


async def authenticate_token(
    db: AsyncSession,
    raw_token: str,
) -> IngestToken | None:
    """Validate an ingest token.  Returns the token row if valid, else None."""
    tokens = await IngestTokenRepo.list_all_global(db, active_only=True)
    
    for tok in tokens:
        if verify_token(raw_token, tok.token_hash):
            # Touch last_used_at
            await IngestTokenRepo.touch(db, tok.org_id, tok.id)
            return tok
    return None


async def ingest_incident(
    db: AsyncSession,
    *,
    token: IngestToken,
    payload: dict[str, Any],
    config: AppConfig,
) -> IngestResult:
    """Parse an inbound payload, dedup, and create/update an incident.

    Always creates an ``ingest_log`` entry regardless of outcome.
    """
    org_id = token.org_id
    provider = token.provider
    # For "auto" tokens, seed the adapter with any pre-learned paths for this payload shape.
    seeded_paths: dict[str, str] | None = None
    if provider == "auto" and isinstance(token.shape_cache, dict):
        from backend.ingest.llm_extractor import compute_shape_hash

        cached = token.shape_cache.get(compute_shape_hash(payload))
        if isinstance(cached, dict):
            seeded_paths = cached
    adapter = get_adapter(provider, field_mapping=seeded_paths)

    try:
        parsed = adapter.parse(payload)
    except ValueError as exc:
        error_msg = str(exc)

        # Special case: SNS subscription confirmation
        if error_msg.startswith("SNS_SUBSCRIPTION_CONFIRMATION:"):
            subscribe_url = error_msg.split(":", 1)[1]
            await IngestLogRepo.create(
                db,
                org_id,
                ingest_token_id=token.id,
                provider=provider,
                raw_payload=payload,
                dedup_action="skipped",
                error=f"SNS subscription confirmation: {subscribe_url}",
            )
            return IngestResult(
                success=True,
                dedup_action="skipped",
                error=f"SNS subscription confirmation — SubscribeURL: {subscribe_url}",
            )

        # Parsing error
        await IngestLogRepo.create(
            db,
            org_id,
            ingest_token_id=token.id,
            provider=provider,
            raw_payload=payload,
            error=error_msg,
        )
        return IngestResult(success=False, error=error_msg)

    # ── LLM fallback for the Universal adapter ─────────────────────────
    # If the heuristics couldn't find a title, consult the LLM to learn
    # which paths hold the incident fields, then re-parse with those paths.
    if provider == "auto" and parsed.needs_llm:
        learned_paths, cache_hit = await apply_shape_cache(
            db,
            org_id,
            token=token,
            payload=payload,
            config=config,
        )
        if learned_paths:
            logger.info(
                "ingest.auto: used %s paths for token=%s",
                "cached" if cache_hit else "llm-learned",
                token.id,
            )
            parsed = parse_with_paths(payload, learned_paths)

    # Namespace auto-provider dedup per-token so two tools sharing an
    # external_id but coming through different tokens don't collide.
    if provider == "auto":
        parsed.external_source = f"auto:{token.name}"

    # v1 maintenance behavior: matching active windows drop intake alerts
    # before a visible incident is created. The raw payload is still logged
    # for operator-owned audit/replay.
    service_id = token.service_id
    if service_id is not None:
        now = datetime.now(timezone.utc)
        service = await ServiceRepo.get_by_id(db, org_id, service_id)
        active_windows = await MaintenanceWindowRepo.list_active_at(db, org_id, now)
        for window in active_windows:
            matches = window.scope_type == "global"
            if window.scope_type == "service":
                service_ids = set(str(v) for v in (window.target_ids or []))
                if window.scope_id is not None:
                    service_ids.add(str(window.scope_id))
                matches = str(service_id) in service_ids
            elif window.scope_type == "team" and service is not None:
                team_ids = set(str(v) for v in (window.target_ids or []))
                if window.scope_id is not None:
                    team_ids.add(str(window.scope_id))
                matches = str(service.team_id) in team_ids
            if matches:
                await IngestLogRepo.create(
                    db,
                    org_id,
                    ingest_token_id=token.id,
                    provider=provider,
                    raw_payload=payload,
                    dedup_action="skipped",
                    error=f"Suppressed by maintenance window: {window.name}",
                )
                return IngestResult(success=True, dedup_action="skipped")

    # ── Dedup by external fingerprint ──────────────────────────────────
    dedup_action = "created"
    incident: Incident | None = None

    if parsed.external_id and parsed.external_source:
        existing = await IncidentRepo.get_by_external_fingerprint(
            db,
            org_id,
            external_source=parsed.external_source,
            external_id=parsed.external_id,
        )
        if existing is not None:
            # Update existing incident if status changed
            if parsed.status == "resolved" and existing.status != "resolved":
                await IncidentRepo.update_status(db, org_id, existing.id, "resolved")
                dedup_action = "updated"
            else:
                dedup_action = "skipped"
            incident = existing

    if incident is None:
        # Create a new incident. If the token is service-scoped, pre-fill
        # service_id so the paging engine routes to the owning team without
        # the alert payload needing to encode the service.
        incident = Incident(
            org_id=org_id,
            title=parsed.title,
            description=parsed.description,
            severity=parsed.severity,
            status=parsed.status,
            external_id=parsed.external_id,
            external_source=parsed.external_source,
            service_id=token.service_id,
        )
        db.add(incident)
        await db.flush()
        # Apply priority rules — locked at creation per D-021.
        from backend.paging.service import apply_priority_to_incident

        priority_result = await apply_priority_to_incident(db, org_id, incident)
        # Kick off escalation chain if this incident pages humans.
        if priority_result.response_mode in ("page", "escalate_immediate"):
            from backend.paging import escalation as _esc_kickoff

            link = await _esc_kickoff.select_chain_for_incident(
                db,
                org_id,
                service_id=incident.service_id,
                priority=priority_result.priority,
            )
            if link is not None:
                from backend.paging.channel_factory import build_channel_factory

                await _esc_kickoff.start_chain(
                    db,
                    org_id,
                    incident_id=incident.id,
                    chain_id=link.chain_id,
                    mode=priority_result.response_mode,
                    channel_factory=build_channel_factory(),
                )
                import os as _os
                from backend.paging.slack_channel_mirror import (
                    mirror_incident_to_slack_channel,
                )

                await mirror_incident_to_slack_channel(
                    db,
                    org_id,
                    incident=incident,
                    base_url=_os.environ.get("OPSMENDER_PUBLIC_URL"),
                )
        dedup_action = "created"

    # ── Audit log entry ────────────────────────────────────────────────
    await IngestLogRepo.create(
        db,
        org_id,
        ingest_token_id=token.id,
        provider=provider,
        raw_payload=payload,
        incident_id=incident.id,
        dedup_action=dedup_action,
    )

    logger.info(
        "ingest: provider=%s action=%s incident=%s external_id=%s",
        provider,
        dedup_action,
        incident.id,
        parsed.external_id,
    )

    # ── Write uptime sample if availability signal present ──────────
    if parsed.availability is not None:
        avail = parsed.availability
        sla_target = await SLATargetRepo.get_by_name(db, org_id, avail.target_name)
        if sla_target is not None:
            incident.target_id = sla_target.id
            await UptimeSampleRepo.create(
                db,
                org_id,
                target_id=sla_target.id,
                up=avail.up,
                latency_ms=avail.latency_ms,
                source=avail.source,
            )
            logger.info(
                "ingest.availability: target=%s up=%s latency_ms=%s source=%s",
                avail.target_name,
                avail.up,
                avail.latency_ms,
                avail.source,
            )
        else:
            logger.debug(
                "ingest.availability: no SLA target matched for name=%r",
                avail.target_name,
            )

    policy = await load_auto_start_policy(db, org_id, config, incident=incident)
    auto_start_skip = auto_start_skip_reason(
        incident,
        dedup_action=dedup_action,
        policy=policy,
    )
    if auto_start_skip is None:
        logger.info(
            "ingest.auto_start: queued incident=%s tier=%s source=%s severity=%s",
            incident.id,
            policy.session_tier,
            incident.external_source,
            incident.severity,
        )
    elif policy.enabled:
        logger.info(
            "ingest.auto_start: %s incident=%s resolved_tier=%s",
            auto_start_skip,
            incident.id,
            policy.session_tier,
        )

    return IngestResult(
        success=True,
        incident_id=incident.id,
        auto_start_tier=policy.session_tier if auto_start_skip is None else None,
        dedup_action=dedup_action,
    )
