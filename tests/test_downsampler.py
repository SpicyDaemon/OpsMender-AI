"""Tests for the uptime downsampler (Sprint 25)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config_loader import AppConfig
from backend.db.models import (
    Base,
    SLATarget,
    UptimeSample,
    UptimeSample1h,
    UptimeSample5m,
)
from backend.db.repos import SLATargetRepo, UptimeSampleRepo
from backend.sla.downsampler import UptimeDownsampler, _floor_1h, _floor_5m


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(engine, expire_on_commit=False)
    yield fac
    await engine.dispose()


@pytest.fixture
async def db(factory):
    async with factory() as session:
        yield session


@pytest.fixture
async def target(db: AsyncSession) -> SLATarget:
    t = await SLATargetRepo.create(
        db, name="test-target", kind="http",
        config={"url": "https://example.com"},
    )
    await db.commit()
    return t


class TestFloorFunctions:

    def test_floor_5m(self):
        dt = datetime(2026, 4, 30, 14, 13, 45, tzinfo=timezone.utc)
        assert _floor_5m(dt) == datetime(2026, 4, 30, 14, 10, 0, tzinfo=timezone.utc)

    def test_floor_5m_already_aligned(self):
        dt = datetime(2026, 4, 30, 14, 15, 0, tzinfo=timezone.utc)
        assert _floor_5m(dt) == dt

    def test_floor_1h(self):
        dt = datetime(2026, 4, 30, 14, 37, 12, tzinfo=timezone.utc)
        assert _floor_1h(dt) == datetime(2026, 4, 30, 14, 0, 0, tzinfo=timezone.utc)

    def test_floor_1h_already_aligned(self):
        dt = datetime(2026, 4, 30, 14, 0, 0, tzinfo=timezone.utc)
        assert _floor_1h(dt) == dt


class TestRollTo5m:

    @pytest.mark.asyncio
    async def test_creates_5m_buckets(self, factory, db: AsyncSession, target: SLATarget):
        """Raw samples should be grouped into 5m aggregate buckets."""
        downsampler = UptimeDownsampler(factory)

        # Insert 10 raw samples spread across two 5m buckets in the past
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        bucket1_start = _floor_5m(base)
        bucket2_start = bucket1_start + timedelta(minutes=5)

        for i in range(5):
            sample = UptimeSample(
                target_id=target.id,
                observed_at=bucket1_start + timedelta(seconds=30 * i),
                up=(i < 4),  # 4 up, 1 down = 80%
                source="poller",
            )
            db.add(sample)

        for i in range(5):
            sample = UptimeSample(
                target_id=target.id,
                observed_at=bucket2_start + timedelta(seconds=30 * i),
                up=True,  # all up = 100%
                source="poller",
            )
            db.add(sample)
        await db.commit()

        result = await downsampler.run_once()
        assert result["buckets_5m"] == 2

        # Verify the buckets were created
        async with factory() as check_db:
            stmt = select(UptimeSample5m).where(
                UptimeSample5m.target_id == target.id
            ).order_by(UptimeSample5m.bucket_start)
            rows = (await check_db.execute(stmt)).scalars().all()

        assert len(rows) == 2
        assert rows[0].up_pct == 0.8  # 4/5
        assert rows[0].total_samples == 5
        assert rows[1].up_pct == 1.0  # 5/5
        assert rows[1].total_samples == 5

    @pytest.mark.asyncio
    async def test_idempotent(self, factory, db: AsyncSession, target: SLATarget):
        """Running the downsampler twice should not create duplicate buckets."""
        downsampler = UptimeDownsampler(factory)

        # Use a specific bucket_start to guarantee all samples land in one bucket
        bucket_start = _floor_5m(datetime.now(timezone.utc) - timedelta(hours=1))
        for i in range(3):
            sample = UptimeSample(
                target_id=target.id,
                observed_at=bucket_start + timedelta(seconds=30 * i),
                up=True,
                source="poller",
            )
            db.add(sample)
        await db.commit()

        r1 = await downsampler.run_once()
        r2 = await downsampler.run_once()

        assert r1["buckets_5m"] >= 1
        assert r2["buckets_5m"] == 0  # idempotent -- already created

    @pytest.mark.asyncio
    async def test_suppressed_excluded_from_pct(self, factory, db: AsyncSession, target: SLATarget):
        """Suppressed samples should not affect up_pct calculation."""
        downsampler = UptimeDownsampler(factory)

        base = datetime.now(timezone.utc) - timedelta(hours=1)
        bucket_start = _floor_5m(base)

        # 3 up, 2 suppressed down = up_pct should be 100% (only 3 non-suppressed, all up)
        for i in range(3):
            db.add(UptimeSample(
                target_id=target.id,
                observed_at=bucket_start + timedelta(seconds=30 * i),
                up=True, source="poller",
            ))
        for i in range(2):
            db.add(UptimeSample(
                target_id=target.id,
                observed_at=bucket_start + timedelta(seconds=30 * (3 + i)),
                up=False, source="poller", suppressed=True,
            ))
        await db.commit()

        await downsampler.run_once()

        async with factory() as check_db:
            stmt = select(UptimeSample5m).where(
                UptimeSample5m.target_id == target.id,
            )
            row = (await check_db.execute(stmt)).scalar_one()

        assert row.up_pct == 1.0  # suppressed samples excluded from pct
        assert row.total_samples == 5  # all samples counted for total


class TestRollTo1h:

    @pytest.mark.asyncio
    async def test_creates_1h_buckets(self, factory, db: AsyncSession, target: SLATarget):
        """5m buckets should be rolled into 1h buckets."""
        downsampler = UptimeDownsampler(factory)

        # Manually insert 5m buckets spanning 2 hours in the past
        h1 = datetime.now(timezone.utc) - timedelta(hours=3)
        h1_floor = _floor_1h(h1)
        h2_floor = h1_floor + timedelta(hours=1)

        # Hour 1: 12 five-minute buckets
        for i in range(12):
            db.add(UptimeSample5m(
                target_id=target.id,
                bucket_start=h1_floor + timedelta(minutes=5 * i),
                up_pct=0.9 if i < 6 else 1.0,
                total_samples=5,
            ))

        # Hour 2: 12 five-minute buckets all 100%
        for i in range(12):
            db.add(UptimeSample5m(
                target_id=target.id,
                bucket_start=h2_floor + timedelta(minutes=5 * i),
                up_pct=1.0,
                total_samples=5,
            ))
        await db.commit()

        result = await downsampler.run_once()
        assert result["buckets_1h"] == 2

        async with factory() as check_db:
            stmt = select(UptimeSample1h).where(
                UptimeSample1h.target_id == target.id
            ).order_by(UptimeSample1h.bucket_start)
            rows = (await check_db.execute(stmt)).scalars().all()

        assert len(rows) == 2
        # Hour 1: weighted average of 6*0.9 + 6*1.0 = 5.4+6.0 = 11.4 / 12*5 = 0.95
        # Each bucket has 5 samples, so weighted = (0.9*5*6 + 1.0*5*6) / (5*12)
        assert abs(rows[0].up_pct - 0.95) < 0.001
        assert rows[0].total_samples == 60
        assert rows[1].up_pct == 1.0
        assert rows[1].total_samples == 60


class TestPruneRaw:

    @pytest.mark.asyncio
    async def test_prunes_old_samples(self, factory, db: AsyncSession, target: SLATarget):
        """Raw samples older than retention_days should be deleted."""
        downsampler = UptimeDownsampler(factory, retention_days=30)

        now = datetime.now(timezone.utc)

        # Insert old sample (40 days ago)
        db.add(UptimeSample(
            target_id=target.id,
            observed_at=now - timedelta(days=40),
            up=True, source="poller",
        ))
        # Insert recent sample (1 day ago)
        db.add(UptimeSample(
            target_id=target.id,
            observed_at=now - timedelta(days=1),
            up=True, source="poller",
        ))
        await db.commit()

        result = await downsampler.run_once()
        assert result["pruned_raw"] == 1

        # Verify only the recent one remains
        async with factory() as check_db:
            stmt = select(UptimeSample).where(
                UptimeSample.target_id == target.id
            )
            remaining = (await check_db.execute(stmt)).scalars().all()

        assert len(remaining) == 1
        # SQLite strips timezone — make comparison safe
        obs = remaining[0].observed_at
        if obs.tzinfo is None:
            obs = obs.replace(tzinfo=timezone.utc)
        assert obs > now - timedelta(days=2)

    @pytest.mark.asyncio
    async def test_no_prune_when_recent(self, factory, db: AsyncSession, target: SLATarget):
        """Recent samples should not be pruned."""
        downsampler = UptimeDownsampler(factory, retention_days=30)

        now = datetime.now(timezone.utc)
        db.add(UptimeSample(
            target_id=target.id,
            observed_at=now - timedelta(days=5),
            up=True, source="poller",
        ))
        await db.commit()

        result = await downsampler.run_once()
        assert result["pruned_raw"] == 0


class TestRunOnceIntegration:

    @pytest.mark.asyncio
    async def test_full_pipeline(self, factory, db: AsyncSession, target: SLATarget):
        """Full run_once should create 5m + 1h buckets and prune old data."""
        downsampler = UptimeDownsampler(factory, retention_days=30)
        now = datetime.now(timezone.utc)

        # Insert raw samples: 2 hours ago (will be rolled to 5m, then 1h)
        base = now - timedelta(hours=2)
        bucket_start = _floor_5m(base)
        for i in range(5):
            db.add(UptimeSample(
                target_id=target.id,
                observed_at=bucket_start + timedelta(seconds=30 * i),
                up=True, source="poller",
            ))

        # Insert an old sample that should be pruned
        db.add(UptimeSample(
            target_id=target.id,
            observed_at=now - timedelta(days=35),
            up=False, source="poller",
        ))
        await db.commit()

        result = await downsampler.run_once()

        # Should have created at least 1 five-minute bucket
        assert result["buckets_5m"] >= 1
        # Old sample should be pruned
        assert result["pruned_raw"] == 1

    @pytest.mark.asyncio
    async def test_empty_db(self, factory):
        """run_once on empty DB should succeed with all zeros."""
        downsampler = UptimeDownsampler(factory)
        result = await downsampler.run_once()
        assert result == {"buckets_5m": 0, "buckets_1h": 0, "pruned_raw": 0}
