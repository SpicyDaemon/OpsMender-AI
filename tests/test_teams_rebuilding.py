"""M1-96: Microsoft Teams sends nothing while its delivery is being rebuilt.

A page to a saved Teams contact method (``teams_dm`` or ``teams_dm_graph``), or
a routing stage on a Teams Notification Channel, is recorded as skipped with
the reason ``teams_rebuilding`` and never as sent. The notifier skips Teams
connectors without calling the adapter, writing a ``delivery_failed`` audit row
or changing the connector status. Check configuration on a Teams channel shows
only the ``enabled`` check and the failing ``platform`` check, and the Track
lane is refused for Teams. No network is used: every channel and adapter that a
missing guard would reach is a recording stub.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes.bot_connectors import (
    _connector_config_checks,
    _status_from_checks,
    _validate_platform_lanes,
)
from backend.api.routes.incidents import _SKIP_REASONS
from backend.bots import notifier
from backend.bots.capabilities import TEAMS_REBUILDING
from backend.bots.connectors.teams import TeamsAdapter
from backend.bots.delivery import DeliveryReceipt
from backend.db.models import Base, BotActionAudit, BotConnector, Organization
from backend.db.repos import (
    BotConnectorRepo,
    IncidentPageRepo,
    IncidentRepo,
    UserNotificationPrefRepo,
    UserRepo,
)
from backend.paging import notification_escalation as ne
from backend.paging.dispatch import DeliveryAttempt, dispatch_page
from backend.paging.routing import TEAMS_SKIP_REASON

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000096")
TEAMS_CHAT = "19:ops@thread.v2"


@pytest.fixture
async def factory(tmp_path):
    db_path = tmp_path / "teams-rebuilding.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add(Organization(id=TEST_ORG_ID, name="Teams Org", slug="teams-org"))
        await db.commit()
    yield session_factory
    await engine.dispose()


class _StubChannel:
    """A paging channel that records every send and reports it as sent."""

    def __init__(self, key: str):
        self.key = key
        self.calls: list[str] = []

    async def send(self, *, recipient, subject, body, blocks=None, **kwargs):
        self.calls.append(recipient)
        return DeliveryAttempt(self.key, "sent")


@pytest.fixture
def adapter_calls(monkeypatch):
    """Record every Teams adapter send. Each returns what the real adapter
    returns, so a missing guard would also write a delivery_failed row."""
    calls: list[str] = []

    async def send_message(self, connector, *, chat_id, text):
        calls.append(f"send_message:{chat_id}")
        return False, TEAMS_REBUILDING

    async def send_incident_update(self, connector, *, chat_id, text, **kwargs):
        calls.append(f"send_incident_update:{chat_id}")
        return DeliveryReceipt(ok=False, error=TEAMS_REBUILDING)

    monkeypatch.setattr(TeamsAdapter, "send_message", send_message)
    monkeypatch.setattr(TeamsAdapter, "send_incident_update", send_incident_update)
    return calls


async def _user(factory, username="sam"):
    async with factory() as db:
        user = await UserRepo.create(
            db,
            username=username,
            email=f"{username}@test.com",
            password_hash="x",
            role="operator",
            primary_org_id=TEST_ORG_ID,
        )
        await db.commit()
    return user


async def _incident(factory, *, priority="P0"):
    async with factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Checkout 500s",
            description="Spike in 500s",
            severity="critical",
            priority=priority,
            response_mode="page",
        )
        await db.commit()
    return incident


async def _teams_connector(factory, **overrides):
    values = dict(
        name="teams-room",
        platform="teams",
        config={"default_chat_id": TEAMS_CHAT, "allowed_chat_ids": [TEAMS_CHAT]},
        credentials={
            "tenant_id": "tenant",
            "client_id": "client",
            "client_secret": "secret",
        },
        allowed_capabilities=["notifications"],
        status="healthy",
        is_enabled=True,
    )
    values.update(overrides)
    async with factory() as db:
        connector = await BotConnectorRepo.create(db, TEST_ORG_ID, **values)
        await db.commit()
    return connector


def test_the_skip_reason_is_teams_rebuilding_and_reads_as_a_sentence():
    assert TEAMS_SKIP_REASON == "teams_rebuilding"
    # The incident timeline says "Did not page Sam: <reason>."
    assert _SKIP_REASONS[TEAMS_SKIP_REASON] == "Teams delivery is being rebuilt"


# ---------------------------------------------------------------------------
# Paging: dispatch_page and the staged sender
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["teams_dm", "teams_dm_graph"])
async def test_a_page_to_a_teams_contact_method_is_skipped_and_never_sent(factory, key):
    user = await _user(factory)
    incident = await _incident(factory)
    async with factory() as db:
        await IncidentPageRepo.create(
            db, TEST_ORG_ID, incident_id=incident.id, user_id=user.id
        )
        await UserNotificationPrefRepo.upsert(
            db,
            TEST_ORG_ID,
            user.id,
            channels={key: TEAMS_CHAT},
            routing={"P0": [key]},
        )
        await db.commit()

    # The channel the factory would hand out if the Teams guard were missing.
    stub = _StubChannel(key)

    async with factory() as db:
        loaded_incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident.id)
        loaded_user = await UserRepo.get_by_id(db, user.id)
        pages = await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, incident.id)
        result = await dispatch_page(
            db,
            TEST_ORG_ID,
            incident=loaded_incident,
            user=loaded_user,
            page=pages[0],
            channel_factory=lambda wanted: stub if wanted == key else None,
        )
        await db.commit()

    assert result.suppressed is False
    assert [(a.channel, a.status, a.error) for a in result.attempts] == [
        (key, "skipped", "teams_rebuilding")
    ]
    assert stub.calls == []

    async with factory() as db:
        rows = await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, incident.id)
    teams_rows = [row for row in rows if row.channel == key]
    assert [(row.delivery_status, row.delivery_error) for row in teams_rows] == [
        ("skipped", "teams_rebuilding")
    ]
    assert not [row for row in rows if row.delivery_status == "sent"]


@pytest.mark.parametrize("key", ["teams_dm", "teams_dm_graph"])
async def test_a_staged_teams_key_is_skipped_by_the_sender(factory, key):
    user = await _user(factory)
    incident = await _incident(factory)
    async with factory() as db:
        await UserNotificationPrefRepo.upsert(
            db, TEST_ORG_ID, user.id, channels={key: TEAMS_CHAT}, routing={}
        )
        await db.commit()

    # Any key resolves to a recording channel, so a missing guard would send.
    stub = _StubChannel(key)
    sender = ne.build_notification_sender(lambda wanted: stub)
    async with factory() as db:
        outcome = await sender(
            db,
            TEST_ORG_ID,
            channel_id=key,
            incident=incident,
            user=user,
            subject="P0 page",
            body="Checkout 500s",
        )

    assert outcome == ("skipped", "teams_rebuilding")
    assert stub.calls == []


async def test_a_stage_on_a_teams_connector_is_skipped_by_the_sender(
    factory, adapter_calls
):
    user = await _user(factory)
    incident = await _incident(factory)
    connector = await _teams_connector(factory)

    sender = ne.build_notification_sender(lambda wanted: None)
    async with factory() as db:
        outcome = await sender(
            db,
            TEST_ORG_ID,
            channel_id=str(connector.id),
            incident=incident,
            user=user,
            subject="P0 page",
            body="Checkout 500s",
        )

    assert outcome == ("skipped", "teams_rebuilding")
    assert adapter_calls == []


# ---------------------------------------------------------------------------
# Notification Channels: the notifier
# ---------------------------------------------------------------------------


async def test_the_notifier_skips_a_teams_connector_on_an_incident_event(
    factory, adapter_calls, monkeypatch
):
    telegram_sent: list[str] = []

    async def telegram_send(*, bot_token, chat_id, text, **kwargs):
        telegram_sent.append(chat_id)
        return True, None

    monkeypatch.setattr("backend.bots.telegram.send_message", telegram_send)

    teams = await _teams_connector(factory, native_actions_enabled=True)
    async with factory() as db:
        await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="telegram-room",
            platform="telegram",
            config={"allowed_chat_ids": ["-100777"]},
            credentials={"bot_token": "BOT-TOKEN"},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        await db.commit()
    incident = await _incident(factory)

    await notifier.deliver_incident_event(
        factory,
        org_id=TEST_ORG_ID,
        incident_id=incident.id,
        event_type="incident.created",
    )

    # The event reached the other channel, so the Teams skip is not an empty run.
    assert telegram_sent == ["-100777"]
    assert adapter_calls == []
    async with factory() as db:
        audit = (
            (
                await db.execute(
                    select(BotActionAudit).where(
                        BotActionAudit.connector_id == teams.id
                    )
                )
            )
            .scalars()
            .all()
        )
        stored = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, teams.id)
    assert audit == []
    assert stored.status == "healthy"
    assert stored.last_error is None


# ---------------------------------------------------------------------------
# Settings: Check configuration and the Track lane
# ---------------------------------------------------------------------------


def _stored_teams_connector(**overrides) -> BotConnector:
    # As saved before M1-96: a destination, team scope and native actions on.
    values = dict(
        name="teams-room",
        platform="teams",
        config={
            "default_chat_id": TEAMS_CHAT,
            "team_scope": "teams",
            "team_ids": [],
        },
        credentials={
            "tenant_id": "tenant",
            "client_id": "client",
            "client_secret": "secret",
        },
        allowed_capabilities=["notifications", "approvals"],
        status="healthy",
        is_enabled=True,
        native_actions_enabled=True,
    )
    values.update(overrides)
    connector = BotConnector(**values)
    connector.callback_status = "configured"
    return connector


def test_check_configuration_on_teams_returns_only_enabled_and_platform():
    checks = _connector_config_checks(
        _stored_teams_connector(), requested_chat_id=TEAMS_CHAT
    )

    assert [(c.name, c.level) for c in checks] == [
        ("enabled", "pass"),
        ("platform", "fail"),
    ]
    assert checks[1].detail == TEAMS_REBUILDING
    assert _status_from_checks(checks) == (False, "error", TEAMS_REBUILDING)


def test_check_configuration_on_a_disabled_teams_channel_says_it_is_disabled():
    checks = _connector_config_checks(_stored_teams_connector(is_enabled=False))
    assert [(c.name, c.level) for c in checks] == [("enabled", "fail")]


@pytest.mark.parametrize("lanes", [["track"], ["respond", "track"]])
def test_the_track_lane_is_refused_for_teams(lanes):
    with pytest.raises(HTTPException) as exc:
        _validate_platform_lanes("teams", lanes)
    assert exc.value.status_code == 400
    assert "Track lane currently supports" in exc.value.detail
    assert "Teams" not in exc.value.detail


def test_teams_keeps_the_respond_lane_and_slack_keeps_track():
    assert _validate_platform_lanes("teams", ["respond"]) == ["respond"]
    assert _validate_platform_lanes("slack", ["respond", "track"]) == [
        "respond",
        "track",
    ]
