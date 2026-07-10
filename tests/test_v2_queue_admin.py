"""v2 queue admin — purge / cancel-one / reprioritize (queue_rank) repo behavior."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Incident, Organization, Service, Session
from backend.db.repos import SessionRepo

ORG_ID = uuid.UUID("90000000-0000-0000-0000-000000000001")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG_ID, name="QA", slug="qa"))
        await db.commit()
    yield maker
    await engine.dispose()


async def _queued(db, *, priority: str, title: str) -> Session:
    svc = Service(
        org_id=ORG_ID,
        team_id=uuid.uuid4(),
        name=title,
        slug=f"s-{uuid.uuid4().hex[:8]}",
        priority=priority,
    )
    db.add(svc)
    await db.flush()
    inc = Incident(
        org_id=ORG_ID,
        title=title,
        description="x",
        priority=priority,
        service_id=svc.id,
    )
    db.add(inc)
    await db.flush()
    s = Session(
        org_id=ORG_ID,
        incident_id=inc.id,
        tier=2,
        status="queued",
        queued_at=datetime.now(timezone.utc),
    )
    db.add(s)
    await db.flush()
    return s


async def test_purge_and_cancel_one(factory):
    async with factory() as db:
        a = await _queued(db, priority="P2", title="a")
        b = await _queued(db, priority="P1", title="b")
        await db.commit()

        assert await SessionRepo.cancel_queued_session(
            db, ORG_ID, a.id, reason="removed"
        )
        await db.commit()
        assert (await SessionRepo.get_by_id(db, ORG_ID, a.id)).status == "cancelled"
        # cancelling a non-queued session is a no-op
        assert not await SessionRepo.cancel_queued_session(
            db, ORG_ID, a.id, reason="again"
        )

        count = await SessionRepo.purge_queue(db, ORG_ID, reason="purged")
        await db.commit()
        assert count == 1  # only b remained queued
        assert (await SessionRepo.get_by_id(db, ORG_ID, b.id)).status == "cancelled"


async def test_rank_overrides_priority_in_drain_order(factory):
    async with factory() as db:
        p0 = await _queued(db, priority="P0", title="p0")
        p3 = await _queued(db, priority="P3", title="p3")
        await db.commit()

        # Default order: P0 before P3.
        order = await SessionRepo.list_queued_for_drain(db, ORG_ID)
        assert [s.id for s in order] == [p0.id, p3.id]

        # Rank the P3 to the front — it now drains before the P0.
        lo, hi = await SessionRepo.queue_rank_bounds(db, ORG_ID)
        assert (lo, hi) == (None, None)
        await SessionRepo.set_queue_rank(db, ORG_ID, p3.id, rank=-1)
        await db.commit()

        order = await SessionRepo.list_queued_for_drain(db, ORG_ID)
        assert [s.id for s in order] == [p3.id, p0.id]
