"""Distributed incident-created coordination."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import asyncpg

from backend.services.pg_bus import asyncpg_dsn, publish, subscribe

logger = logging.getLogger(__name__)

INCIDENT_CREATED_CHANNEL = "incident.created"


@dataclass(frozen=True)
class IncidentCreatedEvent:
    org_id: uuid.UUID
    incident_id: uuid.UUID
    auto_start_tier: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "org_id": str(self.org_id),
            "incident_id": str(self.incident_id),
            "auto_start_tier": self.auto_start_tier,
        }

    @classmethod
    def from_payload(cls, payload: Any) -> "IncidentCreatedEvent":
        if not isinstance(payload, dict):
            raise ValueError("incident.created payload must be an object")
        tier = payload.get("auto_start_tier")
        if tier is not None:
            tier = int(tier)
            if tier not in {0, 1, 2}:
                raise ValueError("incident.created auto_start_tier must be 0, 1, or 2")
        return cls(
            org_id=uuid.UUID(str(payload["org_id"])),
            incident_id=uuid.UUID(str(payload["incident_id"])),
            auto_start_tier=tier,
        )


class IncidentEventPublisher:
    """Publish incident events using a short-lived PostgreSQL connection."""

    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable[..., Any] = asyncpg.connect,
    ) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self._connect = connect

    async def publish_created(self, event: IncidentCreatedEvent) -> None:
        conn = await self._connect(self._dsn)
        try:
            await publish(conn, INCIDENT_CREATED_CHANNEL, event.to_payload())
        finally:
            await conn.close()


async def dispatch_incident_created(
    app,
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    auto_start_tier: int | None,
) -> None:
    """Call the monolith handler directly or publish for a distributed worker."""

    event = IncidentCreatedEvent(
        org_id=org_id,
        incident_id=incident_id,
        auto_start_tier=auto_start_tier,
    )
    if app.state.config.deployment.mode == "monolith":
        if auto_start_tier is not None:
            from backend.ingest.autostart import schedule_auto_started_session

            schedule_auto_started_session(
                app,
                org_id=org_id,
                incident_id=incident_id,
                tier=auto_start_tier,
            )
        return

    publisher: IncidentEventPublisher = app.state.incident_event_publisher
    await publisher.publish_created(event)


async def start_incident_created_subscriber(app, database_url: str):
    """Open the worker's durable LISTEN connection."""

    conn = await asyncpg.connect(asyncpg_dsn(database_url))

    async def handle(payload: Any) -> None:
        try:
            event = IncidentCreatedEvent.from_payload(payload)
        except (KeyError, TypeError, ValueError):
            logger.warning("Invalid incident.created payload: %r", payload)
            return
        if event.auto_start_tier is None:
            return
        from backend.ingest.autostart import schedule_auto_started_session

        schedule_auto_started_session(
            app,
            org_id=event.org_id,
            incident_id=event.incident_id,
            tier=event.auto_start_tier,
        )

    unsubscribe = await subscribe(conn, INCIDENT_CREATED_CHANNEL, handle)
    return conn, unsubscribe
