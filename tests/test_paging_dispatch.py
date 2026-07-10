"""Tests for the Sprint 35 paging dispatcher + channel implementations.

Covers:
* Maintenance-window suppression (page suppressed, escalate_immediate not).
* Quiet-hours block (with ``min_priority_to_break`` override).
* Channel resolution from ``UserNotificationPref.routing``.
* Dedup within ``organizations.notification_dedup_window_minutes``.
* Per-channel transport: Slack DM, Teams DM, SMS via ``httpx.MockTransport``;
  Email via an injected ``smtplib.SMTP``-shaped factory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Organization
from backend.db.repos import (
    IncidentPageRepo,
    IncidentRepo,
    MaintenanceWindowRepo,
    OrganizationRepo,
    ServiceRepo,
    TeamRepo,
    UserNotificationPrefRepo,
    UserRepo,
)
from backend.paging.channels import (
    EmailChannel,
    SlackDMChannel,
    SMSChannel,
    TeamsDMChannel,
)
from backend.paging.dispatch import (
    DeliveryAttempt,
    dispatch_page,
    evaluate_maintenance_window,
    quiet_hours_block,
    resolve_channels,
)

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")


@pytest.fixture
async def session_factory(tmp_path):
    db_path = tmp_path / "dispatch-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Organization(id=TEST_ORG_ID, name="Dispatch Org", slug="dispatch-org")
        )
        await session.commit()
    yield factory
    await engine.dispose()


async def _make_user(factory, *, username="alice"):
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


async def _make_incident(
    factory, *, priority="P1", response_mode="page", service_id=None, title="boom"
):
    async with factory() as db:
        inc = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title=title,
            description="something broke",
            severity="high",
            priority=priority,
            response_mode=response_mode,
            service_id=service_id,
        )
        await db.commit()
    return inc


async def _record_page(factory, *, incident_id, user_id):
    async with factory() as db:
        page = await IncidentPageRepo.create(
            db,
            TEST_ORG_ID,
            incident_id=incident_id,
            user_id=user_id,
        )
        await db.commit()
    return page


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestResolveChannels:
    def test_picks_routing_for_priority(self):
        routing = {"P0": ["slack_dm", "sms"], "P1": ["email"]}
        assert resolve_channels(routing, priority="P0") == ["slack_dm", "sms"]
        assert resolve_channels(routing, priority="P1") == ["email"]

    def test_filters_unknown_channels(self):
        routing = {"P0": ["slack_dm", "carrier_pigeon"]}
        assert resolve_channels(routing, priority="P0") == ["slack_dm"]

    def test_falls_back_to_default_when_no_routing(self):
        assert resolve_channels(None, priority="P0") == ["email"]
        assert resolve_channels({}, priority="P2") == ["email"]
        assert resolve_channels({"P0": ["sms"]}, priority="P3") == ["email"]


class TestQuietHours:
    def test_no_window_means_no_block(self):
        assert (
            quiet_hours_block(None, priority="P3", at=datetime.now(timezone.utc))
            is False
        )

    def test_block_inside_window(self):
        # 22:00–06:00 UTC, P2 incident at 23:00 UTC — blocked.
        at = datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc)
        quiet = {
            "weekday_start": "22:00",
            "weekday_end": "06:00",
            "min_priority_to_break": "P0",
            "time_zone": "UTC",
        }
        assert quiet_hours_block(quiet, priority="P2", at=at) is True

    def test_p0_breaks_through(self):
        at = datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc)
        quiet = {
            "weekday_start": "22:00",
            "weekday_end": "06:00",
            "min_priority_to_break": "P0",
        }
        assert quiet_hours_block(quiet, priority="P0", at=at) is False

    def test_outside_window(self):
        at = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
        quiet = {
            "weekday_start": "22:00",
            "weekday_end": "06:00",
            "min_priority_to_break": "P0",
        }
        assert quiet_hours_block(quiet, priority="P2", at=at) is False

    def test_p0_always_bypasses_without_min_priority(self):
        # v1 My Routing guarantee: P0 pages through even when no
        # min_priority_to_break is stored.
        at = datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc)
        quiet = {"weekday_start": "22:00", "weekday_end": "06:00"}
        assert quiet_hours_block(quiet, priority="P0", at=at) is False
        assert quiet_hours_block(quiet, priority="P2", at=at) is True

    def test_days_of_week_restriction(self):
        # 2026-05-15 is a Friday (weekday()==4). Window active only Mon-Thu
        # (0-3) → Friday is NOT a quiet day, so P2 is not blocked.
        at = datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc)
        quiet = {
            "weekday_start": "22:00",
            "weekday_end": "06:00",
            "days": [0, 1, 2, 3],
        }
        assert quiet_hours_block(quiet, priority="P2", at=at) is False
        # Include Friday (4) → now blocked.
        quiet_with_fri = {**quiet, "days": [0, 1, 2, 3, 4]}
        assert quiet_hours_block(quiet_with_fri, priority="P2", at=at) is True


# ---------------------------------------------------------------------------
# Maintenance windows
# ---------------------------------------------------------------------------


class TestMaintenanceWindow:
    async def test_global_window_matches(self, session_factory):
        inc = await _make_incident(session_factory)
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            await MaintenanceWindowRepo.create(
                db,
                TEST_ORG_ID,
                name="Global maint",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=30),
                scope_type="global",
            )
            await db.commit()
        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            mw = await evaluate_maintenance_window(
                db, TEST_ORG_ID, incident=inc_loaded, at=now
            )
            assert mw is not None
            assert mw.name == "Global maint"

    async def test_scoped_window_must_match_service(self, session_factory):
        async with session_factory() as db:
            team = await TeamRepo.create(db, TEST_ORG_ID, name="t", slug="t")
            svc = await ServiceRepo.create(
                db, TEST_ORG_ID, name="svc-a", slug="svc-a", team_id=team.id
            )
            other = await ServiceRepo.create(
                db, TEST_ORG_ID, name="svc-b", slug="svc-b", team_id=team.id
            )
            await db.commit()
            svc_id = svc.id
            other_id = other.id

        inc = await _make_incident(session_factory, service_id=svc_id)
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            await MaintenanceWindowRepo.create(
                db,
                TEST_ORG_ID,
                name="Other-service maint",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=30),
                scope_type="service",
                scope_id=other_id,
            )
            await db.commit()
        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            mw = await evaluate_maintenance_window(
                db, TEST_ORG_ID, incident=inc_loaded, at=now
            )
            assert mw is None


# ---------------------------------------------------------------------------
# Dispatcher end-to-end (with stub channels)
# ---------------------------------------------------------------------------


class _StubChannel:
    def __init__(self, key: str, status: str = "sent", error: str | None = None):
        self.key = key
        self._status = status
        self._error = error
        self.calls: list[tuple[str, str, str]] = []

    async def send(self, *, recipient, subject, body, blocks=None):
        self.calls.append((recipient, subject, body))
        return DeliveryAttempt(self.key, self._status, self._error)


def _factory_for(stubs: dict[str, _StubChannel]):
    def _factory(key: str):
        return stubs.get(key)

    return _factory


class TestDispatchPipeline:
    async def test_routes_to_user_preferred_channels(self, session_factory):
        user = await _make_user(session_factory, username="alice")
        inc = await _make_incident(session_factory, priority="P0")
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)

        async with session_factory() as db:
            await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={
                    "slack_dm": "U12345",
                    "email": "alice@test.com",
                    "sms": "+15551234",
                },
                routing={"P0": ["slack_dm", "sms"], "P1": ["email"]},
            )
            await db.commit()

        slack = _StubChannel("slack_dm")
        sms = _StubChannel("sms")
        factory = _factory_for({"slack_dm": slack, "sms": sms})

        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=factory,
            )
            await db.commit()

        assert result.suppressed is False
        statuses = {a.channel: a.status for a in result.attempts}
        assert statuses == {"slack_dm": "sent", "sms": "sent"}
        assert slack.calls and slack.calls[0][0] == "U12345"
        assert sms.calls and sms.calls[0][0] == "+15551234"

        async with session_factory() as db:
            rows = await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            channels_seen = {r.channel for r in rows}
            assert "slack_dm" in channels_seen and "sms" in channels_seen
            # Original "recorded" row preserved as audit anchor.
            assert "recorded" in channels_seen

    async def test_staged_routing_delegates_to_escalation_engine(self, session_factory):
        """New stage shape routes via the notification-escalation engine:
        only stage 0 fires immediately and a NotificationEscalation row is
        created with the next stage scheduled."""
        from backend.db.repos import NotificationEscalationRepo

        user = await _make_user(session_factory, username="carol")
        inc = await _make_incident(session_factory, priority="P0")
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)
        async with session_factory() as db:
            await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={"email": "carol@test.com"},
                routing={
                    "P0": [
                        {"channel_id": "email", "delay_seconds": 300},
                        {"channel_id": "sms", "delay_seconds": 300},
                    ]
                },
            )
            await db.commit()

        email = _StubChannel("email")
        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=_factory_for({"email": email}),
            )
            await db.commit()

        assert result.staged is True
        async with session_factory() as db:
            state = await NotificationEscalationRepo.get(
                db, TEST_ORG_ID, incident_id=inc.id, user_id=user.id
            )
            assert state is not None
            assert state.current_stage == 0
            assert state.status == "running"
            assert state.next_stage_due_at is not None
        # Stage 0 (email) delivered immediately to carol's saved address.
        assert email.calls and email.calls[0][0] == "carol@test.com"

    async def test_maintenance_window_suppresses_page(self, session_factory):
        user = await _make_user(session_factory, username="bob")
        inc = await _make_incident(session_factory, priority="P1", response_mode="page")
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            await MaintenanceWindowRepo.create(
                db,
                TEST_ORG_ID,
                name="deploy window",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=30),
                scope_type="global",
            )
            await db.commit()

        slack = _StubChannel("slack_dm")

        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=_factory_for({"slack_dm": slack}),
                at=now,
            )
            await db.commit()

        assert result.suppressed is True
        assert result.suppression_reason == "maintenance_window"
        assert result.suppressed_by_window_id is not None
        assert slack.calls == []

        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            assert inc_loaded.suppressed_by_maintenance_window_id is not None

    async def test_escalate_immediate_bypasses_maintenance(self, session_factory):
        user = await _make_user(session_factory, username="carol")
        inc = await _make_incident(
            session_factory, priority="P0", response_mode="escalate_immediate"
        )
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)
        now = datetime.now(timezone.utc)
        async with session_factory() as db:
            await MaintenanceWindowRepo.create(
                db,
                TEST_ORG_ID,
                name="freeze",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=30),
                scope_type="global",
            )
            await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={"slack_dm": "Ucarol"},
                routing={"P0": ["slack_dm"]},
            )
            await db.commit()

        slack = _StubChannel("slack_dm")
        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=_factory_for({"slack_dm": slack}),
                at=now,
            )
            await db.commit()

        assert result.suppressed is False
        assert [a.status for a in result.attempts] == ["sent"]
        assert slack.calls

    async def test_dedup_skips_second_delivery_in_window(self, session_factory):
        user = await _make_user(session_factory, username="dave")
        inc = await _make_incident(session_factory, priority="P1")
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)
        async with session_factory() as db:
            await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={"slack_dm": "U99"},
                routing={"P1": ["slack_dm"]},
            )
            await db.commit()

        slack = _StubChannel("slack_dm")
        factory = _factory_for({"slack_dm": slack})

        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            first = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=factory,
            )
            await db.commit()
        assert [a.status for a in first.attempts] == ["sent"]
        assert len(slack.calls) == 1

        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            second = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=factory,
            )
            await db.commit()
        assert [a.status for a in second.attempts] == ["skipped"]
        assert second.attempts[0].error == "dedup"
        assert len(slack.calls) == 1  # still one — no second send

    async def test_dedup_window_can_be_customized(self, session_factory):
        # Set org dedup window to 1 minute; backdate the first send to 2 minutes ago.
        user = await _make_user(session_factory, username="erin")
        inc = await _make_incident(session_factory, priority="P1")
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)
        async with session_factory() as db:
            await OrganizationRepo.update(
                db, TEST_ORG_ID, notification_dedup_window_minutes=1
            )
            await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={"email": "erin@test.com"},
                routing={"P1": ["email"]},
            )
            # Manually record a "sent" attempt from 2 min ago.
            past = datetime.now(timezone.utc) - timedelta(minutes=2)
            attempt = await IncidentPageRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=inc.id,
                user_id=user.id,
                channel="email",
                delivery_status="sent",
            )
            attempt.sent_at = past
            await db.commit()

        email = _StubChannel("email")
        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=_factory_for({"email": email}),
            )
            await db.commit()
        assert [a.status for a in result.attempts] == ["sent"]

    async def test_quiet_hours_blocks_below_threshold(self, session_factory):
        user = await _make_user(session_factory, username="frank")
        inc = await _make_incident(session_factory, priority="P3")
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)
        at = datetime(2026, 5, 15, 23, 30, tzinfo=timezone.utc)
        async with session_factory() as db:
            await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={"email": "frank@test.com"},
                routing={"P3": ["email"]},
                quiet_hours={
                    "weekday_start": "22:00",
                    "weekday_end": "06:00",
                    "min_priority_to_break": "P1",
                    "time_zone": "UTC",
                },
                quiet_hours_provided=True,
            )
            await db.commit()

        email = _StubChannel("email")
        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=_factory_for({"email": email}),
                at=at,
            )
            await db.commit()

        assert result.suppressed is True
        assert result.suppression_reason == "quiet_hours"
        assert email.calls == []

    async def test_unconfigured_channel_records_skipped(self, session_factory):
        user = await _make_user(session_factory, username="gina")
        inc = await _make_incident(session_factory, priority="P1")
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)
        async with session_factory() as db:
            await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={"slack_dm": "Uxx"},
                routing={"P1": ["slack_dm"]},
            )
            await db.commit()

        # factory returns None for slack_dm — org hasn't wired up Slack.
        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=lambda key: None,
            )
            await db.commit()
        assert [a.status for a in result.attempts] == ["skipped"]
        assert result.attempts[0].error == "channel_unconfigured"


# ---------------------------------------------------------------------------
# Channel transports
# ---------------------------------------------------------------------------


def _mock_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    return factory


class TestSlackDMChannel:
    async def test_sent_on_ok_response(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"ok": True})

        channel = SlackDMChannel(
            bot_token="xoxb-test", http_client_factory=_mock_factory(handler)
        )
        attempt = await channel.send(recipient="U1", subject="boom", body="oh no")
        assert attempt.status == "sent"
        assert captured["url"] == "https://slack.com/api/chat.postMessage"
        assert captured["auth"] == "Bearer xoxb-test"

    async def test_failed_on_slack_error(self):
        def handler(request):
            return httpx.Response(200, json={"ok": False, "error": "not_in_channel"})

        channel = SlackDMChannel(
            bot_token="xoxb-test", http_client_factory=_mock_factory(handler)
        )
        attempt = await channel.send(recipient="U1", subject="x", body="y")
        assert attempt.status == "failed"
        assert "not_in_channel" in (attempt.error or "")

    async def test_failed_on_http_500(self):
        def handler(request):
            return httpx.Response(500, json={"ok": False})

        channel = SlackDMChannel(
            bot_token="xoxb-test", http_client_factory=_mock_factory(handler)
        )
        attempt = await channel.send(recipient="U1", subject="x", body="y")
        assert attempt.status == "failed"
        assert "500" in (attempt.error or "")


class TestTeamsDMChannel:
    async def test_sent_on_2xx(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, text="ok")

        channel = TeamsDMChannel(
            webhook_url="https://outlook.office.com/webhook/abc",
            http_client_factory=_mock_factory(handler),
        )
        attempt = await channel.send(recipient="alice@test", subject="s", body="b")
        assert attempt.status == "sent"
        assert captured["url"].startswith("https://outlook.office.com/")

    async def test_failed_on_4xx(self):
        def handler(request):
            return httpx.Response(400, text="bad webhook")

        channel = TeamsDMChannel(
            webhook_url="https://example.com/hook",
            http_client_factory=_mock_factory(handler),
        )
        attempt = await channel.send(recipient="x", subject="s", body="b")
        assert attempt.status == "failed"


class TestSMSChannel:
    async def test_sent_on_201(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(201, json={"sid": "SM123"})

        channel = SMSChannel(
            account_sid="ACtest",
            auth_token="tok",
            from_number="+15550000000",
            http_client_factory=_mock_factory(handler),
        )
        attempt = await channel.send(recipient="+15551234", subject="s", body="b")
        assert attempt.status == "sent"
        assert "ACtest" in captured["url"]

    async def test_failed_on_400(self):
        def handler(request):
            return httpx.Response(400, json={"message": "Invalid 'To' Phone Number"})

        channel = SMSChannel(
            account_sid="ACtest",
            auth_token="tok",
            from_number="+1",
            http_client_factory=_mock_factory(handler),
        )
        attempt = await channel.send(recipient="bogus", subject="s", body="b")
        assert attempt.status == "failed"
        assert "Invalid" in (attempt.error or "")


class _FakeSMTP:
    instances: list["_FakeSMTP"] = []

    def __init__(self, raise_on_send=False):
        self.starttls_called = False
        self.login_called: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        self.quit_called = False
        self._raise_on_send = raise_on_send
        _FakeSMTP.instances.append(self)

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_called = (user, password)

    def send_message(self, msg):
        if self._raise_on_send:
            raise RuntimeError("smtp boom")
        self.sent.append(msg)

    def quit(self):
        self.quit_called = True


class TestEmailChannel:
    async def test_sent_via_smtp(self):
        _FakeSMTP.instances.clear()
        channel = EmailChannel(
            smtp_host="smtp.test",
            smtp_user="u",
            smtp_password="p",
            from_addr="ops@test",
            smtp_factory=lambda: _FakeSMTP(),
        )
        attempt = await channel.send(
            recipient="alice@test.com", subject="boom", body="oh no"
        )
        assert attempt.status == "sent"
        assert len(_FakeSMTP.instances) == 1
        inst = _FakeSMTP.instances[0]
        assert inst.starttls_called is True
        assert inst.login_called == ("u", "p")
        assert inst.quit_called is True
        assert len(inst.sent) == 1
        assert inst.sent[0]["To"] == "alice@test.com"
        assert inst.sent[0]["Subject"] == "boom"

    async def test_failed_on_smtp_exception(self):
        _FakeSMTP.instances.clear()
        channel = EmailChannel(
            smtp_host="smtp.test",
            smtp_factory=lambda: _FakeSMTP(raise_on_send=True),
        )
        attempt = await channel.send(
            recipient="alice@test.com", subject="boom", body="oh no"
        )
        assert attempt.status == "failed"
        assert "boom" in (attempt.error or "")


# ---------------------------------------------------------------------------
# End-to-end: dispatch through a real SlackDMChannel + MockTransport
# ---------------------------------------------------------------------------


class TestDispatchEndToEnd:
    async def test_slack_dm_via_mock_transport(self, session_factory):
        """dispatch_page → real SlackDMChannel → MockTransport captures payload."""
        user = await _make_user(session_factory, username="end-to-end")
        inc = await _make_incident(
            session_factory, priority="P0", title="Database is down"
        )
        await _record_page(session_factory, incident_id=inc.id, user_id=user.id)

        async with session_factory() as db:
            await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={"slack_dm": "U_END2END"},
                routing={"P0": ["slack_dm"]},
            )
            await db.commit()

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = request.read().decode("utf-8")
            return httpx.Response(200, json={"ok": True, "ts": "1234.5678"})

        slack_channel = SlackDMChannel(
            bot_token="xoxb-end2end",
            http_client_factory=_mock_factory(handler),
        )

        def channel_factory(key: str):
            return slack_channel if key == "slack_dm" else None

        async with session_factory() as db:
            inc_loaded = await IncidentRepo.get_by_id(db, TEST_ORG_ID, inc.id)
            user_loaded = await UserRepo.get_by_id(db, user.id)
            page_loaded = (
                await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            )[0]
            result = await dispatch_page(
                db,
                TEST_ORG_ID,
                incident=inc_loaded,
                user=user_loaded,
                page=page_loaded,
                channel_factory=channel_factory,
            )
            await db.commit()

        assert result.suppressed is False
        assert len(result.attempts) == 1
        assert result.attempts[0].channel == "slack_dm"
        assert result.attempts[0].status == "sent"

        # MockTransport captured the real HTTP call.
        assert captured["url"] == "https://slack.com/api/chat.postMessage"
        assert captured["auth"] == "Bearer xoxb-end2end"
        assert "U_END2END" in captured["body"]
        assert "Database is down" in captured["body"]

        # And the dispatcher recorded a `slack_dm` row alongside the audit anchor.
        async with session_factory() as db:
            rows = await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, inc.id)
            statuses = {(r.channel, r.delivery_status) for r in rows}
            assert ("slack_dm", "sent") in statuses
