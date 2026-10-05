"""Personal notification steps claimed under lock, on real PostgreSQL
connections (EC-E07).

A controlled transport holds the first send open, so a second tick, an ACK,
a resolve, a team handoff or a deletion overlaps a send that is in flight.
Run with PART4_PG_URL pointed at a disposable database. The URL is not logged.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, IncidentPage, Organization
from backend.db.repos import (
    IncidentRepo,
    NotificationEscalationRepo,
    ServiceRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging import escalation as esc
from backend.paging import notification_escalation as ne
from backend.paging import reassign
from backend.paging.routing import parse_stages

pytestmark = pytest.mark.integration

# Started 301 seconds ago, step 2 is due now. THREE has a step after it;
# in TWO it is the last one.
THREE = [
    {"channel_id": "sms", "delay_seconds": 300},
    {"channel_id": "voice", "delay_seconds": 300},
    {"channel_id": "email", "delay_seconds": 300},
]
TWO = THREE[:2]


@pytest.fixture
async def pg():
    url = os.environ.get("PART4_PG_URL")
    if not url:
        pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
    engine = create_async_engine(url, pool_size=6)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    tag = org_id.hex[:8]
    async with factory() as db:
        db.add(Organization(id=org_id, name="Stage claims", slug=f"stages-{tag}"))
        users = []
        for index, role in enumerate(("operator", "operator", "admin")):
            user = await UserRepo.create(
                db,
                username=f"stages-{tag}-{index}",
                email=f"stages-{tag}-{index}@example.test",
                password_hash="x",
                role=role,
                primary_org_id=org_id,
            )
            users.append(user.id)
        await db.commit()
    yield factory, org_id, users
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class _Transport:
    """Records every send. The first one stays open until ``release``."""

    def __init__(self, *, hold: bool = True) -> None:
        self.calls: list[tuple[str, uuid.UUID, float]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not hold:
            self.entered.set()

    async def __call__(self, db, org_id, *, channel_id, incident, user, **_):
        self.calls.append((channel_id, user.id, time.perf_counter()))
        if not self.entered.is_set():
            self.entered.set()
            await self.release.wait()
        return ("sent", None)


async def _quiet(db, org_id, **_):
    return ("sent", None)


async def _incident(factory, org_id, **fields):
    async with factory() as db:
        incident = await IncidentRepo.create(
            db, org_id, title="stage race", description="test", **fields
        )
        await db.commit()
        return incident.id


async def _start(factory, org_id, incident_id, user_id, stages, at):
    async with factory() as db:
        await ne.start_escalation(
            db,
            org_id,
            incident=await IncidentRepo.get_by_id(db, org_id, incident_id),
            user=await UserRepo.get_by_id(db, user_id),
            stages=parse_stages(stages),
            sender=_quiet,
            at=at,
        )
        await db.commit()


async def _state(factory, org_id, incident_id, user_id):
    async with factory() as db:
        return await NotificationEscalationRepo.get(
            db, org_id, incident_id=incident_id, user_id=user_id
        )


async def _steps_sent(factory, incident_id):
    async with factory() as db:
        rows = (
            await db.execute(
                select(IncidentPage.user_id, IncidentPage.step_index)
                .where(IncidentPage.incident_id == incident_id)
                .order_by(IncidentPage.step_index)
            )
        ).all()
        return [tuple(row) for row in rows]


async def _tick(factory, transport, at, pids=None):
    async with factory() as db:
        pid = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        if pids is not None:
            pids.append(pid)
        changed = await ne.tick_all_due(db, sender=transport, at=at)
        await db.commit()
        return changed


async def _run(factory, work, pids):
    """Run ``work`` in its own connection; return its result and commit time."""
    async with factory() as db:
        pids.append((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        result = await work(db)
        await db.commit()
        return result, time.perf_counter()


async def _waits_on_lock(factory, pids, task, timeout=10.0):
    """True once the task's connection waits on a lock; False if it ends first."""
    deadline = time.monotonic() + timeout
    async with factory() as observer:
        while time.monotonic() < deadline:
            if task.done():
                return False
            if pids:
                event = (
                    await observer.execute(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid = :pid"
                        ),
                        {"pid": pids[0]},
                    )
                ).scalar_one_or_none()
                # pg_stat_activity is a per-transaction snapshot.
                await observer.rollback()
                if event == "Lock":
                    return True
            await asyncio.sleep(0.02)
    raise AssertionError("the overlapping change neither waited nor finished")


@pytest.mark.asyncio
async def test_two_ticks_send_a_due_step_once(pg):
    factory, org_id, users = pg
    now = datetime.now(timezone.utc)
    incident_id = await _incident(factory, org_id)
    await _start(
        factory, org_id, incident_id, users[0], THREE, now - timedelta(seconds=301)
    )
    transport, pids = _Transport(), []

    first = asyncio.create_task(_tick(factory, transport, now, pids))
    await asyncio.wait_for(transport.entered.wait(), timeout=10)
    try:
        # A second scheduler runs while the first is still sending.
        second = await asyncio.wait_for(_tick(factory, transport, now, pids), 10)
    finally:
        transport.release.set()
    assert await asyncio.wait_for(first, timeout=10) == 1

    assert second == 0
    assert len(set(pids)) == 2
    assert [call[:2] for call in transport.calls] == [("voice", users[0])]
    assert await _steps_sent(factory, incident_id) == [(users[0], 0), (users[0], 1)]
    state = await _state(factory, org_id, incident_id, users[0])
    assert (state.status, state.current_stage) == ("running", 1)


@pytest.mark.asyncio
async def test_an_ack_waits_for_the_tick_and_nothing_sends_after_it(pg):
    """Two people due in one tick: the ACK can't commit between their sends."""
    factory, org_id, users = pg
    now = datetime.now(timezone.utc)
    incident_id = await _incident(factory, org_id)
    for user_id in users[:2]:
        await _start(
            factory, org_id, incident_id, user_id, THREE, now - timedelta(seconds=301)
        )
    transport, ack_pids = _Transport(), []

    async def ack(db):
        outcome = await esc.acknowledge(
            db, org_id, incident_id=incident_id, assignee_id=users[0], at=now
        )
        return outcome.status

    tick = asyncio.create_task(_tick(factory, transport, now))
    await asyncio.wait_for(transport.entered.wait(), timeout=10)
    stop = asyncio.create_task(_run(factory, ack, ack_pids))
    try:
        ack_waited = await _waits_on_lock(factory, ack_pids, stop)
    finally:
        transport.release.set()
    changed = await asyncio.wait_for(tick, timeout=10)
    outcome, acked_at = await asyncio.wait_for(stop, timeout=10)

    assert ack_waited
    assert (changed, outcome) == (2, "acknowledged")
    assert sorted(call[1] for call in transport.calls) == sorted(users[:2])
    assert all(started < acked_at for *_, started in transport.calls)
    for user_id in users[:2]:
        state = await _state(factory, org_id, incident_id, user_id)
        assert (state.status, state.current_stage) == ("acked", 1)
        assert state.next_stage_due_at is None

    # Once the ACK is committed, later due times send nothing.
    later = _Transport(hold=False)
    assert await _tick(factory, later, now + timedelta(seconds=900)) == 0
    assert later.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_status", ["acked", "resolved"])
async def test_a_stop_during_the_last_step_keeps_its_status(pg, stop_status):
    factory, org_id, users = pg
    now = datetime.now(timezone.utc)
    incident_id = await _incident(factory, org_id)
    await _start(
        factory, org_id, incident_id, users[0], TWO, now - timedelta(seconds=301)
    )
    transport, stop_pids = _Transport(), []

    async def stop_it(db):
        if stop_status == "acked":
            await esc.acknowledge(
                db, org_id, incident_id=incident_id, assignee_id=users[0], at=now
            )
        else:
            await IncidentRepo.update_fields(db, org_id, incident_id, status="resolved")

    tick = asyncio.create_task(_tick(factory, transport, now))
    await asyncio.wait_for(transport.entered.wait(), timeout=10)
    stop = asyncio.create_task(_run(factory, stop_it, stop_pids))
    try:
        stop_waited = await _waits_on_lock(factory, stop_pids, stop)
    finally:
        transport.release.set()
    changed = await asyncio.wait_for(tick, timeout=10)
    await asyncio.wait_for(stop, timeout=10)

    assert stop_waited
    assert changed == 1
    assert [call[:2] for call in transport.calls] == [("voice", users[0])]
    state = await _state(factory, org_id, incident_id, users[0])
    # The last step was sent and recorded; the stop's status is kept.
    assert (state.status, state.current_stage) == (stop_status, 1)
    assert state.next_stage_due_at is None
    assert await _steps_sent(factory, incident_id) == [(users[0], 0), (users[0], 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["handoff", "delete"])
async def test_a_change_holding_the_incident_waits_instead_of_deadlocking(pg, change):
    """Handoff and deletion lock the incident row and then end its steps."""
    factory, org_id, users = pg
    tag = org_id.hex[:8]
    async with factory() as db:
        team_a = await TeamRepo.create(db, org_id, name="Owning", slug=f"a-{tag}")
        team_b = await TeamRepo.create(db, org_id, name="Receiving", slug=f"b-{tag}")
        service = await ServiceRepo.create(
            db,
            org_id,
            team_id=team_a.id,
            name="Stage service",
            slug=f"stage-service-{tag}",
            priority="P2",
        )
        await db.commit()
    now = datetime.now(timezone.utc)
    incident_id = await _incident(
        factory, org_id, service_id=service.id, priority="P2", response_mode="notify"
    )
    await _start(
        factory, org_id, incident_id, users[0], THREE, now - timedelta(seconds=301)
    )
    transport, change_pids = _Transport(), []

    async def apply(db):
        if change == "handoff":
            await reassign.reassign_to_team(
                db,
                org_id,
                incident=await IncidentRepo.get_by_id(db, org_id, incident_id),
                team=await TeamRepo.get_by_id(db, org_id, team_b.id),
                actor=await UserRepo.get_by_id(db, users[2]),
                note="Wrong team",
            )
        else:
            assert await IncidentRepo.get_by_id(
                db, org_id, incident_id, for_update=True
            )
            assert await IncidentRepo.delete_permanently(db, org_id, incident_id)

    tick = asyncio.create_task(_tick(factory, transport, now))
    await asyncio.wait_for(transport.entered.wait(), timeout=10)
    pending = asyncio.create_task(_run(factory, apply, change_pids))
    try:
        change_waited = await _waits_on_lock(factory, change_pids, pending)
    finally:
        transport.release.set()
    changed = await asyncio.wait_for(tick, timeout=15)
    await asyncio.wait_for(pending, timeout=15)

    assert change_waited
    assert changed == 1
    assert [call[:2] for call in transport.calls] == [("voice", users[0])]
    state = await _state(factory, org_id, incident_id, users[0])
    if change == "handoff":
        # The step went out first; the handoff then ended the rest.
        assert (state.status, state.current_stage) == ("cancelled", 1)
        assert state.next_stage_due_at is None
    else:
        assert state is None
        async with factory() as db:
            assert await IncidentRepo.get_by_id(db, org_id, incident_id) is None
