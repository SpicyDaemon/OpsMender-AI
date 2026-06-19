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
from backend.bots.delivery import DeliveryReceipt, UpdateResult
from backend.bots.incident_card import build_incident_message, incident_link
from backend.db.models import Base
from backend.db.repos import (
    BotConnectorRepo,
    IncidentNotificationReceiptRepo,
    IncidentPageRepo,
    IncidentRepo,
    ServiceRepo,
    SessionRepo,
    TeamRepo,
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
    name=None,
    team_ids=None,
    lanes=None,
):
    config = {"allowed_chat_ids": allowed_chat_ids or []}
    if team_ids is not None:
        config["team_scope"] = "teams"
        config["team_ids"] = [str(team_id) for team_id in team_ids]
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name=name or f"chan-{uuid.uuid4()}",
            platform=platform,
            config=config,
            credentials={"bot_token": "BOT-TOKEN"},
            allowed_capabilities=list(capabilities),
            lanes=lanes,
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


async def _make_service_incident(factory, *, team_name="Platform", slug="platform"):
    async with factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name=team_name, slug=slug)
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name=f"{team_name} API",
            slug=f"{slug}-api",
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title=f"{team_name} outage",
            description="connection pool exhausted",
            severity="high",
            service_id=service.id,
        )
        await db.commit()
        return team.id, service.id, incident.id


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
    async def test_track_status_posts_are_lane_gated_and_reuse_message_id(
        self, factory, monkeypatch
    ):
        class FakeSlackAdapter:
            platform = "slack"

            def __init__(self):
                self.sent = []
                self.updated = []

            async def send_incident_update(self, connector, **kwargs):
                self.sent.append((connector.name, kwargs))
                return DeliveryReceipt(
                    ok=True,
                    external_channel_id=kwargs["chat_id"],
                    external_message_id=f"msg-{connector.name}",
                    can_update=True,
                )

            async def update_incident_update(self, connector, **kwargs):
                self.updated.append((connector.name, kwargs))
                return UpdateResult(
                    ok=True,
                    receipt=DeliveryReceipt(
                        ok=True,
                        external_channel_id=kwargs["chat_id"],
                        external_message_id=kwargs["external_message_id"],
                        can_update=True,
                    ),
                )

        fake = FakeSlackAdapter()
        monkeypatch.setattr(notifier, "get_adapter", lambda platform: fake)
        monkeypatch.setattr(notifier, "supports_message_update", lambda platform: True)

        track_id = await _make_connector(
            factory,
            name="track",
            platform="slack",
            capabilities=["notifications"],
            allowed_chat_ids=["C-TRACK"],
            lanes=["track"],
        )
        await _make_connector(
            factory,
            name="respond",
            platform="slack",
            capabilities=["notifications"],
            allowed_chat_ids=["C-RESPOND"],
            lanes=["respond"],
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.created",
        )
        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.acknowledged",
        )

        track_sends = [item for item in fake.sent if item[0] == "track"]
        respond_sends = [item for item in fake.sent if item[0] == "respond"]
        assert len(track_sends) == 1
        assert track_sends[0][1]["status_update"] is True
        assert len(respond_sends) == 1
        assert "status_update" not in respond_sends[0][1]
        assert len(fake.updated) == 2
        track_update = next(item for item in fake.updated if item[0] == "track")
        assert track_update[1]["external_message_id"] == "msg-track"
        assert track_update[1]["status_update"] is True

        async with factory() as db:
            from backend.db.repos import IncidentTrackPostRepo

            post = await IncidentTrackPostRepo.get(
                db,
                TEST_ORG_ID,
                incident_id=incident_id,
                connector_id=track_id,
            )
        assert post is not None
        assert post.external_message_id == "msg-track"
        assert post.channel_ref == "C-TRACK"

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
        async with factory() as db:
            receipts = await IncidentNotificationReceiptRepo.list_for_incident(
                db, TEST_ORG_ID, incident_id
            )
        assert len(receipts) == 2
        assert {r.external_channel_id for r in receipts} == {"-100A", "-100B"}
        assert {r.lifecycle_event for r in receipts} == {"incident.created"}
        assert all(r.can_update is False for r in receipts)

    async def test_update_capable_adapter_edits_existing_incident_message(
        self, factory, monkeypatch
    ):
        class FakeUpdateAdapter:
            platform = "slack"

            def __init__(self):
                self.sent = []
                self.updated = []

            async def send_incident_update(
                self,
                connector,
                *,
                chat_id,
                text,
                incident=None,
                native_actions_ready=False,
            ):
                self.sent.append((chat_id, text))
                return DeliveryReceipt(
                    external_channel_id=chat_id,
                    external_message_id="msg-1",
                    external_thread_id="thread-1",
                    can_update=True,
                )

            async def update_incident_update(
                self,
                connector,
                *,
                chat_id,
                text,
                external_message_id,
                external_thread_id=None,
                incident=None,
                native_actions_ready=False,
            ):
                self.updated.append((chat_id, external_message_id, external_thread_id, text))
                return UpdateResult(
                    ok=True,
                    receipt=DeliveryReceipt(
                        external_channel_id=chat_id,
                        external_message_id=external_message_id,
                        external_thread_id=external_thread_id,
                        can_update=True,
                    ),
                )

        fake = FakeUpdateAdapter()
        monkeypatch.setattr(notifier, "get_adapter", lambda platform: fake)
        monkeypatch.setattr(notifier, "supports_message_update", lambda platform: True)

        await _make_connector(
            factory,
            platform="slack",
            capabilities=["notifications"],
            allowed_chat_ids=["C123"],
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.created",
        )
        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.resolved",
        )

        assert len(fake.sent) == 1
        assert len(fake.updated) == 1
        assert fake.updated[0][1] == "msg-1"
        async with factory() as db:
            receipts = await IncidentNotificationReceiptRepo.list_for_incident(
                db, TEST_ORG_ID, incident_id
            )
        assert [r.lifecycle_event for r in receipts] == [
            "incident.created",
            "incident.resolved",
        ]
        assert all(r.external_message_id == "msg-1" for r in receipts)
        assert all(r.can_update is True for r in receipts)

    async def test_update_fallback_posts_followup_message(self, factory, monkeypatch):
        """When the provider cannot edit (edit window closed, message gone),
        the notifier posts a fresh follow-up message instead of dropping the
        update — and records a second receipt."""

        class FallbackAdapter:
            platform = "slack"

            def __init__(self):
                self.sent = []

            async def send_incident_update(
                self, connector, *, chat_id, text, incident=None, native_actions_ready=False
            ):
                self.sent.append((chat_id, text))
                return DeliveryReceipt(
                    external_channel_id=chat_id,
                    external_message_id=f"msg-{len(self.sent)}",
                    can_update=True,
                )

            async def update_incident_update(
                self,
                connector,
                *,
                chat_id,
                text,
                external_message_id,
                external_thread_id=None,
                incident=None,
                native_actions_ready=False,
            ):
                return UpdateResult(
                    ok=False, error="edit_window_closed", fallback_to_followup=True
                )

        fake = FallbackAdapter()
        monkeypatch.setattr(notifier, "get_adapter", lambda platform: fake)
        monkeypatch.setattr(notifier, "supports_message_update", lambda platform: True)

        await _make_connector(
            factory,
            platform="slack",
            capabilities=["notifications"],
            allowed_chat_ids=["C123"],
        )
        incident_id = await _make_incident(factory)

        await notifier.deliver_incident_event(
            factory, org_id=TEST_ORG_ID, incident_id=incident_id, event_type="incident.created"
        )
        await notifier.deliver_incident_event(
            factory, org_id=TEST_ORG_ID, incident_id=incident_id, event_type="incident.resolved"
        )

        # Update failed -> a second message was posted (follow-up), not an edit.
        assert len(fake.sent) == 2
        async with factory() as db:
            receipts = await IncidentNotificationReceiptRepo.list_for_incident(
                db, TEST_ORG_ID, incident_id
            )
        assert [r.lifecycle_event for r in receipts] == [
            "incident.created",
            "incident.resolved",
        ]
        assert {r.external_message_id for r in receipts} == {"msg-1", "msg-2"}

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

    async def test_team_scoped_channel_receives_matching_service_incident(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(*, bot_token, chat_id, text, **kwargs):
            sent.append({"chat_id": chat_id, "text": text})
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        team_id, _, incident_id = await _make_service_incident(
            factory, team_name="Payments", slug="payments"
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-payments"],
            team_ids=[team_id],
        )

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.acknowledged",
        )

        assert [s["chat_id"] for s in sent] == ["-payments"]
        assert "Payments API" in sent[0]["text"]
        assert "Team: Payments" in sent[0]["text"]
        assert "acknowledged" in sent[0]["text"].lower()

    async def test_team_scoped_channel_skips_non_matching_service_incident(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(**kwargs):
            sent.append(kwargs)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        matching_team_id, _, _ = await _make_service_incident(
            factory, team_name="Search", slug="search"
        )
        _, _, incident_id = await _make_service_incident(
            factory, team_name="Billing", slug="billing"
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-search"],
            team_ids=[matching_team_id],
        )

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.created",
        )

        assert sent == []

    async def test_unowned_incident_only_reaches_workspace_channels(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(*, bot_token, chat_id, text, **kwargs):
            sent.append(chat_id)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        team_id, _, _ = await _make_service_incident(
            factory, team_name="Core", slug="core"
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-team"],
            team_ids=[team_id],
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-workspace"],
            name="workspace",
        )
        incident_id = await _make_incident(factory, title="Unowned outage")

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.created",
        )

        assert sent == ["-workspace"]

    async def test_prebuilt_escalation_text_respects_team_scope(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(*, bot_token, chat_id, text, **kwargs):
            sent.append(chat_id)
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        team_id, _, _ = await _make_service_incident(
            factory, team_name="Edge", slug="edge"
        )
        other_team_id, _, _ = await _make_service_incident(
            factory, team_name="Data", slug="data"
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-edge"],
            team_ids=[team_id],
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-data"],
            team_ids=[other_team_id],
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-workspace"],
            name="workspace",
        )

        await notifier.deliver_incident_text(
            factory,
            org_id=TEST_ORG_ID,
            text="*Incident escalated*",
            event_type="incident.escalated",
            team_id=team_id,
        )

        assert sent == ["-edge", "-workspace"]

    async def test_ai_session_started_respects_incident_team_scope(
        self, factory, monkeypatch
    ):
        sent = []

        async def fake_send(*, bot_token, chat_id, text, **kwargs):
            sent.append({"chat_id": chat_id, "text": text})
            return True, None

        monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)

        team_id, _, incident_id = await _make_service_incident(
            factory, team_name="Runtime", slug="runtime"
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-runtime"],
            team_ids=[team_id],
        )
        await _make_connector(
            factory,
            capabilities=["notifications"],
            allowed_chat_ids=["-workspace"],
            name="workspace",
        )
        async with factory() as db:
            actor = await UserRepo.create(
                db,
                username="sam",
                email="sam@example.com",
                password_hash="x",
                first_name="Sam",
                last_name="Ops",
            )
            session = await SessionRepo.create(
                db,
                TEST_ORG_ID,
                tier=1,
                incident_id=incident_id,
            )
            await db.commit()

        await notifier.deliver_session_chat_event(
            factory,
            org_id=TEST_ORG_ID,
            event_type="session.created",
            session_id=session.id,
            actor_user_id=actor.id,
            base_url="https://ops.example.com",
        )

        assert {s["chat_id"] for s in sent} == {"-runtime", "-workspace"}
        assert all("AI session started by Sam Ops" in s["text"] for s in sent)
        assert all("Team: `Runtime`" in s["text"] for s in sent)
        assert all("/dashboard/sessions/detail?id=" in s["text"] for s in sent)

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
