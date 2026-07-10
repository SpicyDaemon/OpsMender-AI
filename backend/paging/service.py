"""Side-effect bridge between the paging algorithms and the DB.

``apply_priority_to_incident`` is the single entry point called from
incident-creation paths (manual REST create + inbound ingest). In v1,
service configuration is the source of truth for priority. Legacy priority
rules remain in the DB/API for compatibility but are not used to decide new
incident priority.
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Incident
from backend.db.repos import (
    PriorityRuleRepo,
    ServiceRepo,
)
from backend.paging.priority import (
    DEFAULT_MODE_FOR,
    PriorityAssignment,
    PriorityRuleLike,
    assign_priority,
)


def _to_rule_like(rule) -> PriorityRuleLike:
    return PriorityRuleLike(
        id=rule.id,
        name=rule.name,
        rule_index=rule.rule_index,
        condition=rule.condition or {},
        priority=rule.priority,
        response_mode=rule.response_mode,
        is_active=rule.is_active,
    )


def incident_to_payload(incident: Incident) -> dict[str, Any]:
    """Extract the matchable fields off an Incident for rule evaluation."""

    payload: dict[str, Any] = {
        "title": incident.title or "",
        "description": incident.description or "",
        "status": incident.status,
        "severity": incident.severity,
        "external_source": incident.external_source,
    }
    if incident.service_id is not None:
        payload["service_id"] = str(incident.service_id)
    return payload


async def compute_priority_for_payload(
    db: AsyncSession,
    org_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    service_id: uuid.UUID | None = None,
    llm_callback: Callable[[dict, str], Awaitable[tuple[str | None, str | None]]]
    | None = None,
) -> PriorityAssignment:
    """Return priority + response_mode for a payload.

    v1 prefers a service's configured priority. For unbound/manual incidents,
    fall back to a deterministic severity mapping rather than legacy rules.
    """

    if service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, service_id)
        if service is not None:
            priority = service.priority or "P2"
            return PriorityAssignment(
                priority=priority,
                response_mode=DEFAULT_MODE_FOR.get(priority, "notify"),
                matched_rule_id=None,
            )

    severity = str(payload.get("severity") or "").strip().lower()
    severity_priority = {
        "critical": "P0",
        "high": "P1",
        "medium": "P2",
        "low": "P3",
    }.get(severity, "P2")
    return PriorityAssignment(
        priority=severity_priority,
        response_mode=DEFAULT_MODE_FOR.get(severity_priority, "notify"),
        matched_rule_id=None,
    )


async def compute_priority_with_legacy_rules(
    db: AsyncSession,
    org_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    llm_callback: Callable[[dict, str], Awaitable[tuple[str | None, str | None]]]
    | None = None,
) -> PriorityAssignment:
    """Legacy helper kept for internal tests/tools that still exercise rules."""
    with db.no_autoflush:
        rules = await PriorityRuleRepo.list_all(db, org_id, active_only=True)
    return await assign_priority(
        payload,
        [_to_rule_like(r) for r in rules],
        llm_escalation_enabled=False,
        llm_callback=llm_callback,
    )


async def apply_priority_to_incident(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident: Incident,
    *,
    payload: dict[str, Any] | None = None,
    llm_callback: Callable[[dict, str], Awaitable[tuple[str | None, str | None]]]
    | None = None,
) -> PriorityAssignment:
    """Compute + persist priority/response_mode on a freshly-created incident.

    Used by the inbound-ingest path where the ``Incident`` row already exists
    by the time we have rules to apply. For the REST create path, prefer
    ``compute_priority_for_payload`` and pass the result through
    ``IncidentRepo.create`` to avoid an extra UPDATE.
    """

    if payload is None:
        payload = incident_to_payload(incident)

    result = await compute_priority_for_payload(
        db,
        org_id,
        payload,
        service_id=incident.service_id,
        llm_callback=llm_callback,
    )

    incident.priority = result.priority
    incident.response_mode = result.response_mode

    if result.llm_escalated:
        rule_priority = None
        if result.matched_rule_id is not None:
            rules = await PriorityRuleRepo.list_all(db, org_id, active_only=True)
            for rule in rules:
                if rule.id == result.matched_rule_id:
                    rule_priority = rule.priority
                    break
        await PriorityRuleRepo.log_llm_override(
            db,
            org_id,
            incident_id=incident.id,
            rule_priority=rule_priority or "P3",
            llm_priority=result.priority,
            llm_reason=result.llm_reason,
        )
    await db.flush()
    return result
