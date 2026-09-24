"""Inform the receiving service when a provider fingerprint belongs elsewhere."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Incident, IngestLog, Service
from backend.db.repos import (
    EscalationChainRepo,
    EscalationStepRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    UserRepo,
)
from backend.notifications import CATEGORY_INCIDENT, emit_to_users
from backend.paging.escalation import _resolve_step_targets

log = logging.getLogger(__name__)

COLLISION_EVENT = "incident.collision"
_MARKER_RE = re.compile(r"^collision:v1:[0-9a-fA-F-]{36}:\s*")


def ingest_note_text(error: str) -> str:
    """Readable form of an ingest-log note, without the collision marker."""
    return _MARKER_RE.sub("", error)


def _safe(value: str) -> str:
    # Names and fingerprints are untrusted provider/operator text. Keep log
    # records single-line and prevent chat mention or markup expansion.
    return (
        " ".join(value.split())
        .replace("@", "＠")
        .replace("<", "‹")
        .replace(">", "›")[:160]
    )


async def record_collision(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    losing_service: Service,
    fingerprint: str,
) -> tuple[str, str | None]:
    """Persist one notice per owner incident and losing service.

    The owning incident row serializes competing Postgres intake transactions.
    The caller creates the ordinary IngestLog entry separately on every delivery.
    """
    locked = (
        await db.execute(
            select(Incident)
            .where(Incident.org_id == org_id, Incident.id == incident.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if locked.status not in ("open", "in_progress"):
        return "", None
    owner = (
        await ServiceRepo.get_by_id(db, org_id, locked.service_id)
        if locked.service_id is not None
        else None
    )
    owner_name = _safe(owner.name) if owner else "Unassigned service"
    loser_name = _safe(losing_service.name)
    marker = f"collision:v1:{losing_service.id}:"
    explanation = (
        f"{marker} Alert for {loser_name} was folded into incident "
        f"{locked.id} owned by {owner_name}."
    )
    log.warning(
        "ingest collision: owner=%s receiving=%s fingerprint=%s incident=%s",
        owner_name,
        loser_name,
        _safe(fingerprint),
        locked.id,
    )
    already = (
        await db.execute(
            select(IngestLog.id)
            .where(
                IngestLog.org_id == org_id,
                IngestLog.incident_id == locked.id,
                IngestLog.error.like(f"{marker}%"),
            )
            .limit(1)
        )
    ).first()
    if already:
        return explanation, None

    links = sorted(
        await ServiceEscalationChainRepo.list_for_service(
            db, org_id, losing_service.id
        ),
        key=lambda link: str(link.id),
    )
    # Priority comes from the service, so pick the chain the receiving service
    # would have paged for an incident of its own, not the owner's priority.
    priority = losing_service.priority or "P2"
    matching = []
    defaults = []
    for link in links:
        condition = link.applies_when or {}
        priorities = (
            condition.get("priorities") if isinstance(condition, dict) else None
        )
        if priorities and priority in {str(p).upper() for p in priorities}:
            matching.append(link)
        elif not priorities:
            defaults.append(link)
    selected = (matching or defaults or [None])[0]
    responders: set[uuid.UUID] = set()
    if selected is None:
        log.warning(
            "ingest collision: no eligible chain for service=%s", losing_service.id
        )
    if selected is not None:
        chain = await EscalationChainRepo.get_by_id(db, org_id, selected.chain_id)
        # A service may link a chain owned by another team (a shared on-call
        # chain); its responders are the people this alert would have paged.
        if chain is not None and chain.is_active:
            for step in await EscalationStepRepo.list_for_chain(db, org_id, chain.id):
                try:
                    targets = await _resolve_step_targets(
                        db,
                        org_id,
                        target_type=step.target_type,
                        target_id=step.target_id,
                        at=datetime.now(timezone.utc),
                    )
                except (ValueError, LookupError):
                    log.warning(
                        "ingest collision: invalid target in chain=%s", chain.id
                    )
                    continue
                for user_id in targets:
                    user = await UserRepo.get_by_id(db, user_id)
                    if user is not None and user.is_active and user.deleted_at is None:
                        responders.add(user_id)
        else:
            log.warning(
                "ingest collision: unavailable chain for service=%s",
                losing_service.id,
            )
            selected = None

    if selected is not None and not responders:
        log.warning(
            "ingest collision: no active responders for service=%s",
            losing_service.id,
        )

    path = f"/dashboard/incidents/detail?id={locked.id}"
    text = (
        f"Alert for {loser_name} was absorbed into incident {locked.id} "
        f"owned by {owner_name}. {path}"
    )
    # Same path as every other Inbox producer: honours per-category mute and
    # quiet hours (live push only), and persists in the intake transaction.
    await emit_to_users(
        db,
        org_id,
        sorted(responders, key=str),
        event_type=COLLISION_EVENT,
        category=CATEGORY_INCIDENT,
        title="Alert assigned to another service",
        body=text,
        link=path,
        incident_id=locked.id,
    )
    return explanation, text if selected is not None else None
