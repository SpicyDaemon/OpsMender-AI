"""Tests for SLA Poller."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config_loader import AppConfig
from backend.db.models import Base, MaintenanceWindow
from backend.db.repos import MaintenanceWindowRepo, SLATargetRepo
from backend.sla.poller import SLAPoller


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
def config():
    return AppConfig.load()


class TestSLAPoller:
    @pytest.mark.asyncio
    async def test_probe_target_http_success(self, factory, config, db: AsyncSession):
        poller = SLAPoller(factory, config)
        target = await SLATargetRepo.create(
            db,
            TEST_ORG_ID,
            name="web",
            kind="http",
            config={"url": "http://test.com", "expected_status": 200},
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = Response(200)
            up, latency = await poller._probe_target(target)

            assert up is True
            assert latency is not None
            mock_req.assert_called_once_with("GET", "http://test.com")

    @pytest.mark.asyncio
    async def test_probe_target_http_failure(self, factory, config, db: AsyncSession):
        poller = SLAPoller(factory, config)
        target = await SLATargetRepo.create(
            db,
            TEST_ORG_ID,
            name="web2",
            kind="http",
            config={"url": "http://test.com", "expected_status": 200},
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = Response(500)
            up, latency = await poller._probe_target(target)

            assert up is False
            assert latency is not None

    @pytest.mark.asyncio
    async def test_probe_target_tcp_success(self, factory, config, db: AsyncSession):
        poller = SLAPoller(factory, config)
        target = await SLATargetRepo.create(
            db,
            TEST_ORG_ID,
            name="db",
            kind="tcp",
            config={"host": "127.0.0.1", "port": 5432},
        )

        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_conn:
            mock_conn.return_value = (mock_reader, mock_writer)
            up, latency = await poller._probe_target(target)

            assert up is True
            assert latency is not None
            mock_conn.assert_called_once_with("127.0.0.1", 5432)

    @pytest.mark.asyncio
    async def test_probe_and_record_creates_sample(
        self, factory, config, db: AsyncSession
    ):
        poller = SLAPoller(factory, config)
        target = await SLATargetRepo.create(
            db, TEST_ORG_ID, name="web3", kind="http", config={"url": "http://test.com"}
        )

        with patch.object(
            poller, "_probe_target", new_callable=AsyncMock
        ) as mock_probe:
            mock_probe.return_value = (True, 42)
            await poller._probe_and_record(target)

        # Check DB for sample
        from sqlalchemy import select
        from backend.db.models import UptimeSample

        stmt = select(UptimeSample).where(UptimeSample.target_id == target.id)
        res = await db.execute(stmt)
        sample = res.scalar_one()

        assert sample.up is True
        assert sample.latency_ms == 42
        assert sample.suppressed is False

    @pytest.mark.asyncio
    async def test_probe_and_record_suppressed_in_maintenance(
        self, factory, config, db: AsyncSession
    ):
        poller = SLAPoller(factory, config)
        target = await SLATargetRepo.create(
            db, TEST_ORG_ID, name="web4", kind="http", config={"url": "http://test.com"}
        )

        now = datetime.now(timezone.utc)
        mw = MaintenanceWindow(
            name="test-mw",
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(minutes=5),
            target_ids=[str(target.id)],
        )
        db.add(mw)
        await db.commit()

        with patch.object(
            poller, "_probe_target", new_callable=AsyncMock
        ) as mock_probe:
            mock_probe.return_value = (False, 100)
            await poller._probe_and_record(target)

        from sqlalchemy import select
        from backend.db.models import UptimeSample

        stmt = select(UptimeSample).where(UptimeSample.target_id == target.id)
        res = await db.execute(stmt)
        sample = res.scalar_one()

        assert sample.up is False
        assert sample.suppressed is True
