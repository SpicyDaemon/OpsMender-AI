"""Non-blocking outbound ticket status synchronization."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from backend.db.repos import (
    IncidentIntegrationLinkRepo,
    IncidentRepo,
    IntegrationConnectorRepo,
    ServiceRepo,
    TicketSyncStateRepo,
)
from backend.integrations.registry import get_adapter

logger = logging.getLogger(__name__)

# Integration kinds that can hold an incident's ticket. Each exposes a create
# method that returns ``integration_link`` + ``ticket_sync`` payloads when given
# an ``incident_id`` (see the jira/servicenow adapters).
TICKETING_KINDS = {"jira", "servicenow"}

DEFAULT_STATUS_MAPS: dict[str, dict[str, str]] = {
    "jira": {
        "open": "To Do",
        "in_progress": "In Progress",
        "resolved": "Done",
    },
    "servicenow": {
        "open": "1",
        "in_progress": "2",
        "resolved": "6",
    },
}


def normalized_status_map(kind: str, value: Any) -> dict[str, str]:
    base = dict(DEFAULT_STATUS_MAPS.get(kind, {}))
    if isinstance(value, dict):
        for key, mapped in value.items():
            if key in {"open", "in_progress", "resolved"} and mapped is not None:
                base[key] = str(mapped)
    return base


def reverse_status(status_map: dict[str, Any], external_status: str) -> str | None:
    target = str(external_status).strip().casefold()
    for internal, external in status_map.items():
        if str(external).strip().casefold() == target:
            return internal
    return None


async def sync_incident_status(
    factory,
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    new_status: str,
) -> None:
    try:
        async with factory() as db:
            states = await TicketSyncStateRepo.list_for_incident(
                db,
                org_id,
                incident_id,
            )
            for state in states:
                connector = await IntegrationConnectorRepo.get_by_id(
                    db,
                    org_id,
                    state.integration_connector_id,
                )
                if (
                    connector is None
                    or not connector.is_enabled
                    or not connector.config.get("ticket_sync_enabled")
                ):
                    continue
                adapter = get_adapter(connector.kind)
                if adapter is None:
                    continue
                status_map = normalized_status_map(
                    connector.kind,
                    state.status_map or connector.config.get("status_map"),
                )
                external_status = status_map.get(new_status)
                if not external_status:
                    continue
                try:
                    auth = IntegrationConnectorRepo.decrypt_auth(connector)
                    result = await adapter.safe_invoke(
                        "sync_status_out",
                        connector,
                        auth,
                        {
                            "ticket_id": state.external_ticket_id,
                            "new_status": external_status,
                        },
                    )
                    if result.ok:
                        await TicketSyncStateRepo.mark_synced(
                            db,
                            state,
                            direction="outbound",
                        )
                    else:
                        logger.warning(
                            "ticket sync failed connector=%s incident=%s: %s",
                            connector.id,
                            incident_id,
                            result.error,
                        )
                except Exception:
                    logger.exception(
                        "ticket sync failed connector=%s incident=%s",
                        connector.id,
                        incident_id,
                    )
            await db.commit()
    except Exception:
        logger.exception("outbound ticket sync task failed incident=%s", incident_id)


def schedule_ticket_status_sync(
    app,
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    new_status: str,
) -> None:
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        return
    task = asyncio.create_task(
        sync_incident_status(
            factory,
            org_id=org_id,
            incident_id=incident_id,
            new_status=new_status,
        ),
        name=f"ticket-sync:{incident_id}:{new_status}",
    )
    registry = getattr(app.state, "background_tasks", None)
    if isinstance(registry, set):
        registry.add(task)
        task.add_done_callback(registry.discard)


def _create_params(kind: str, incident) -> tuple[str, dict[str, Any]]:
    """The adapter action + kwargs to open a ticket for *incident*."""
    summary = incident.title or f"Incident {incident.id}"
    description = incident.description or ""
    if kind == "servicenow":
        return "create_record", {
            "fields": {
                "short_description": summary,
                "description": description,
            },
            "incident_id": str(incident.id),
        }
    # jira (default)
    return "create_issue", {
        "summary": summary,
        "description": description,
        "incident_id": str(incident.id),
    }


async def provision_incident_tickets(
    factory,
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
) -> int:
    """Open + link a ticket per sync-enabled ticketing connector on the service.

    For each integration connector in the incident's service allowlist that is a
    ticketing kind with ``ticket_sync_enabled``, create a ticket (unless one is
    already linked), persist the incident link + ticket-sync state, then push the
    incident's current status onto it. Idempotent: connectors with an existing
    sync state are skipped, so a repeated dispatch never opens a duplicate."""

    created = 0
    try:
        async with factory() as db:
            incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
            if incident is None or incident.service_id is None:
                return 0
            service = await ServiceRepo.get_by_id(db, org_id, incident.service_id)
            if service is None:
                return 0
            already_linked = {
                state.integration_connector_id
                for state in await TicketSyncStateRepo.list_for_incident(
                    db, org_id, incident_id
                )
            }
            for raw_id in service.allowed_integration_connector_ids or []:
                try:
                    connector_id = uuid.UUID(str(raw_id))
                except (TypeError, ValueError):
                    continue
                if connector_id in already_linked:
                    continue
                connector = await IntegrationConnectorRepo.get_by_id(
                    db, org_id, connector_id
                )
                if (
                    connector is None
                    or not connector.is_enabled
                    or connector.kind not in TICKETING_KINDS
                    or not connector.config.get("ticket_sync_enabled")
                ):
                    continue
                adapter = get_adapter(connector.kind)
                if adapter is None:
                    continue
                action, params = _create_params(connector.kind, incident)
                try:
                    auth = IntegrationConnectorRepo.decrypt_auth(connector)
                    result = await adapter.safe_invoke(
                        action, connector, auth, params
                    )
                except Exception:
                    logger.exception(
                        "ticket provisioning failed connector=%s incident=%s",
                        connector.id,
                        incident_id,
                    )
                    continue
                if not result.ok:
                    logger.warning(
                        "ticket provisioning failed connector=%s incident=%s: %s",
                        connector.id,
                        incident_id,
                        result.error,
                    )
                    continue
                link = result.data.get("integration_link")
                if isinstance(link, dict):
                    await IncidentIntegrationLinkRepo.upsert(
                        db,
                        org_id,
                        incident_id=incident_id,
                        connector_id=connector.id,
                        reference_type=str(link.get("reference_type") or "ticket"),
                        external_id=str(link["external_id"]),
                        url=str(link["url"]),
                        title=str(link["title"]) if link.get("title") else None,
                        reference_meta={},
                    )
                sync = result.data.get("ticket_sync")
                if isinstance(sync, dict):
                    await TicketSyncStateRepo.upsert(
                        db,
                        org_id,
                        connector_id=connector.id,
                        incident_id=incident_id,
                        external_ticket_id=str(sync["external_ticket_id"]),
                        external_ticket_url=(
                            str(sync["external_ticket_url"])
                            if sync.get("external_ticket_url")
                            else None
                        ),
                        status_map=dict(connector.config.get("status_map") or {}),
                    )
                    created += 1
            await db.commit()
    except Exception:
        logger.exception(
            "ticket provisioning task failed incident=%s", incident_id
        )
        return created

    if created:
        # Push the incident's current status onto the freshly opened ticket(s)
        # (e.g. "open" -> the admin-mapped status). A no-op/illegal transition is
        # logged and ignored inside sync_incident_status.
        async with factory() as db:
            incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
            status = incident.status if incident is not None else None
        if status:
            await sync_incident_status(
                factory, org_id=org_id, incident_id=incident_id, new_status=status
            )
    return created


def schedule_incident_ticket_provisioning(
    app,
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
) -> None:
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        return
    task = asyncio.create_task(
        provision_incident_tickets(
            factory, org_id=org_id, incident_id=incident_id
        ),
        name=f"ticket-provision:{incident_id}",
    )
    registry = getattr(app.state, "background_tasks", None)
    if isinstance(registry, set):
        registry.add(task)
        task.add_done_callback(registry.discard)
