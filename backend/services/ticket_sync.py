"""Non-blocking outbound ticket status synchronization."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from backend.db.repos import (
    IntegrationConnectorRepo,
    TicketSyncStateRepo,
)
from backend.integrations.registry import get_adapter

logger = logging.getLogger(__name__)

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
