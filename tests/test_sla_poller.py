"""Tests for SLA Poller."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config_loader import AppConfig
from backend.db.models import Base, MaintenanceWindow
from backend.db.repos import MaintenanceWindowRepo, SLATargetRepo
from backend.sla.poller import SLAPoller, expected_status_matches

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(engine, expire_on_commit=False)
    async with fac() as session:
        from backend.db.models import Organization

        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()
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
    async def test_probe_target_http_expected_error_code(
        self, factory, config, db: AsyncSession
    ):
        poller = SLAPoller(factory, config)
        target = await SLATargetRepo.create(
            db,
            TEST_ORG_ID,
            name="missing-page",
            kind="http",
            config={"url": "http://test.com/missing", "expected_statuses": [404]},
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = Response(404)
            up, latency = await poller._probe_target(target)

            assert up is True
            assert latency is not None

    def test_expected_status_matches_multiple_forms(self):
        assert expected_status_matches(204, {"expected_statuses": [200, 204]})
        assert expected_status_matches(404, {"expected_statuses": "200,404"})
        assert expected_status_matches(202, {"expected_statuses": "2xx"})
        assert expected_status_matches(503, {"expected_statuses": "500-599"})
        assert expected_status_matches(500, {"expected_status": 200}) is False

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
            await poller._probe_and_record(TEST_ORG_ID, target)

        # Check DB for sample
        from sqlalchemy import select
        from backend.db.models import UptimeSample

        stmt = select(UptimeSample).where(
            UptimeSample.org_id == TEST_ORG_ID, UptimeSample.target_id == target.id
        )
        res = await db.execute(stmt)
        sample = res.scalar_one()

        assert sample.up is True
        assert sample.latency_ms == 42
        assert sample.suppressed is False

    def test_poller_defaults_enabled_every_5_minutes(self):
        """v1: the HTTP/HTTPS checker runs automatically on a 5-minute cadence."""
        from backend.config_loader import SLAConfig

        defaults = SLAConfig()
        assert defaults.poller_enabled is True
        assert defaults.poll_interval_default == 300

    @pytest.mark.asyncio
    async def test_scheduler_selects_active_http_targets_only(
        self, db: AsyncSession
    ):
        """The poller's per-tick target query returns active targets only —
        inactive (monitoring-paused) targets are not probed."""
        await SLATargetRepo.create(
            db, TEST_ORG_ID, name="active-web", kind="http",
            config={"url": "http://test.com"}, is_active=True,
        )
        await SLATargetRepo.create(
            db, TEST_ORG_ID, name="paused-web", kind="http",
            config={"url": "http://down.com"}, is_active=False,
        )
        await db.commit()

        selected = await SLATargetRepo.list_all(db, TEST_ORG_ID, active_only=True)
        names = {t.name for t in selected}
        assert names == {"active-web"}

    @pytest.mark.asyncio
    async def test_automatic_check_records_down_sample_on_failure(
        self, factory, config, db: AsyncSession
    ):
        """A failing automatic check records a down (up=False) sample — the same
        path the scheduler runs per target each tick."""
        target = await SLATargetRepo.create(
            db, TEST_ORG_ID, name="failing", kind="http",
            config={"url": "http://test.com"},
        )
        await db.commit()

        poller = SLAPoller(factory, config)
        with patch.object(poller, "_probe_target", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = (False, 88)
            await poller._probe_and_record(TEST_ORG_ID, target)

        from sqlalchemy import select
        from backend.db.models import UptimeSample

        sample = (
            await db.execute(
                select(UptimeSample).where(UptimeSample.target_id == target.id)
            )
        ).scalar_one()
        assert sample.up is False
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
            org_id=TEST_ORG_ID,
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
            await poller._probe_and_record(TEST_ORG_ID, target)

        from sqlalchemy import select
        from backend.db.models import UptimeSample

        stmt = select(UptimeSample).where(
            UptimeSample.org_id == TEST_ORG_ID, UptimeSample.target_id == target.id
        )
        res = await db.execute(stmt)
        sample = res.scalar_one()

        assert sample.up is False
        assert sample.suppressed is True
