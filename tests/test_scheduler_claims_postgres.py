"""M1-68 (O-16, O-23) on real PostgreSQL connections: two audit schedulers that
tick together on one due schedule make one run; two pollers that tick together
on one SLA target make one check; a dead claimant's lease expires and the job
runs once more, not twice. Run with PART4_PG_URL pointed at a disposable
database. The URL is not logged.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.auditor.scheduler import AuditScheduler
from backend.config_loader import AppConfig
from backend.db.models import AuditRun, Base, Organization, UptimeSample
from backend.db.repos import AuditScheduleRepo, SLATargetRepo
from backend.mcp.pool import MCPServerPool
from backend.sla.poller import SLAPoller

pytestmark = pytest.mark.integration

ROUNDS = 4


@pytest.fixture
async def pg(tmp_path):
    url = os.environ.get("PART4_PG_URL")
    if not url:
        pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
    engine = create_async_engine(url, pool_size=10)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Claims", slug=f"claims-{org_id.hex[:8]}"))
        await db.commit()
    try:
        yield factory, org_id, AppConfig.load()
    finally:
        await engine.dispose()


async def _due_schedule(factory, org_id, now, name):
    async with factory() as db:
        schedule = await AuditScheduleRepo.create(
            db,
            org_id,
            name=name,
            analyzers=["environment-scan"],
            interval_minutes=60,
            next_run_at=now - timedelta(minutes=1),
        )
        await db.commit()
        return schedule.id


async def _runs(factory, org_id):
    async with factory() as db:
        return (
            await db.scalars(select(AuditRun).where(AuditRun.org_id == org_id))
        ).all()


async def test_two_schedulers_together_make_one_run(pg):
    factory, org_id, config = pg
    pool = MCPServerPool(factory, env_fallback=[])
    started: list = []

    async def slow_run(db, *, run_id, **kwargs):
        started.append(run_id)
        await asyncio.sleep(0.05)
        return 0

    with patch("backend.auditor.scheduler.run_audit", side_effect=slow_run):
        for round_number in range(ROUNDS):
            now = datetime.now(timezone.utc)
            await _due_schedule(factory, org_id, now, f"due-{round_number}")
            schedulers = [
                AuditScheduler(factory, pool=pool, config=config) for _ in range(2)
            ]
            fired = await asyncio.gather(*(s.tick(now=now) for s in schedulers))
            assert sorted(fired) == [0, 1], fired
    assert len(started) == ROUNDS
    assert len(await _runs(factory, org_id)) == ROUNDS


async def test_two_pollers_together_make_one_check(pg):
    factory, org_id, config = pg
    async with factory() as db:
        target = await SLATargetRepo.create(
            db, org_id, name="web", kind="http", config={"url": "http://test.com"}
        )
        await db.commit()
        target_id = target.id

    pollers = [SLAPoller(factory, config) for _ in range(2)]
    patches = [
        patch.object(p, "_probe_target", AsyncMock(return_value=(True, 7)))
        for p in pollers
    ]
    for active in patches:
        active.start()
    try:
        await asyncio.gather(*(p._tick() for p in pollers))
        for poller in pollers:
            for _ in range(200):
                if not poller._running_probes:
                    break
                await asyncio.sleep(0.02)
    finally:
        for active in patches:
            active.stop()
    async with factory() as db:
        samples = (
            await db.scalars(
                select(UptimeSample).where(UptimeSample.target_id == target_id)
            )
        ).all()
    assert len(samples) == 1


async def test_a_dead_claimants_job_runs_once_more_after_the_lease(pg):
    factory, org_id, config = pg
    pool = MCPServerPool(factory, env_fallback=[])
    now = datetime.now(timezone.utc)
    schedule_id = await _due_schedule(factory, org_id, now, "orphaned")

    # The first claimant claims and dies before its run.
    async with factory() as db:
        assert await AuditScheduleRepo.claim(db, schedule_id, now=now, lease_minutes=60)
        await db.commit()

    # Due schedules are listed across the whole database, so count this org's
    # runs rather than what the scheduler fired in all.
    runs: list = []

    async def record(db, *, run_id, org_id, **kwargs):
        runs.append(org_id)
        return 0

    survivor = AuditScheduler(factory, pool=pool, config=config)
    with patch("backend.auditor.scheduler.run_audit", side_effect=record):
        await survivor.tick(now=now + timedelta(minutes=30))
        within_lease = runs.count(org_id)
        await survivor.tick(now=now + timedelta(minutes=61))
        after_lease = runs.count(org_id)
        await survivor.tick(now=now + timedelta(minutes=62))
        again = runs.count(org_id)
    assert (within_lease, after_lease, again) == (0, 1, 1)
    async with factory() as db:
        schedule = await AuditScheduleRepo.get_by_id(db, org_id, schedule_id)
        assert schedule.claimed_at is None
        assert schedule.next_run_at > now + timedelta(minutes=61)
