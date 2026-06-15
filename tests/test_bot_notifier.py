"""Tests for outbound Telegram delivery (session events + co-pilot relay)."""

from __future__ import annotations

import pytest
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.bots.notifier as notifier_mod
from backend.bots import notifier
from backend.db.models import Base, BotActionAudit
from backend.db.repos import (
    BotActionAuditRepo,
    BotConnectorRepo,
    IncidentRepo,
    SessionRepo,
)


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
    allowed_chat_ids=None,
    is_enabled=True,
    name="tg-conn",
):
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name=name,
            platform="telegram",
            config={"allowed_chat_ids": allowed_chat_ids or []},
            credentials={"bot_token": "BOT-TOKEN"},
            allowed_capabilities=list(capabilities),
            status="configured",
            is_enabled=is_enabled,
        )
        await db.commit()
        return connector.id


class TestSessionChatEventFanOut:
    async def test_delivers_to_notifications_capable_connector(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(
            *, bot_token, chat_id, text, parse_mode="Markdown", timeout_seconds=10.0
        ):
            sent.append({"token": bot_token, "chat_id": chat_id, "text": text})
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-100777", "-100888"],
        )

        async with factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="DB outage",
                description="conn pool exhausted",
                severity="high",
            )
            session = await SessionRepo.create(
                db, TEST_ORG_ID, incident_id=incident.id, tier=1
            )
            await db.commit()
            session_id = session.id

        await notifier.deliver_session_chat_event(
            factory,
            org_id=TEST_ORG_ID,
            event_type="session.created",
            session_id=session_id,
        )

        assert len(sent) == 2
        assert {s["chat_id"] for s in sent} == {"-100777", "-100888"}
        assert all("DB outage" in s["text"] for s in sent)
        assert all(s["token"] == "BOT-TOKEN" for s in sent)

    async def test_completed_post_includes_summary(self, factory, monkeypatch):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)
        await _make_connector(
            factory, capabilities=["notifications"], allowed_chat_ids=["-100777"]
        )
        async with factory() as db:
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="DB outage", description="x", severity="high"
            )
            session = await SessionRepo.create(
                db, TEST_ORG_ID, incident_id=incident.id, tier=1
            )
            session.summary = "Restarted the connection pool; service recovered."
            await db.commit()
            session_id = session.id

        await notifier.deliver_session_chat_event(
            factory,
            org_id=TEST_ORG_ID,
            event_type="session.completed",
            session_id=session_id,
        )
        assert sent
        assert all(
            "Summary: Restarted the connection pool" in s["text"] for s in sent
        )

    async def test_non_completed_post_omits_summary(self, factory, monkeypatch):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)
        await _make_connector(
            factory, capabilities=["notifications"], allowed_chat_ids=["-100777"]
        )
        async with factory() as db:
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="DB outage", description="x", severity="high"
            )
            session = await SessionRepo.create(
                db, TEST_ORG_ID, incident_id=incident.id, tier=1
            )
            session.summary = "should not appear on a created event"
            await db.commit()
            session_id = session.id

        await notifier.deliver_session_chat_event(
            factory,
            org_id=TEST_ORG_ID,
            event_type="session.created",
            session_id=session_id,
        )
        assert sent
        assert all("Summary:" not in s["text"] for s in sent)

    async def test_skips_connector_without_notifications_capability(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        await _make_connector(
            factory,
            capabilities=["incident_lookup"],
            allowed_chat_ids=["-100777"],
        )

        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await db.commit()
            session_id = session.id

        await notifier.deliver_session_chat_event(
            factory,
            org_id=TEST_ORG_ID,
            event_type="session.completed",
            session_id=session_id,
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
            allowed_chat_ids=["-100777"],
            is_enabled=False,
        )

        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await db.commit()
            session_id = session.id

        await notifier.deliver_session_chat_event(
            factory,
            org_id=TEST_ORG_ID,
            event_type="session.created",
            session_id=session_id,
        )

        assert sent == []

    async def test_delivery_failure_marks_connector_error_and_audits(
        self, factory, monkeypatch
    ):
        async def fake_send(**kwargs):
            return False, "http 502: bad gateway"

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        connector_id = await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-100777"],
        )

        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await db.commit()
            session_id = session.id

        await notifier.deliver_session_chat_event(
            factory,
            org_id=TEST_ORG_ID,
            event_type="session.created",
            session_id=session_id,
        )

        async with factory() as db:
            connector = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, connector_id)
            assert connector is not None
            assert connector.status == "error"
            assert "502" in (connector.last_error or "")

            entries = (
                (
                    await db.execute(
                        select(BotActionAudit).where(
                            BotActionAudit.connector_id == connector_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(entries) == 1
            assert entries[0].status == "delivery_failed"
            assert entries[0].command == "notify:session.created"


class TestCopilotRelayBack:
    async def test_relays_to_originating_chats(self, factory, monkeypatch):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        connector_id = await _make_connector(
            factory,
            capabilities=["copilot_chat"],
        )

        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await db.commit()
            session_id = session.id

            # Two distinct originating chats + one duplicate (newest wins)
            await BotActionAuditRepo.create(
                db,
                TEST_ORG_ID,
                connector_id=connector_id,
                platform="telegram",
                chat_id="-100A",
                command="/chat",
                status="ok",
                session_id=session_id,
            )
            await BotActionAuditRepo.create(
                db,
                TEST_ORG_ID,
                connector_id=connector_id,
                platform="telegram",
                chat_id="-100B",
                command="/chat",
                status="ok",
                session_id=session_id,
            )
            await BotActionAuditRepo.create(
                db,
                TEST_ORG_ID,
                connector_id=connector_id,
                platform="telegram",
                chat_id="-100A",
                command="/chat",
                status="ok",
                session_id=session_id,
            )
            await db.commit()

        await notifier.deliver_copilot_relay(
            factory,
            org_id=TEST_ORG_ID,
            session_id=session_id,
            reply_text="restart succeeded",
        )

        assert {s["chat_id"] for s in sent} == {"-100A", "-100B"}
        assert all("restart succeeded" in s["text"] for s in sent)

    async def test_skips_connector_without_copilot_chat_capability(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        connector_id = await _make_connector(
            factory,
            capabilities=["incident_lookup"],
        )

        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await db.commit()
            session_id = session.id
            await BotActionAuditRepo.create(
                db,
                TEST_ORG_ID,
                connector_id=connector_id,
                platform="telegram",
                chat_id="-100A",
                command="/chat",
                status="ok",
                session_id=session_id,
            )
            await db.commit()

        await notifier.deliver_copilot_relay(
            factory,
            org_id=TEST_ORG_ID,
            session_id=session_id,
            reply_text="hello",
        )

        assert sent == []

    async def test_no_originating_chat_no_delivery(self, factory, monkeypatch):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        async with factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await db.commit()
            session_id = session.id

        await notifier.deliver_copilot_relay(
            factory,
            org_id=TEST_ORG_ID,
            session_id=session_id,
            reply_text="hello",
        )

        assert sent == []
