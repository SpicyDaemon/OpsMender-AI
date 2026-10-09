"""M1-36: steps stop for people who can no longer act (O-14), and someone
paged after the incident has an owner gets only their first step (O-04).

Driven with a controlled clock: personal steps tick first, then the chain,
as the scheduler does. Every step is sent by the test sender.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.db.models import IncidentPage
from backend.db.repos import (
    IncidentAssignmentRepo,
    IncidentRepo,
    NotificationEscalationRepo,
    UserRepo,
)
from backend.paging import escalation as _esc
from backend.paging import notification_escalation as _ne
from tests.test_level_waits import (
    NO_CHANNELS,
    THREE_MINUTE_STEPS,
    _people,
    _start,
    _steps,
    failing as _failing_fixture,
)
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)
failing = pytest.fixture(_failing_fixture.__wrapped__)


async def _tick(w: World, base: datetime, minutes) -> None:
    for minute in minutes:
        at = base + timedelta(minutes=minute)
        async with w.app.state.session_factory() as db:
            await _ne.tick_all_due(
                db, at=at, sender=_ne.build_notification_sender(None)
            )
            await _esc.tick_all_due(db, at=at, channel_factory=NO_CHANNELS)
            await db.commit()


async def _steps_sent(w: World, incident_id, user_id) -> list[int]:
    """The personal steps sent to someone, by index."""
    async with w.app.state.session_factory() as db:
        return sorted(
            (
                await db.execute(
                    select(IncidentPage.step_index).where(
                        IncidentPage.incident_id == incident_id,
                        IncidentPage.user_id == user_id,
                        IncidentPage.chain_id.is_(None),
                        IncidentPage.delivery_status == "sent",
                    )
                )
            )
            .scalars()
            .all()
        )


async def _row(w: World, incident_id, user_id):
    async with w.app.state.session_factory() as db:
        return await NotificationEscalationRepo.get(
            db, TEST_ORG_ID, incident_id=incident_id, user_id=user_id
        )


async def _own(w: World, incident_id, user_id, at: datetime) -> None:
    async with w.app.state.session_factory() as db:
        await _esc.acknowledge(
            db, TEST_ORG_ID, incident_id=incident_id, assignee_id=user_id, at=at
        )
        await db.commit()


@pytest.mark.parametrize(
    "change",
    [{"is_active": False}, {"role": "viewer"}],
    ids=["deactivated", "demoted"],
)
async def test_deactivation_or_demotion_stops_running_steps(world, failing, change):
    person, next_level = await _people(world, 2)
    await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, [person, next_level], timeout=600, at=base)
    assert (await _row(world, incident_id, person)).status == "running"

    changed = await world.client.patch(
        f"/auth/users/{person}", json=change, headers=world.admin
    )
    assert changed.status_code == 200, changed.text
    row = await _row(world, incident_id, person)
    assert (row.status, row.next_stage_due_at) == ("cancelled", None)
    await _tick(world, base, [3, 6, 9])
    assert await _steps_sent(world, incident_id, person) == [0]


@pytest.mark.parametrize("change", ["inactive", "viewer", "deleted"])
async def test_no_step_goes_to_someone_who_can_no_longer_act(world, failing, change):
    # A change that didn't come through People (say, a role from sign-in)
    # still stops the next step when it falls due.
    person, next_level = await _people(world, 2)
    await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, [person, next_level], timeout=600, at=base)
    async with world.app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, person)
        if change == "inactive":
            user.is_active = False
        elif change == "viewer":
            user.role = "viewer"
        else:
            user.deleted_at = base
        await db.commit()
    await _tick(world, base, [3, 6, 9])
    assert await _steps_sent(world, incident_id, person) == [0]
    assert (await _row(world, incident_id, person)).status == "cancelled"


@pytest.mark.parametrize(("owned", "expected"), [(True, [0]), (False, [0, 1, 2])])
async def test_a_responder_gets_one_step_once_the_incident_has_an_owner(
    world, failing, owned, expected
):
    first, next_level, helper = await _people(world, 3)
    await _steps(world, helper, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, [first, next_level], timeout=1800, at=base)
    if owned:
        await _own(world, incident_id, first, base)
    added = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helper)]},
        headers=world.admin,
    )
    assert added.status_code == 201, added.text
    # Responders are paged at the real time; tick well past their steps.
    later = datetime.now(timezone.utc)
    await _tick(world, later, [3, 6, 9])
    assert await _steps_sent(world, incident_id, helper) == expected
    async with world.app.state.session_factory() as db:
        owner = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident_id)
    assert (owner.assigned_to if owner is not None else None) == (
        first if owned else None
    )


@pytest.mark.parametrize(("owned", "expected"), [(True, [0]), (False, [0, 1, 2])])
async def test_escalate_now_gives_one_step_while_the_owner_keeps_it(
    world, failing, owned, expected
):
    first, second = await _people(world, 2)
    for person in (first, second):
        await _steps(world, person, THREE_MINUTE_STEPS)
    base = datetime.now(timezone.utc)
    incident_id = await _start(world, [first, second], timeout=1800, at=base)
    if owned:
        await _own(world, incident_id, first, base + timedelta(minutes=1))
    async with world.app.state.session_factory() as db:
        fired = await _esc.escalate_now(
            db,
            TEST_ORG_ID,
            incident_id=incident_id,
            at=base + timedelta(minutes=2),
            channel_factory=NO_CHANNELS,
        )
        await db.commit()
    assert fired is not None and fired.users_paged == [second]
    await _tick(world, base, [5, 8, 11])
    assert await _steps_sent(world, incident_id, second) == expected
    async with world.app.state.session_factory() as db:
        owner = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident_id)
    assert (owner.assigned_to if owner is not None else None) == (
        first if owned else None
    )


async def test_someone_who_can_no_longer_act_is_never_started(world, failing):
    person, next_level = await _people(world, 2)
    await _steps(world, person, THREE_MINUTE_STEPS)
    async with world.app.state.session_factory() as db:
        (await UserRepo.get_by_id(db, person)).role = "viewer"
        await db.commit()
    base = datetime.now(timezone.utc)
    # A viewer on a level is skipped as a target; a responder request for
    # them is refused. Starting their steps directly sends nothing either.
    incident_id = await _start(world, [next_level], timeout=600, at=base)
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        user = await UserRepo.get_by_id(db, person)
        await _ne.start_escalation(
            db,
            TEST_ORG_ID,
            incident=incident,
            user=user,
            stages=_ne.parse_stages(
                [{"channel_id": c, "delay_seconds": d} for c, d in THREE_MINUTE_STEPS]
            ),
            sender=_ne.build_notification_sender(None),
            at=base,
        )
        await db.commit()
    assert await _row(world, incident_id, person) is None
    assert await _steps_sent(world, incident_id, person) == []
