"""Tests for incident-card delivery to Notification Channels.

Covers the v1 honesty/security contract: enabled channels with the
``notifications`` capability receive an incident message carrying an
authenticated incident link and NO public action URL; disabled channels and
channels lacking the capability receive nothing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.bots import notifier
from backend.bots.incident_card import build_incident_message, incident_link
from backend.db.models import Base
from backend.db.repos import (
    BotConnectorRepo,
    IncidentPageRepo,
    IncidentRepo,
    UserRepo,
)

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_connector(
    factory,
    *,
    capabilities,
    platform="telegram",
    allowed_chat_ids=None,
    is_enabled=True,
    name="chan",
):
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name=name,
            platform=platform,
            config={"allowed_chat_ids": allowed_chat_ids or []},
            credentials={"bot_token": "BOT-TOKEN"},
            allowed_capabilities=list(capabilities),
            status="configured",
            is_enabled=is_enabled,
        )
        await db.commit()
        return connector.id


async def _make_incident(factory, *, title="DB outage", severity="high"):
    async with factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title=title,
            description="connection pool exhausted",
            severity=severity,
        )
        await db.commit()
        return incident.id


# ---------------------------------------------------------------------------
# Message content (unit) — authenticated link, no public action URL
# ---------------------------------------------------------------------------


class _StubIncident:
    def __init__(self):
        self.id = uuid.uuid4()
        self.title = "Checkout 500s"
        self.description = "Spike in 500s on checkout"
        self.status = "open"
        self.severity = "critical"
        self.priority = "P1"
        self.external_source = "cloudwatch"
        from datetime import datetime, timezone

        self.created_at = datetime(2026, 6, 5, tzinfo=timezone.utc)


def test_message_carries_authenticated_link_and_no_public_action_url():
    inc = _StubIncident()
    text = build_incident_message(
        inc,
        event_type="incident.created",
        base_url="https://ops.example.com",
        supports_actions=False,
    )
    assert inc.title in text
    assert "critical" in text
    link = incident_link(inc.id, base_url="https://ops.example.com")
    assert link in text
    assert link.startswith("https://ops.example.com/dashboard/incidents/detail?id=")
    # Honesty/security: delivery-only fallback points the user into OpsMender,
    # and there is no public action-mutation URL embedded in the message.
    assert "Sign in to OpsMender" in text
    assert "token=" not in text
    assert "/ack?" not in text and "/resolve?" not in text


def test_message_omits_signin_hint_when_actions_supported():
    # Future adapters that gain verified callbacks would render real controls;
    # the sign-in fallback is then suppressed.
    inc = _StubIncident()
    text = build_incident_message(inc, supports_actions=True)
    assert "Sign in to OpsMender" not in text


def test_headline_reflects_event_type():
    inc = _StubIncident()
    assert "acknowledged" in build_incident_message(
        inc, event_type="incident.acknowledged"
    ).lower()
    assert "resolved" in build_incident_message(
        inc, event_type="incident.resolved"
    ).lower()


# ---------------------------------------------------------------------------
# Delivery fan-out
# ---------------------------------------------------------------------------


class TestIncidentEventFanOut:
    async def test_created_event_delivers_to_notifications_channel(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(*, bot_token, chat_id, text, **kwargs):
            sent.append({"chat_id": chat_id, "text": text})
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory, capabilities=["notifications"], allowed_chat_ids=["-100A", "-100B"]
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.created",
            base_url="https://ops.example.com",
        )

        assert {s["chat_id"] for s in sent} == {"-100A", "-100B"}
        assert all("DB outage" in s["text"] for s in sent)
        assert all("https://ops.example.com/dashboard/incidents/detail" in s["text"] for s in sent)
        assert all("token=" not in s["text"] for s in sent)

    async def test_resolved_event_delivers(self, factory, monkeypatch):
        sent = []

        async def fake_send(*, bot_token, chat_id, text, **kwargs):
            sent.append(text)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory, capabilities=["notifications"], allowed_chat_ids=["-100A"]
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.resolved",
        )
        assert len(sent) == 1
        assert "resolved" in sent[0].lower()

    async def test_skips_connector_without_notifications_capability(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory, capabilities=["incident_lookup"], allowed_chat_ids=["-100A"]
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.created",
        )
        assert sent == []

    async def test_skips_disabled_connector(self, factory, monkeypatch):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-100A"],
            is_enabled=False,
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.created",
        )
        assert sent == []

    async def test_escalated_event_delivers_with_escalation_context(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(*, bot_token, chat_id, text, **kwargs):
            sent.append(text)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory, capabilities=["notifications"], allowed_chat_ids=["-100A"]
        )
        incident_id = await _make_incident(factory)

        # Two escalation pages: step 0 -> Alice, step 1 -> Bob (escalated to Bob).
        async with factory() as db:
            alice = await UserRepo.create(
                db, username="alice", email="a@x.io", password_hash="x",
                first_name="Alice", last_name="A",
            )
            bob = await UserRepo.create(
                db, username="bob", email="b@x.io", password_hash="x",
                first_name="Bob", last_name="B",
            )
            await IncidentPageRepo.create(
                db, TEST_ORG_ID, incident_id=incident_id, user_id=alice.id, step_index=0
            )
            await IncidentPageRepo.create(
                db, TEST_ORG_ID, incident_id=incident_id, user_id=bob.id, step_index=1
            )
            await db.commit()

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.escalated",
            base_url="https://ops.example.com",
        )

        assert len(sent) == 1
        text = sent[0]
        assert "escalated" in text.lower()
        assert "Escalated to Alice B" not in text  # Bob is the current target
        assert "Escalated to Bob B" in text
        assert "Escalation level: 1" in text
        assert "Previous responder: Alice A" in text
        assert "https://ops.example.com/dashboard/incidents/detail" in text
        assert "token=" not in text

    async def test_disabled_channel_skips_escalation(self, factory, monkeypatch):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-100A"],
            is_enabled=False,
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.escalated",
        )
        assert sent == []

    async def test_unknown_event_type_is_ignored(self, factory, monkeypatch):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory, capabilities=["notifications"], allowed_chat_ids=["-100A"]
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.deleted",
        )
        assert sent == []
