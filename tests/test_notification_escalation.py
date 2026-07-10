"""Staged notification escalation — parsing, engine, ack/resolve stop."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Organization
from backend.db.repos import (
    IncidentRepo,
    NotificationEscalationRepo,
    UserRepo,
)
from backend.paging import notification_escalation as ne
from backend.paging.routing import parse_stages, routing_is_staged, stage_channel_ids

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def session_factory(tmp_path):
    db_path = tmp_path / "ne-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="NE Org", slug="ne-org"))
        await session.commit()
    yield factory
    await engine.dispose()


async def _user(factory, username="op1"):
    async with factory() as db:
        u = await UserRepo.create(
            db,
            username=username,
            email=f"{username}@test.com",
            password_hash="x",
            role="operator",
            primary_org_id=TEST_ORG_ID,
        )
        await db.commit()
    return u


async def _incident(factory, *, priority="P0", title="boom"):
    async with factory() as db:
        inc = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title=title,
            description="x",
            severity="critical",
            priority=priority,
            response_mode="page",
        )
        await db.commit()
    return inc


def _recording_sender(log: list[str]):
    async def sender(db, org_id, *, channel_id, incident, user, subject, body):
        log.append(channel_id)
        return ("sent", None)

    return sender


# ---------------------------------------------------------------------------
# Stage parsing / backward compatibility
# ---------------------------------------------------------------------------


class TestParseStages:
    def test_new_dict_shape(self):
        stages = parse_stages(
            [
                {"channel_id": "teams-prod", "delay_seconds": 300},
                {"channel_id": "sms-primary", "delay_seconds": 120},
            ]
        )
        assert [(s.channel_id, s.delay_seconds) for s in stages] == [
            ("teams-prod", 300),
            ("sms-primary", 120),
        ]

    def test_legacy_list_becomes_stage_one(self):
        # Backward compat: existing single/legacy routing becomes stages with
        # no inter-stage delay (preserves immediate fan-out semantics).
        stages = parse_stages(["slack_dm", "email"])
        assert [(s.channel_id, s.delay_seconds) for s in stages] == [
            ("slack_dm", 0),
            ("email", 0),
        ]

    def test_caps_at_three_stages(self):
        stages = parse_stages([{"channel_id": f"c{i}"} for i in range(5)])
        assert len(stages) == 3

    def test_skips_blank_and_bad_entries(self):
        stages = parse_stages([{"channel_id": ""}, {"delay_seconds": 10}, "  ", "ok"])
        assert stage_channel_ids([{"channel_id": ""}, "ok"]) == ["ok"]
        assert [s.channel_id for s in stages] == ["ok"]

    def test_routing_is_staged_detects_shape(self):
        assert routing_is_staged([{"channel_id": "x"}]) is True
        assert routing_is_staged(["email"]) is False
        assert routing_is_staged(None) is False


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class TestEngine:
    async def test_start_fires_stage_zero_and_schedules_next(self, session_factory):
        user = await _user(session_factory)
        inc = await _incident(session_factory)
        log: list[str] = []
        now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        async with session_factory() as db:
            await ne.start_escalation(
                db,
                TEST_ORG_ID,
                incident=inc,
                user=user,
                stages=parse_stages(
                    [
                        {"channel_id": "teams", "delay_seconds": 300},
                        {"channel_id": "sms", "delay_seconds": 300},
                        {"channel_id": "telegram", "delay_seconds": 300},
                    ]
                ),
                sender=_recording_sender(log),
                at=now,
            )
            await db.commit()
            state = await NotificationEscalationRepo.get(
                db, TEST_ORG_ID, incident_id=inc.id, user_id=user.id
            )
            assert log == ["teams"]  # only stage 0 fired immediately
            assert state.current_stage == 0
            assert state.status == "running"
            assert state.next_stage_due_at is not None

    async def test_tick_advances_through_stages_with_delay(self, session_factory):
        user = await _user(session_factory)
        inc = await _incident(session_factory)
        log: list[str] = []
        sender = _recording_sender(log)
        t0 = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        stages = parse_stages(
            [
                {"channel_id": "teams", "delay_seconds": 300},
                {"channel_id": "sms", "delay_seconds": 300},
                {"channel_id": "telegram", "delay_seconds": 300},
            ]
        )
        async with session_factory() as db:
            await ne.start_escalation(
                db,
                TEST_ORG_ID,
                incident=inc,
                user=user,
                stages=stages,
                sender=sender,
                at=t0,
            )
            await db.commit()

        # Not due yet → no advance.
        async with session_factory() as db:
            fired = await ne.tick_all_due(
                db, sender=sender, at=t0 + timedelta(seconds=60)
            )
            await db.commit()
            assert fired == 0
            assert log == ["teams"]

        # After 300s → stage 1.
        async with session_factory() as db:
            fired = await ne.tick_all_due(
                db, sender=sender, at=t0 + timedelta(seconds=300)
            )
            await db.commit()
            assert fired == 1
            assert log == ["teams", "sms"]

        # After another 300s → stage 2, then exhausted.
        async with session_factory() as db:
            await ne.tick_all_due(db, sender=sender, at=t0 + timedelta(seconds=600))
            await db.commit()
            state = await NotificationEscalationRepo.get(
                db, TEST_ORG_ID, incident_id=inc.id, user_id=user.id
            )
            assert log == ["teams", "sms", "telegram"]
            assert state.status == "exhausted"
            assert state.next_stage_due_at is None

    async def test_single_stage_exhausts_immediately(self, session_factory):
        user = await _user(session_factory)
        inc = await _incident(session_factory)
        log: list[str] = []
        async with session_factory() as db:
            await ne.start_escalation(
                db,
                TEST_ORG_ID,
                incident=inc,
                user=user,
                stages=parse_stages([{"channel_id": "email", "delay_seconds": 300}]),
                sender=_recording_sender(log),
                at=datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc),
            )
            await db.commit()
            state = await NotificationEscalationRepo.get(
                db, TEST_ORG_ID, incident_id=inc.id, user_id=user.id
            )
            assert state.status == "exhausted"
            assert state.next_stage_due_at is None

    async def test_ack_stops_remaining_stages(self, session_factory):
        user = await _user(session_factory)
        inc = await _incident(session_factory)
        log: list[str] = []
        sender = _recording_sender(log)
        t0 = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        stages = parse_stages(
            [
                {"channel_id": "teams", "delay_seconds": 300},
                {"channel_id": "sms", "delay_seconds": 300},
            ]
        )
        async with session_factory() as db:
            await ne.start_escalation(
                db,
                TEST_ORG_ID,
                incident=inc,
                user=user,
                stages=stages,
                sender=sender,
                at=t0,
            )
            await db.commit()
        # Acknowledge.
        async with session_factory() as db:
            stopped = await ne.stop_escalation(
                db,
                TEST_ORG_ID,
                incident_id=inc.id,
                status="acked",
                at=t0 + timedelta(seconds=10),
            )
            await db.commit()
            assert stopped == 1
        # Tick after delay → nothing fires.
        async with session_factory() as db:
            fired = await ne.tick_all_due(
                db, sender=sender, at=t0 + timedelta(seconds=300)
            )
            await db.commit()
            assert fired == 0
            assert log == ["teams"]
            state = await NotificationEscalationRepo.get(
                db, TEST_ORG_ID, incident_id=inc.id, user_id=user.id
            )
            assert state.status == "acked"

    async def test_resolution_stops_via_update_status(self, session_factory):
        user = await _user(session_factory)
        inc = await _incident(session_factory)
        log: list[str] = []
        sender = _recording_sender(log)
        t0 = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        stages = parse_stages(
            [
                {"channel_id": "teams", "delay_seconds": 300},
                {"channel_id": "sms", "delay_seconds": 300},
            ]
        )
        async with session_factory() as db:
            await ne.start_escalation(
                db,
                TEST_ORG_ID,
                incident=inc,
                user=user,
                stages=stages,
                sender=sender,
                at=t0,
            )
            await db.commit()
        # Resolving the incident must stop escalation (update_status chokepoint).
        async with session_factory() as db:
            await IncidentRepo.update_status(db, TEST_ORG_ID, inc.id, "resolved")
            await db.commit()
        async with session_factory() as db:
            fired = await ne.tick_all_due(
                db, sender=sender, at=t0 + timedelta(seconds=300)
            )
            await db.commit()
            assert fired == 0
            assert log == ["teams"]
            state = await NotificationEscalationRepo.get(
                db, TEST_ORG_ID, incident_id=inc.id, user_id=user.id
            )
            assert state.status == "resolved"

    async def test_start_is_idempotent_per_incident_user(self, session_factory):
        user = await _user(session_factory)
        inc = await _incident(session_factory)
        log: list[str] = []
        sender = _recording_sender(log)
        t0 = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
        stages = parse_stages(
            [{"channel_id": "teams", "delay_seconds": 300}, {"channel_id": "sms"}]
        )
        async with session_factory() as db:
            await ne.start_escalation(
                db,
                TEST_ORG_ID,
                incident=inc,
                user=user,
                stages=stages,
                sender=sender,
                at=t0,
            )
            await ne.start_escalation(
                db,
                TEST_ORG_ID,
                incident=inc,
                user=user,
                stages=stages,
                sender=sender,
                at=t0,
            )
            await db.commit()
            assert log == ["teams"]  # second start is a no-op


# ---------------------------------------------------------------------------
# Default sender — channel resolution
# ---------------------------------------------------------------------------


class TestDefaultSender:
    async def test_legacy_key_without_destination_is_skipped(self, session_factory):
        user = await _user(session_factory)
        inc = await _incident(session_factory)
        sender = ne.build_notification_sender(lambda key: None)
        async with session_factory() as db:
            status, detail = await sender(
                db,
                TEST_ORG_ID,
                channel_id="sms",
                incident=inc,
                user=user,
                subject="s",
                body="b",
            )
            assert status == "skipped"

    async def test_unknown_connector_id_is_skipped(self, session_factory):
        user = await _user(session_factory)
        inc = await _incident(session_factory)
        sender = ne.build_notification_sender(lambda key: None)
        async with session_factory() as db:
            status, detail = await sender(
                db,
                TEST_ORG_ID,
                channel_id=str(uuid.uuid4()),
                incident=inc,
                user=user,
                subject="s",
                body="b",
            )
            assert status == "skipped"
            assert detail == "channel_unconfigured"
