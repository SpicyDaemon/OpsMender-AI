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

from backend.db.models import Incident, IngestToken
from backend.db.repos import IncidentRepo, IngestLogRepo, IngestTokenRepo
from backend.ingest.registry import get_adapter

logger = logging.getLogger(__name__)


def generate_token() -> str:
    """Generate a secure random ingest token (returned once on creation)."""
    return f"aim_ingest_{secrets.token_urlsafe(32)}"


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
    dedup_action: str | None = None  # created | updated | skipped
    error: str | None = None


async def authenticate_token(
    db: AsyncSession,
    raw_token: str,
) -> IngestToken | None:
    """Validate an ingest token.  Returns the token row if valid, else None."""
    tokens = await IngestTokenRepo.list_all(db, active_only=True)
    for tok in tokens:
        if verify_token(raw_token, tok.token_hash):
            # Touch last_used_at
            await IngestTokenRepo.touch(db, tok.id)
            return tok
    return None


async def ingest_incident(
    db: AsyncSession,
    *,
    token: IngestToken,
    payload: dict[str, Any],
) -> IngestResult:
    """Parse an inbound payload, dedup, and create/update an incident.

    Always creates an ``ingest_log`` entry regardless of outcome.
    """
    provider = token.provider
    adapter = get_adapter(provider)

    try:
        parsed = adapter.parse(payload)
    except ValueError as exc:
        error_msg = str(exc)

        # Special case: SNS subscription confirmation
        if error_msg.startswith("SNS_SUBSCRIPTION_CONFIRMATION:"):
            subscribe_url = error_msg.split(":", 1)[1]
            await IngestLogRepo.create(
                db,
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
            ingest_token_id=token.id,
            provider=provider,
            raw_payload=payload,
            error=error_msg,
        )
        return IngestResult(success=False, error=error_msg)

    # ── Dedup by external fingerprint ──────────────────────────────────
    dedup_action = "created"
    incident: Incident | None = None

    if parsed.external_id and parsed.external_source:
        existing = await IncidentRepo.get_by_external_fingerprint(
            db,
            external_source=parsed.external_source,
            external_id=parsed.external_id,
        )
        if existing is not None:
            # Update existing incident if status changed
            if parsed.status == "resolved" and existing.status != "resolved":
                await IncidentRepo.update_status(db, existing.id, "resolved")
                dedup_action = "updated"
            else:
                dedup_action = "skipped"
            incident = existing

    if incident is None:
        # Create a new incident
        incident = Incident(
            title=parsed.title,
            description=parsed.description,
            severity=parsed.severity,
            status=parsed.status,
            external_id=parsed.external_id,
            external_source=parsed.external_source,
        )
        db.add(incident)
        await db.flush()
        dedup_action = "created"

    # ── Audit log entry ────────────────────────────────────────────────
    await IngestLogRepo.create(
        db,
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

    return IngestResult(
        success=True,
        incident_id=incident.id,
        dedup_action=dedup_action,
    )
