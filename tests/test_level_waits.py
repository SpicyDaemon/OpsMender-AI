"""M1-22: the next level waits for the paged people's own steps, with the
locked timing (O-01, O-02).

A level escalates at the later of its timeout and the moment everyone it
paged has had all their own steps plus an answer window after the last one
(that step's stored wait; none when it could not be sent), unless someone
acknowledges. Someone with no steps saved holds nothing, so the level timeout
alone decides for them, and new levels wait 3 minutes (O-01). The wait for
people's steps ends at most 10 minutes after the level paged for P0 and P1,
and 20 for P2 and P3 (O-02). Someone who is no longer an active admin or
operator holds nothing (O-14). Driven with a controlled clock: personal steps
tick first, then the chain, as the scheduler does.
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

ONE_MINUTE_STEPS = [("push", 60), ("sms", 60), ("voice", 60)]
THREE_MINUTE_STEPS = [("push", 180), ("sms", 180), ("voice", 180)]
FIVE_MINUTE_STEPS = [("push", 300), ("sms", 300), ("voice", 300)]
TEN_MINUTE_STEPS = [("push", 600), ("sms", 600), ("voice", 600)]
# Steps used up at 2 minutes, then a 30-minute answer window.
LONG_WINDOW_STEPS = [("push", 60), ("sms", 60), ("voice", 1800)]


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


async def _steps(w: World, user_id: uuid.UUID, steps, priority: str = "P1") -> None:
    async with w.app.state.session_factory() as db:
        await UserNotificationPrefRepo.upsert(
            db,
            TEST_ORG_ID,
            user_id,
            routing={
                priority: [{"channel_id": c, "delay_seconds": d} for c, d in steps]
            },
        )
        await db.commit()


async def _start_incident(
    w: World, chain_id, *, at: datetime, priority: str, factory
) -> uuid.UUID:
    async with w.app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="checkout down",
            description="d",
            priority=priority,
            response_mode="page",
        )
        await _esc.start_chain(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain_id,
            at=at,
            channel_factory=factory,
        )
        await db.commit()
        return incident.id


async def _start(
    w: World,
    levels,
    *,
    timeout: int,
    at: datetime,
    priority: str = "P1",
    factory=NO_CHANNELS,
) -> uuid.UUID:
    """An incident whose chain has one level per entry: a user id, or a list
    of user ids paged together through a team."""
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
        await db.commit()
        chain_id = chain.id
    return await _start_incident(w, chain_id, at=at, priority=priority, factory=factory)


async def _level_minutes(
    w: World,
    incident_id,
    base: datetime,
    until: int,
    factory=NO_CHANNELS,
    *,
    start: int = 1,
) -> list[int]:
    """Tick minute by minute from ``start``; return the minute each level
    fired (with 0 for the first level when starting at minute 1)."""
    fired = [0] if start == 1 else []
    async with w.app.state.session_factory() as db:
        last = (
            await IncidentChainStateRepo.get_for_incident(db, TEST_ORG_ID, incident_id)
        ).current_step_index
    for minute in range(start, until + 1):
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


@pytest.mark.parametrize(
    "factory", [EMAIL_SENT, NO_CHANNELS], ids=["reached", "not-reached"]
)
async def test_people_without_steps_move_at_the_level_timeout(world, failing, factory):
    # No hidden answer window stretches a 60-second level, whether or not the
    # immediate page reached them (O-01).
    people = await _people(world, 3)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=60, at=base, factory=factory)
    assert await _level_minutes(world, incident_id, base, 4, factory=factory) == [
        0,
        1,
        2,
    ]


async def test_new_levels_wait_3_minutes(world, failing):
    people = await _people(world, 3)
    async with world.app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name="Defaults", slug=f"defaults-{uuid.uuid4().hex[:6]}"
        )
        await db.commit()
        team_id = team.id
    chain = await world.client.post(
        "/escalation-chains",
        json={"team_id": str(team_id), "name": f"defaults-{uuid.uuid4().hex[:6]}"},
        headers=world.admin,
    )
    assert chain.status_code == 201, chain.text
    chain_id = chain.json()["id"]
    for index, person in enumerate(people):
        level = await world.client.post(
            f"/escalation-chains/{chain_id}/steps",
            json={"step_index": index, "target_type": "user", "target_id": str(person)},
            headers=world.admin,
        )
        assert level.status_code == 201, level.text
        assert level.json()["timeout_seconds"] == 180
    base = datetime.now(timezone.utc)
    incident_id = await _start_incident(
        world, uuid.UUID(chain_id), at=base, priority="P1", factory=NO_CHANNELS
    )
    assert await _level_minutes(world, incident_id, base, 8) == [0, 3, 6]


async def test_three_3_minute_stages_give_9_minutes(world, failing):
    people = await _people(world, 4)
    for person in people:
        await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=60, at=base)
    # Steps at 0, 3 and 6, then the last step's 3-minute answer window.
    assert await _level_minutes(world, incident_id, base, 30) == [0, 9, 18, 27]


@pytest.mark.parametrize(
    ("priority", "steps"),
    [
        ("P0", FIVE_MINUTE_STEPS),
        ("P1", FIVE_MINUTE_STEPS),
        ("P1", LONG_WINDOW_STEPS),
    ],
    ids=["p0-5-minute", "p1-5-minute", "p1-long-window"],
)
async def test_p0_and_p1_wait_up_to_10_minutes(world, failing, priority, steps):
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, steps, priority)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=60, at=base, priority=priority)
    assert await _level_minutes(world, incident_id, base, 16) == [0, 10]


@pytest.mark.parametrize(
    ("priority", "steps", "expected"),
    [
        ("P2", FIVE_MINUTE_STEPS, [0, 15]),
        ("P2", TEN_MINUTE_STEPS, [0, 20]),
        ("P3", TEN_MINUTE_STEPS, [0, 20]),
        ("P3", LONG_WINDOW_STEPS, [0, 20]),
    ],
    ids=["p2-5-minute", "p2-10-minute", "p3-10-minute", "p3-long-window"],
)
async def test_p2_and_p3_wait_up_to_20_minutes(
    world, failing, priority, steps, expected
):
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, steps, priority)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=60, at=base, priority=priority)
    assert await _level_minutes(world, incident_id, base, 25) == expected


async def test_a_longer_level_timeout_is_the_minimum(world, failing):
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=720, at=base)
    assert await _level_minutes(world, incident_id, base, 15) == [0, 12]


async def test_a_last_step_that_could_not_be_sent_gets_no_answer_window(world, failing):
    failing.update({"sms", "voice"})
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=60, at=base)
    assert await _level_minutes(world, incident_id, base, 10) == [0, 6]


async def test_an_acknowledgement_stops_everything(world, failing):
    people = await _people(world, 2)
    for person in people:
        await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=60, at=base)
    assert await _level_minutes(world, incident_id, base, 1) == [0]
    async with world.app.state.session_factory() as db:
        await _esc.acknowledge(
            db,
            TEST_ORG_ID,
            incident_id=incident_id,
            assignee_id=people[0],
            at=base + timedelta(minutes=2),
        )
        await db.commit()
    for minute in range(3, 13):
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
    # Push at 0 went out; the SMS at 3 and the call at 6 did not.
    assert sorted(sent) == [0]


async def test_a_level_with_two_people_waits_for_the_slower(world, failing):
    quick, slow, next_level = await _people(world, 3)
    await _steps(world, quick, ONE_MINUTE_STEPS)
    await _steps(world, slow, THREE_MINUTE_STEPS)
    await _steps(world, next_level, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, [[quick, slow], next_level], timeout=60, at=base)
    assert await _level_minutes(world, incident_id, base, 12) == [0, 9]


@pytest.mark.parametrize(
    "change",
    [{"is_active": False}, {"role": "viewer"}],
    ids=["deactivated", "demoted"],
)
async def test_someone_who_can_no_longer_act_does_not_hold_the_level(
    world, failing, change
):
    person, next_level = await _people(world, 2)
    await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, [person, next_level], timeout=60, at=base)
    changed = await world.client.patch(
        f"/auth/users/{person}", json=change, headers=world.admin
    )
    assert changed.status_code == 200, changed.text
    # The level moves at its timeout, not at the end of their steps (9).
    assert await _level_minutes(world, incident_id, base, 4) == [0, 1]


async def _delete_last_level(w: World, incident_id) -> None:
    async with w.app.state.session_factory() as db:
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident_id
        )
        steps = await EscalationStepRepo.list_for_chain(db, TEST_ORG_ID, state.chain_id)
        chain_id, last = state.chain_id, steps[-1].id
    deleted = await w.client.delete(
        f"/escalation-chains/{chain_id}/steps/{last}", headers=w.admin
    )
    assert deleted.status_code == 204, deleted.text


async def test_deleting_a_later_level_keeps_the_current_wait(world, failing):
    people = await _people(world, 3)
    for person in people:
        await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, people, timeout=60, at=base)
    assert await _level_minutes(world, incident_id, base, 5) == [0]
    # Deleting a level renumbers the run; the first person's call at 6 and
    # answer window still hold the next level until 9.
    await _delete_last_level(world, incident_id)
    assert await _level_minutes(world, incident_id, base, 10, start=6) == [9]
