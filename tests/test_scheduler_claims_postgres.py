"""M1-68 (O-16, O-23) on real PostgreSQL connections: two pollers that tick
together on one SLA target make one check. The audit scheduler cases went with
the environment scan audits (M1-93). Run with PART4_PG_URL pointed at a
disposable database. The URL is not logged.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config_loader import AppConfig
from backend.db.models import Base, Organization, UptimeSample
from backend.db.repos import SLATargetRepo
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
