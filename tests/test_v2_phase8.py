"""v2 Phase 8 — opt-in bounded memory growth (eviction)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, IncidentMemory, Organization
from backend.db.repos import IncidentMemoryRepo
from backend.memory.writeback import maybe_evict

ORG_ID = uuid.UUID("80000000-0000-0000-0000-000000000001")
SERVICE_ID = uuid.UUID("80000000-0000-0000-0000-0000000000aa")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG_ID, name="P8", slug="p8"))
        await db.commit()
    yield maker
    await engine.dispose()


async def _add(db, *, title, age_days, pinned=False, helpful=0):
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    db.add(
        IncidentMemory(
            org_id=ORG_ID,
            service_id=SERVICE_ID,
            title=title,
            summary_md="x",
            tags=[],
            pinned=pinned,
            helpful_count=helpful,
            created_at=ts,
            updated_at=ts,
            last_used_at=ts,
        )
    )


async def test_eviction_disabled_is_noop(factory):
    async with factory() as db:
        for i in range(5):
            await _add(db, title=f"m{i}", age_days=i)
        await db.commit()
    report = await maybe_evict(
        factory, org_id=ORG_ID, service_id=SERVICE_ID, enabled=False, max_total=2
    )
    assert report["evicted"] == 0
    async with factory() as db:
        assert await IncidentMemoryRepo.count_for_service(db, ORG_ID, SERVICE_ID) == 5


async def test_evicts_oldest_first_down_to_ceiling(factory):
    async with factory() as db:
        # 6 plain memories of increasing age (m5 oldest).
        for i in range(6):
            await _add(db, title=f"m{i}", age_days=i)
        await db.commit()

    report = await maybe_evict(
        factory, org_id=ORG_ID, service_id=SERVICE_ID, enabled=True, max_total=3
    )
    assert report["evicted"] == 3
    assert report["total_after"] == 3
    async with factory() as db:
        remaining = {
            m.title
            for m in await IncidentMemoryRepo.list_for_org(
                db, ORG_ID, service_id=SERVICE_ID
            )
        }
    # Newest three survive; oldest three (m3/m4/m5) evicted.
    assert remaining == {"m0", "m1", "m2"}


async def test_pinned_and_high_recall_are_protected(factory):
    async with factory() as db:
        # Oldest two are protected (pinned / high helpful) and must survive even
        # though they're the least-recently-used.
        await _add(db, title="pinned-old", age_days=100, pinned=True)
        await _add(db, title="loved-old", age_days=99, helpful=5)
        await _add(db, title="plain-a", age_days=3)
        await _add(db, title="plain-b", age_days=2)
        await _add(db, title="plain-c", age_days=1)
        await db.commit()

    report = await maybe_evict(
        factory, org_id=ORG_ID, service_id=SERVICE_ID, enabled=True, max_total=3
    )
    assert report["protected"] == 2
    assert report["evicted"] == 2  # 5 total - 3 ceiling
    async with factory() as db:
        remaining = {
            m.title
            for m in await IncidentMemoryRepo.list_for_org(
                db, ORG_ID, service_id=SERVICE_ID
            )
        }
    # Protected pair survives; only plain memories are evictable (oldest first).
    assert "pinned-old" in remaining and "loved-old" in remaining
    assert "plain-c" in remaining
    assert "plain-a" not in remaining and "plain-b" not in remaining
