"""Distributed deployment role and PostgreSQL bus tests."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from backend.api.app import create_app
from backend.config_loader import AppConfig, resolve_deployment_config
from backend.services.incident_events import (
    IncidentCreatedEvent,
    IncidentEventPublisher,
    dispatch_incident_created,
)
from backend.services.pg_bus import publish, subscribe


def _config(tmp_path, monkeypatch, *, mode: str, role: str | None = None):
    monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", mode)
    monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "development")
    if role is None:
        monkeypatch.delenv("OPSMENDER_SERVICE_ROLE", raising=False)
    else:
        monkeypatch.setenv("OPSMENDER_SERVICE_ROLE", role)
    env_file = tmp_path / f"{mode}-{role or 'none'}.env"
    env_file.write_text(
        "OPSMENDER_JWT_SECRET=distributed-test-secret\n"
        "OPSMENDER_DATABASE_URL=sqlite+aiosqlite://\n",
        encoding="utf-8",
    )
    return AppConfig.load(env_file)


def _paths(app) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_legacy_development_mode_remains_full_monolith(tmp_path, monkeypatch):
    app = create_app(_config(tmp_path, monkeypatch, mode="development"))
    paths = _paths(app)

    assert app.state.deployment_mode == "monolith"
    assert app.state.service_role == "all"
    assert "/auth/login" in paths
    assert "/incidents/ingest" in paths
    assert "/bot/slack/interactions" in paths


def test_distributed_api_registers_user_api_only(tmp_path, monkeypatch):
    app = create_app(_config(tmp_path, monkeypatch, mode="distributed", role="api"))
    paths = _paths(app)

    assert "/auth/login" in paths
    assert "/incidents" in paths
    assert "/ingest-tokens" in paths
    assert "/incidents/ingest" not in paths
    assert "/bot/slack/interactions" not in paths


def test_distributed_dispatcher_registers_inbound_routes_only(tmp_path, monkeypatch):
    app = create_app(
        _config(tmp_path, monkeypatch, mode="distributed", role="dispatcher")
    )
    paths = _paths(app)

    assert "/incidents/ingest" in paths
    assert "/api/v1/intake/{service_token}" in paths
    assert "/bot/slack/interactions" in paths
    assert "/bot/teams/activity" in paths
    assert "/bot-connectors/{connector_id}/discord/webhook" in paths
    assert "/auth/login" not in paths
    assert "/ingest-tokens" not in paths


@pytest.mark.parametrize("role", ["worker", "scheduler"])
def test_non_http_roles_register_health_only(tmp_path, monkeypatch, role):
    app = create_app(_config(tmp_path, monkeypatch, mode="distributed", role=role))
    paths = _paths(app)

    assert "/health" in paths
    assert "/auth/login" not in paths
    assert "/incidents" not in paths
    assert "/incidents/ingest" not in paths


def test_deployment_config_validates_distributed_role():
    with pytest.raises(ValueError, match="OPSMENDER_SERVICE_ROLE"):
        resolve_deployment_config(
            {
                "OPSMENDER_DEPLOYMENT_MODE": "distributed",
                "OPSMENDER_ENVIRONMENT": "production",
                "OPSMENDER_SERVICE_ROLE": "unknown",
            }
        )


class FakeConnection:
    def __init__(self):
        self.executed = []
        self.listeners = {}
        self.closed = False

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def add_listener(self, channel, callback):
        self.listeners[channel] = callback

    async def remove_listener(self, channel, callback):
        assert self.listeners[channel] is callback
        del self.listeners[channel]

    async def close(self):
        self.closed = True


async def test_pg_bus_publish_and_subscribe_json_payload():
    conn = FakeConnection()
    received = []

    await publish(conn, "incident.created", {"incident_id": "abc"})
    unsubscribe = await subscribe(
        conn, "incident.created", lambda payload: received.append(payload)
    )
    conn.listeners["incident.created"](conn, 1, "incident.created", '{"ok":true}')
    await asyncio.sleep(0)

    assert conn.executed == [
        (
            "SELECT pg_notify($1, $2)",
            ("incident.created", '{"incident_id":"abc"}'),
        )
    ]
    assert received == [{"ok": True}]
    await unsubscribe()
    assert conn.listeners == {}


async def test_incident_event_publisher_closes_connection():
    conn = FakeConnection()

    async def connect(_dsn):
        return conn

    publisher = IncidentEventPublisher(
        "postgresql+asyncpg://user:pw@db/opsmender",
        connect=connect,
    )
    event = IncidentCreatedEvent(
        org_id=uuid.uuid4(),
        incident_id=uuid.uuid4(),
        auto_start_tier=0,
    )

    await publisher.publish_created(event)

    assert conn.executed[0][1][0] == "incident.created"
    assert conn.closed is True


async def test_distributed_dispatch_publishes_incident_created():
    events = []

    class FakePublisher:
        async def publish_created(self, event):
            events.append(event)

    app = SimpleNamespace(
        state=SimpleNamespace(
            config=SimpleNamespace(deployment=SimpleNamespace(mode="distributed")),
            incident_event_publisher=FakePublisher(),
        )
    )
    org_id = uuid.uuid4()
    incident_id = uuid.uuid4()

    await dispatch_incident_created(
        app,
        org_id=org_id,
        incident_id=incident_id,
        auto_start_tier=0,
    )

    assert events == [
        IncidentCreatedEvent(
            org_id=org_id,
            incident_id=incident_id,
            auto_start_tier=0,
        )
    ]
