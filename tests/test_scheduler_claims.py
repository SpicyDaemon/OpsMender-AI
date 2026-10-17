"""M1-68 (O-16, O-23) on SQLite: a due audit schedule is claimed once per
interval and the run releases the claim; a dead claimant's lease expires; an
SLA target's poll is claimed once per interval across pollers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.db.repos import AuditScheduleRepo, SLATargetRepo
from backend.sla.poller import SLAPoller
from tests.test_audits import TEST_ORG_ID, app as _app_fixture
from tests.test_sla_poller import config as _config_fixture

app = pytest.fixture(_app_fixture.__wrapped__)
config = pytest.fixture(_config_fixture.__wrapped__)


async def _due_schedule(factory, now):
    async with factory() as db:
        schedule = await AuditScheduleRepo.create(
            db,
            TEST_ORG_ID,
            name="due",
            analyzers=["environment-scan"],
            interval_minutes=60,
            next_run_at=now - timedelta(minutes=1),
        )
        await db.commit()
        return schedule.id


async def test_a_due_schedule_is_claimed_once_until_the_run_releases_it(app):
    factory = app.state.session_factory
    now = datetime.now(timezone.utc)
    schedule_id = await _due_schedule(factory, now)

    async with factory() as db:
        first = await AuditScheduleRepo.claim(
            db, schedule_id, now=now, lease_minutes=60
        )
        second = await AuditScheduleRepo.claim(
            db, schedule_id, now=now, lease_minutes=60
        )
        await db.commit()
    assert (first, second) == (True, False)

    # The claimant died: after the lease the schedule can be claimed again.
    later = now + timedelta(minutes=61)
    async with factory() as db:
        before_lease = await AuditScheduleRepo.claim(
            db, schedule_id, now=now + timedelta(minutes=59), lease_minutes=60
        )
        after_lease = await AuditScheduleRepo.claim(
            db, schedule_id, now=later, lease_minutes=60
        )
        await db.commit()
    assert (before_lease, after_lease) == (False, True)

    # A finished run releases the claim and moves the schedule on.
    async with factory() as db:
        schedule = await AuditScheduleRepo.get_by_id(db, TEST_ORG_ID, schedule_id)
        await AuditScheduleRepo.mark_run(db, schedule, now=later)
        await db.commit()
    async with factory() as db:
        schedule = await AuditScheduleRepo.get_by_id(db, TEST_ORG_ID, schedule_id)
        assert schedule.claimed_at is None
        assert schedule.next_run_at.replace(tzinfo=None) > later.replace(tzinfo=None)
        not_due = await AuditScheduleRepo.claim(
            db, schedule_id, now=later, lease_minutes=60
        )
    assert not_due is False


async def test_two_schedulers_ticking_together_make_one_run(app):
    from backend.auditor.scheduler import AuditScheduler
    from backend.db.repos import AuditRunRepo

    factory = app.state.session_factory
    now = datetime.now(timezone.utc)
    await _due_schedule(factory, now)
    runs: list = []

    async def record(db, *, run_id, **kwargs):
        runs.append(run_id)
        # Long enough for the other scheduler to list the same due schedule.
        await asyncio.sleep(0.1)
        return 0

    schedulers = [
        AuditScheduler(factory, pool=app.state.mcp_pool, config=app.state.config)
        for _ in range(2)
    ]
    with patch("backend.auditor.scheduler.run_audit", side_effect=record):
        fired = await asyncio.gather(*(s.tick(now=now) for s in schedulers))
    assert sorted(fired) == [0, 1]
    assert len(runs) == 1
    async with factory() as db:
        assert (
            len(await AuditRunRepo.list_all(db, TEST_ORG_ID, limit=10, offset=0)) == 1
        )


async def test_a_target_is_probed_once_per_interval_across_pollers(app, config):
    from sqlalchemy import select

    from backend.db.models import UptimeSample

    factory = app.state.session_factory
    async with factory() as db:
        target = await SLATargetRepo.create(
            db, TEST_ORG_ID, name="web", kind="http", config={"url": "http://test.com"}
        )
        await db.commit()
        target_id = target.id

    pollers = [SLAPoller(factory, config) for _ in range(2)]
    with (
        patch.object(pollers[0], "_probe_target", AsyncMock(return_value=(True, 5))),
        patch.object(pollers[1], "_probe_target", AsyncMock(return_value=(True, 5))),
    ):
        for poller in pollers:
            await poller._tick()
        for poller in pollers:
            while poller._running_probes:
                await asyncio.sleep(0.01)
    async with factory() as db:
        samples = (
            await db.scalars(
                select(UptimeSample).where(UptimeSample.target_id == target_id)
            )
        ).all()
    assert len(samples) == 1

    # The next interval is the next claim; a tick just before it still wins.
    interval = config.sla.poll_interval_default
    now = datetime.now(timezone.utc)
    async with factory() as db:
        too_soon = await SLATargetRepo.claim_poll(
            db,
            TEST_ORG_ID,
            target_id,
            now=now + timedelta(seconds=interval * 0.5),
            interval_seconds=interval,
        )
        next_turn = await SLATargetRepo.claim_poll(
            db,
            TEST_ORG_ID,
            target_id,
            now=now + timedelta(seconds=interval * 0.95),
            interval_seconds=interval,
        )
        await db.commit()
    assert (too_soon, next_turn) == (False, True)
