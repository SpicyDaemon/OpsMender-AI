"""M1-68 (O-16, O-23) on SQLite: an SLA target's poll is claimed once per
interval across pollers. The audit schedule half went with the environment scan
audits (M1-93)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Organization
from backend.db.repos import SLATargetRepo
from backend.sla.poller import SLAPoller
from tests.test_sla_poller import config as _config_fixture

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
config = pytest.fixture(_config_fixture.__wrapped__)


@pytest.fixture
async def app(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org"))
        await session.commit()
    yield SimpleNamespace(state=SimpleNamespace(session_factory=factory))
    await engine.dispose()


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
