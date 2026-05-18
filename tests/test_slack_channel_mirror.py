"""Tests for the per-incident Slack channel mirror (Sprint 36 step 5)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Incident, Organization
from backend.db.repos import IncidentRepo, OrganizationRepo
from backend.paging.slack_channel_mirror import (
    channel_name_for_incident,
    mirror_incident_to_slack_channel,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


@pytest.fixture
async def db_session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/m.db", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Org", slug="org"))
        await session.commit()
    async with factory() as session:
        yield session
    await engine.dispose()


def _record_transport(captured: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/conversations.create"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "channel": {"id": "C123", "name": "inc-deadbeef"},
                },
            )
        if request.url.path.endswith("/chat.postMessage"):
            return httpx.Response(200, json={"ok": True, "ts": "1"})
        return httpx.Response(404, json={"ok": False, "error": "no_route"})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport)

    return factory


class TestChannelName:
    def test_uses_first_eight_hex_of_uuid(self):
        inc = Incident(
            id=uuid.UUID("deadbeef-1234-5678-9abc-def012345678"),
            org_id=TEST_ORG_ID,
            title="t",
            description="d",
        )
        assert channel_name_for_incident(inc) == "inc-deadbeef"

    def test_strips_unsafe_chars(self):
        inc = Incident(
            id=uuid.UUID("12345678-1234-5678-9abc-def012345678"),
            org_id=TEST_ORG_ID,
            title="t",
            description="d",
        )
        # `INC#` is lowercased to `inc#`, then `#` collapses to a single
        # hyphen, yielding `inc-12345678`. Slack channel names only allow
        # `[a-z0-9_-]`.
        assert channel_name_for_incident(inc, prefix="INC#") == "inc-12345678"


class TestMirrorIncidentToSlackChannel:
    async def test_noop_when_feature_disabled(self, db_session):
        incident = await IncidentRepo.create(
            db_session,
            TEST_ORG_ID,
            title="boom",
            description="d",
            priority="P1",
            response_mode="page",
        )
        await db_session.commit()
        captured: list[httpx.Request] = []
        result = await mirror_incident_to_slack_channel(
            db_session,
            TEST_ORG_ID,
            incident=incident,
            bot_token="xoxb-x",
            http_client_factory=_record_transport(captured),
        )
        assert result is None
        assert captured == []
        assert incident.slack_channel_id is None

    async def test_noop_when_bot_token_missing(self, db_session, monkeypatch):
        await OrganizationRepo.update(
            db_session,
            TEST_ORG_ID,
            slack_incident_channels_enabled=True,
        )
        await db_session.commit()
        incident = await IncidentRepo.create(
            db_session,
            TEST_ORG_ID,
            title="boom",
            description="d",
            priority="P1",
            response_mode="page",
        )
        await db_session.commit()
        monkeypatch.delenv("OPSMENDER_SLACK_BOT_TOKEN", raising=False)
        captured: list[httpx.Request] = []
        result = await mirror_incident_to_slack_channel(
            db_session,
            TEST_ORG_ID,
            incident=incident,
            http_client_factory=_record_transport(captured),
        )
        assert result is None
        assert captured == []

    async def test_creates_channel_and_posts_card(self, db_session):
        await OrganizationRepo.update(
            db_session,
            TEST_ORG_ID,
            slack_incident_channels_enabled=True,
        )
        await db_session.commit()
        incident = await IncidentRepo.create(
            db_session,
            TEST_ORG_ID,
            title="db on fire",
            description="latency p99 spike",
            priority="P0",
            response_mode="page",
        )
        await db_session.commit()
        captured: list[httpx.Request] = []
        result = await mirror_incident_to_slack_channel(
            db_session,
            TEST_ORG_ID,
            incident=incident,
            bot_token="xoxb-test",
            http_client_factory=_record_transport(captured),
            base_url="https://ops.example.com",
        )
        await db_session.commit()
        assert result == "C123"
        # Two requests: conversations.create then chat.postMessage.
        assert len(captured) == 2
        assert captured[0].url.path.endswith("/conversations.create")
        create_body = json.loads(captured[0].content.decode("utf-8"))
        assert create_body["name"].startswith("inc-")
        assert create_body["is_private"] is False
        assert captured[0].headers["Authorization"] == "Bearer xoxb-test"

        post_body = json.loads(captured[1].content.decode("utf-8"))
        assert post_body["channel"] == "C123"
        assert "db on fire" in post_body["text"]
        assert post_body["blocks"]  # Block Kit payload included.

        reloaded = await IncidentRepo.get_by_id(
            db_session, TEST_ORG_ID, incident.id
        )
        assert reloaded.slack_channel_id == "C123"
        assert reloaded.slack_channel_name.startswith("inc-")

    async def test_idempotent_when_already_mirrored(self, db_session):
        await OrganizationRepo.update(
            db_session,
            TEST_ORG_ID,
            slack_incident_channels_enabled=True,
        )
        await db_session.commit()
        incident = await IncidentRepo.create(
            db_session,
            TEST_ORG_ID,
            title="x",
            description="d",
            priority="P1",
            response_mode="page",
        )
        incident.slack_channel_id = "C_EXISTING"
        await db_session.commit()
        captured: list[httpx.Request] = []
        result = await mirror_incident_to_slack_channel(
            db_session,
            TEST_ORG_ID,
            incident=incident,
            bot_token="xoxb-x",
            http_client_factory=_record_transport(captured),
        )
        assert result == "C_EXISTING"
        assert captured == []

    async def test_slack_error_returns_none(self, db_session):
        await OrganizationRepo.update(
            db_session,
            TEST_ORG_ID,
            slack_incident_channels_enabled=True,
        )
        await db_session.commit()
        incident = await IncidentRepo.create(
            db_session,
            TEST_ORG_ID,
            title="x",
            description="d",
            priority="P1",
            response_mode="page",
        )
        await db_session.commit()

        def fail_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"ok": False, "error": "name_taken"}
            )

        transport = httpx.MockTransport(fail_handler)

        def factory():
            return httpx.AsyncClient(transport=transport)

        result = await mirror_incident_to_slack_channel(
            db_session,
            TEST_ORG_ID,
            incident=incident,
            bot_token="xoxb-x",
            http_client_factory=factory,
        )
        assert result is None
        assert incident.slack_channel_id is None
