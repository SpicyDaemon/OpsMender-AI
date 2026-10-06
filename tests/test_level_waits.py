"""M1-22: the next level waits for the paged people's own steps.

A level escalates at the later of its timeout and the moment everyone it
paged has had all their own steps plus an answer window after the last one
(that step's stored wait; none when it could not be sent), unless someone
acknowledges, and never more than 30 minutes after the level fired. Driven
with a controlled clock: personal steps tick first, then the chain, as the
scheduler does.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.db.models import IncidentPage, NotificationEscalation
from backend.db.repos import (
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentChainStateRepo,
    IncidentRepo,
    TeamRepo,
    UserNotificationPrefRepo,
)
from backend.paging import escalation as _esc
from backend.paging import notification_escalation as _ne
from backend.paging.dispatch import DeliveryAttempt
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

DEFAULT_STEPS = [("push", 300), ("sms", 300), ("voice", 300)]


def NO_CHANNELS(_key):
    """Pages are dispatched; personal steps go through the test sender."""
    return None


class _SentChannel:
    async def send(self, *, recipient, subject, body, **_kwargs):
        return DeliveryAttempt("email", "sent", None)


def EMAIL_SENT(key):
    """Immediate routing (email by default) that reaches the person."""
    return _SentChannel() if key == "email" else None


@pytest.fixture
def failing(monkeypatch) -> set[str]:
    """Channels whose sends fail; everything else is sent."""
    channels: set[str] = set()

    def build(_factory=None):
        async def sender(db, org_id, *, channel_id, incident, user, subject, body):
            return (
                ("failed", "carrier refused")
                if channel_id in channels
                else ("sent", None)
            )

        return sender

    monkeypatch.setattr(_ne, "build_notification_sender", build)
    return channels


async def _steps(w: World, user_id: uuid.UUID, steps) -> None:
    async with w.app.state.session_factory() as db:
        await UserNotificationPrefRepo.upsert(
            db,
            TEST_ORG_ID,
            user_id,
            routing={"P1": [{"channel_id": c, "delay_seconds": d} for c, d in steps]},
        )
        await db.commit()


async def _start(
    w: World, levels, *, timeout: int, at: datetime, factory=NO_CHANNELS
) -> uuid.UUID:
    """A P1 incident whose chain has one level per entry: a user id, or a
    list of user ids paged together through a team."""
    async with w.app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name="Waits", slug=f"waits-{uuid.uuid4().hex[:6]}"
        )
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=team.id, name=f"waits-{uuid.uuid4().hex[:6]}"
        )
        for index, level in enumerate(levels):
            if isinstance(level, list):
                group = await TeamRepo.create(
                    db,
                    TEST_ORG_ID,
                    name=f"L{index}",
                    slug=f"l{index}-{uuid.uuid4().hex[:6]}",
                )
                for user_id in level:
                    await TeamRepo.add_member(
                        db, TEST_ORG_ID, group.id, user_id=user_id
                    )
                target_type, target_id = "team", group.id
            else:
                target_type, target_id = "user", level
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=index,
                target_type=target_type,
                target_id=target_id,
                timeout_seconds=timeout,
            )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="checkout down",
            description="d",
            priority="P1",
            response_mode="page",
        )
        await _esc.start_chain(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain.id,
            at=at,
            channel_factory=factory,
        )
        await db.commit()
        return incident.id


async def _level_minutes(
    w: World, incident_id, base: datetime, until: int, factory=NO_CHANNELS
) -> list[int]:
    """Tick minute by minute; return the minute each level fired."""
    fired = [0]
    async with w.app.state.session_factory() as db:
        last = (
            await IncidentChainStateRepo.get_for_incident(db, TEST_ORG_ID, incident_id)
        ).current_step_index
    for minute in range(1, until + 1):
        at = base + timedelta(minutes=minute)
        async with w.app.state.session_factory() as db:
            await _ne.tick_all_due(
                db, at=at, sender=_ne.build_notification_sender(None)
            )
            await _esc.tick_all_due(db, at=at, channel_factory=factory)
            await db.commit()
        async with w.app.state.session_factory() as db:
            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident_id
            )
        if state.current_step_index != last:
            fired.append(minute)
            last = state.current_step_index
    return fired


async def _people(w: World, count: int) -> list[uuid.UUID]:
    return [await _user(w.app, f"lw-{uuid.uuid4().hex[:6]}") for _ in range(count)]


async def test_default_steps_give_15_minutes_per_level(world, failing):
    people = await _people(world, 4)
    for person in people:
        await _steps(world, person, DEFAULT_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=300, at=base)
    assert await _level_minutes(world, incident_id, base, 50) == [0, 15, 30, 45]


async def test_long_waits_are_capped_at_30_minutes(world, failing):
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, [("push", 1800), ("sms", 1800), ("voice", 1800)])
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=300, at=base)
    assert await _level_minutes(world, incident_id, base, 35) == [0, 30]


async def test_a_longer_level_timeout_is_the_minimum(world, failing):
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, DEFAULT_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=1200, at=base)
    assert await _level_minutes(world, incident_id, base, 25) == [0, 20]


async def test_a_last_step_that_could_not_be_sent_gets_no_answer_window(world, failing):
    failing.update({"sms", "voice"})
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, DEFAULT_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=300, at=base)
    assert await _level_minutes(world, incident_id, base, 15) == [0, 10]


async def test_an_acknowledgement_at_7_stops_everything(world, failing):
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, DEFAULT_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=300, at=base)
    assert await _level_minutes(world, incident_id, base, 6) == [0]
    async with world.app.state.session_factory() as db:
        await _esc.acknowledge(
            db,
            TEST_ORG_ID,
            incident_id=incident_id,
            assignee_id=people[0],
            at=base + timedelta(minutes=7),
        )
        await db.commit()
    for minute in range(8, 15):
        at = base + timedelta(minutes=minute)
        async with world.app.state.session_factory() as db:
            await _ne.tick_all_due(
                db, at=at, sender=_ne.build_notification_sender(None)
            )
            await _esc.tick_all_due(db, at=at, channel_factory=NO_CHANNELS)
            await db.commit()
    async with world.app.state.session_factory() as db:
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident_id
        )
        steps = (
            (
                await db.execute(
                    select(NotificationEscalation.status).where(
                        NotificationEscalation.incident_id == incident_id
                    )
                )
            )
            .scalars()
            .all()
        )
        sent = (
            (
                await db.execute(
                    select(IncidentPage.step_index).where(
                        IncidentPage.incident_id == incident_id,
                        IncidentPage.chain_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert (state.status, state.current_step_index) == ("acked", 0)
    assert steps == ["acked"]
    # Push at 0 and SMS at 5 went out; the call at 10 did not.
    assert sorted(sent) == [0, 1]


async def test_a_level_with_two_people_waits_for_the_slower(world, failing):
    quick, slow, next_level = await _people(world, 3)
    await _steps(world, quick, DEFAULT_STEPS)
    await _steps(world, slow, [("push", 300), ("sms", 300), ("voice", 600)])
    await _steps(world, next_level, DEFAULT_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, [[quick, slow], next_level], timeout=300, at=base)
    assert await _level_minutes(world, incident_id, base, 25) == [0, 20]


async def test_immediate_routing_waits_one_answer_window(world, failing):
    first, second = await _people(world, 2)
    base = datetime.now(timezone.utc)
    incident_id = await _start(
        world, [first, second], timeout=60, at=base, factory=EMAIL_SENT
    )
    assert await _level_minutes(world, incident_id, base, 7, factory=EMAIL_SENT) == [
        0,
        5,
    ]


async def test_immediate_routing_that_reached_nobody_keeps_the_timeout(world, failing):
    first, second = await _people(world, 2)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, [first, second], timeout=60, at=base)
    assert await _level_minutes(world, incident_id, base, 3) == [0, 1]
