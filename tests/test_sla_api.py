"""Tests for SLA / SLO / Maintenance Window CRUD APIs (Sprint 25)."""

from __future__ import annotations

import uuid
import os
from unittest.mock import AsyncMock, patch

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.auth import create_access_token, hash_password
from backend.config_loader import AppConfig
from backend.db.models import Base, User

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def app_db():
    """Create an in-memory SQLite app + db for testing."""
    config = AppConfig.load()
    app = create_app(config)

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Wire the factory into the app dependencies
    from backend.api import deps

    deps._session_factory = factory
    app.state.session_factory = factory

    # Create an admin user
    async with factory() as db:
        from backend.db.models import Organization

        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        db.add(org)
        await db.commit()

        admin = User(
            username="admin",
            email="admin@test.com",
            password_hash=hash_password("password123"),
            role="admin",
            primary_org_id=TEST_ORG_ID,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        admin_id = admin.id

    token = create_access_token(admin_id, "admin")

    yield app, factory, token

    await engine.dispose()


@pytest.fixture
async def client(app_db):
    app, _, token = app_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


@pytest.fixture
async def db(app_db):
    _, factory, _ = app_db
    async with factory() as session:
        yield session


# ======================================================================
# SLA Target CRUD
# ======================================================================


class TestSLATargetAPI:
    @pytest.mark.asyncio
    async def test_create_sla_target(self, client: AsyncClient):
        resp = await client.post(
            "/sla-targets",
            json={
                "name": "web-app",
                "kind": "http",
                "config": {
                    "url": "https://example.com",
                    "expected_statuses": [200, "2xx"],
                },
                "owner_team": "platform",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "web-app"
        assert data["kind"] == "http"
        assert data["config"]["url"] == "https://example.com"
        assert data["config"]["expected_statuses"] == [200, "2xx"]
        assert data["owner_team"] == "platform"
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_create_sla_target_rejects_bad_expected_status(
        self, client: AsyncClient
    ):
        resp = await client.post(
            "/sla-targets",
            json={
                "name": "bad-status",
                "kind": "http",
                "config": {"url": "https://example.com", "expected_statuses": ["wat"]},
            },
        )
        assert resp.status_code == 400
        assert "Invalid HTTP status code" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_sla_targets(self, client: AsyncClient):
        await client.post(
            "/sla-targets",
            json={
                "name": "t1",
                "kind": "http",
            },
        )
        await client.post(
            "/sla-targets",
            json={
                "name": "t2",
                "kind": "tcp",
            },
        )

        resp = await client.get("/sla-targets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    @pytest.mark.asyncio
    async def test_get_sla_target(self, client: AsyncClient):
        create_resp = await client.post(
            "/sla-targets",
            json={
                "name": "get-me",
                "kind": "external",
            },
        )
        tid = create_resp.json()["id"]

        resp = await client.get(f"/sla-targets/{tid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "get-me"

    @pytest.mark.asyncio
    async def test_get_sla_target_not_found(self, client: AsyncClient):
        resp = await client.get(f"/sla-targets/{uuid.uuid4()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_sla_target(self, client: AsyncClient):
        create_resp = await client.post(
            "/sla-targets",
            json={
                "name": "updatable",
                "kind": "http",
            },
        )
        tid = create_resp.json()["id"]

        resp = await client.put(
            f"/sla-targets/{tid}",
            json={
                "name": "updated-name",
                "is_active": False,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "updated-name"
        assert resp.json()["is_active"] is False

    @pytest.mark.asyncio
    async def test_delete_sla_target(self, client: AsyncClient):
        create_resp = await client.post(
            "/sla-targets",
            json={
                "name": "deletable",
                "kind": "tcp",
            },
        )
        tid = create_resp.json()["id"]

        resp = await client.delete(f"/sla-targets/{tid}")
        assert resp.status_code == 204

        resp2 = await client.get(f"/sla-targets/{tid}")
        assert resp2.status_code == 404

    @pytest.mark.asyncio
    async def test_create_duplicate_name_conflict(self, client: AsyncClient):
        await client.post(
            "/sla-targets",
            json={
                "name": "duped",
                "kind": "http",
            },
        )
        resp = await client.post(
            "/sla-targets",
            json={
                "name": "duped",
                "kind": "tcp",
            },
        )
        assert resp.status_code == 409


# ======================================================================
# SLO CRUD
# ======================================================================


class TestSLOAPI:
    @pytest.mark.asyncio
    async def test_create_slo(self, client: AsyncClient):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "slo-target",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        resp = await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "99.9% availability",
                "objective_pct": 99.9,
                "window_seconds": 604800,  # 7d
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "99.9% availability"
        assert data["objective_pct"] == 99.9
        assert data["target_id"] == target_id

    @pytest.mark.asyncio
    async def test_create_slo_bad_target(self, client: AsyncClient):
        resp = await client.post(
            "/slos",
            json={
                "target_id": str(uuid.uuid4()),
                "name": "orphan",
                "objective_pct": 99.0,
                "window_seconds": 3600,
            },
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_slos(self, client: AsyncClient):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "slo-list-target",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "slo-1",
                "objective_pct": 99.0,
                "window_seconds": 3600,
            },
        )
        await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "slo-2",
                "objective_pct": 99.5,
                "window_seconds": 86400,
            },
        )

        resp = await client.get("/slos")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 2

    @pytest.mark.asyncio
    async def test_update_slo(self, client: AsyncClient):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "slo-update-target",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        create_resp = await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "adjustable",
                "objective_pct": 99.0,
                "window_seconds": 3600,
            },
        )
        slo_id = create_resp.json()["id"]

        resp = await client.put(
            f"/slos/{slo_id}",
            json={
                "objective_pct": 99.5,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["objective_pct"] == 99.5

    @pytest.mark.asyncio
    async def test_delete_slo(self, client: AsyncClient):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "slo-del-target",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        create_resp = await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "removable",
                "objective_pct": 99.9,
                "window_seconds": 3600,
            },
        )
        slo_id = create_resp.json()["id"]

        resp = await client.delete(f"/slos/{slo_id}")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_slo_status_no_samples(self, client: AsyncClient):
        """SLO with no uptime samples should report 100% uptime."""
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "slo-status-target",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        create_resp = await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "status-test",
                "objective_pct": 99.9,
                "window_seconds": 604800,
            },
        )
        slo_id = create_resp.json()["id"]

        resp = await client.get(f"/slos/{slo_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["compliant"] is True
        assert data["actual_pct"] == 100.0


# ======================================================================
# Maintenance Window CRUD
# ======================================================================


class TestMaintenanceWindowAPI:
    @pytest.mark.asyncio
    async def test_create_maintenance_window(self, client: AsyncClient):
        now = datetime.now(timezone.utc)
        resp = await client.post(
            "/maintenance-windows",
            json={
                "name": "deploy",
                "reason": "weekly deploy",
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=2)).isoformat(),
                "target_ids": ["*"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "deploy"
        assert data["reason"] == "weekly deploy"
        assert data["target_ids"] == ["*"]

    @pytest.mark.asyncio
    async def test_create_maintenance_window_bad_times(self, client: AsyncClient):
        now = datetime.now(timezone.utc)
        resp = await client.post(
            "/maintenance-windows",
            json={
                "name": "bad",
                "starts_at": (now + timedelta(hours=2)).isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
                "target_ids": ["*"],
            },
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_maintenance_windows(self, client: AsyncClient):
        now = datetime.now(timezone.utc)
        await client.post(
            "/maintenance-windows",
            json={
                "name": "mw1",
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=2)).isoformat(),
                "target_ids": ["*"],
            },
        )

        resp = await client.get("/maintenance-windows")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_update_maintenance_window(self, client: AsyncClient):
        now = datetime.now(timezone.utc)
        create_resp = await client.post(
            "/maintenance-windows",
            json={
                "name": "updatable-mw",
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=2)).isoformat(),
                "target_ids": ["*"],
            },
        )
        mw_id = create_resp.json()["id"]

        resp = await client.put(
            f"/maintenance-windows/{mw_id}",
            json={
                "name": "renamed-mw",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "renamed-mw"

    @pytest.mark.asyncio
    async def test_delete_maintenance_window(self, client: AsyncClient):
        now = datetime.now(timezone.utc)
        create_resp = await client.post(
            "/maintenance-windows",
            json={
                "name": "deletable-mw",
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=2)).isoformat(),
                "target_ids": ["*"],
            },
        )
        mw_id = create_resp.json()["id"]

        resp = await client.delete(f"/maintenance-windows/{mw_id}")
        assert resp.status_code == 204


# ======================================================================
# Uptime + SLO status with data
# ======================================================================


class TestUptimeAPI:
    @pytest.mark.asyncio
    async def test_uptime_empty(self, client: AsyncClient):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "uptime-empty",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        resp = await client.get(f"/sla-targets/{target_id}/uptime?window=7d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["uptime_pct"] == 100.0
        assert data["total_samples"] == 0

    @pytest.mark.asyncio
    async def test_uptime_with_samples(self, client: AsyncClient, db: AsyncSession):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "uptime-samples",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        # Insert some uptime samples directly
        from backend.db.repos import UptimeSampleRepo

        datetime.now(timezone.utc)
        for i in range(10):
            await UptimeSampleRepo.create(
                db,
                TEST_ORG_ID,
                target_id=uuid.UUID(target_id),
                up=(i < 9),  # 9 up, 1 down = 90% uptime
                latency_ms=50,
                source="poller",
            )
        await db.commit()

        resp = await client.get(f"/sla-targets/{target_id}/uptime?window=7d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_samples"] == 10
        assert data["up_samples"] == 9
        assert data["uptime_pct"] == 90.0
        # Outage history: the trailing down sample is one (ongoing) episode.
        assert len(data["episodes"]) == 1
        assert data["episodes"][0]["maintenance"] is False
        assert data["episodes"][0]["ended_at"] is None

    @pytest.mark.asyncio
    async def test_slo_status_with_data(self, client: AsyncClient, db: AsyncSession):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "slo-data-target",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        # Create SLO with 95% objective
        slo_resp = await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "95pct",
                "objective_pct": 95.0,
                "window_seconds": 604800,
            },
        )
        slo_id = slo_resp.json()["id"]

        # Insert 100 samples: 90 up, 10 down = 90% uptime (violating 95% SLO)
        from backend.db.repos import UptimeSampleRepo

        for i in range(100):
            await UptimeSampleRepo.create(
                db,
                TEST_ORG_ID,
                target_id=uuid.UUID(target_id),
                up=(i < 90),
                latency_ms=50,
                source="poller",
            )
        await db.commit()

        resp = await client.get(f"/slos/{slo_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["actual_pct"] == 90.0
        assert data["compliant"] is False
        assert data["burn_rate"] > 1.0  # consuming budget faster than allowed

    @pytest.mark.asyncio
    async def test_uptime_maintenance_counts_as_up(
        self, client: AsyncClient, db: AsyncSession
    ):
        """Covered samples count as up without hiding the saved probe result."""
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "uptime-suppressed",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        from backend.db.repos import UptimeSampleRepo

        assert target_resp.status_code == 201
        # 2 up, 1 down, 2 covered down = 80% effective uptime.
        for i in range(2):
            await UptimeSampleRepo.create(
                db,
                TEST_ORG_ID,
                target_id=uuid.UUID(target_id),
                up=True,
                source="poller",
            )
        await UptimeSampleRepo.create(
            db, TEST_ORG_ID, target_id=uuid.UUID(target_id), up=False, source="poller"
        )
        for _ in range(2):
            await UptimeSampleRepo.create(
                db,
                TEST_ORG_ID,
                target_id=uuid.UUID(target_id),
                up=False,
                source="poller",
                suppressed=True,
            )
        await db.commit()

        resp = await client.get(f"/sla-targets/{target_id}/uptime?window=7d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_samples"] == 5
        assert data["up_samples"] == 4
        assert data["uptime_pct"] == 80.0
        assert data["downtime_seconds"] == 60
        assert data["suppressed_seconds"] == 120  # 2 * 60s
        saved = await UptimeSampleRepo.query_window(
            db,
            TEST_ORG_ID,
            uuid.UUID(target_id),
            since=datetime.now(timezone.utc) - timedelta(days=7),
        )
        assert sum(sample.up for sample in saved) == 2
        assert sum(sample.suppressed for sample in saved) == 2

    @pytest.mark.asyncio
    async def test_sla_target_incidents(self, client: AsyncClient, db: AsyncSession):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "target-incidents",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        from backend.db.repos import IncidentRepo

        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="Target Outage", description="Test"
        )
        incident.target_id = uuid.UUID(target_id)
        await db.commit()

        resp = await client.get(f"/sla-targets/{target_id}/incidents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Target Outage"
        assert data[0]["id"] == str(incident.id)


# ======================================================================
# Reliability v1 cleanup - enriched targets, uptime windows, summary, SLO precision
# ======================================================================


class TestReliabilityV1:
    @pytest.mark.asyncio
    async def test_target_url_and_status_in_list_and_detail(
        self, client: AsyncClient, db: AsyncSession
    ):
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "url-visible",
                "kind": "http",
                "config": {"url": "https://shop.example.com/health"},
            },
        )
        target_id = target_resp.json()["id"]

        from backend.db.repos import UptimeSampleRepo

        await UptimeSampleRepo.create(
            db, TEST_ORG_ID, target_id=uuid.UUID(target_id), up=True, source="poller"
        )
        await db.commit()

        # List response carries url, monitor_type, current_status, last_check_at.
        list_resp = await client.get("/sla-targets")
        item = next(t for t in list_resp.json()["items"] if t["id"] == target_id)
        assert item["url"] == "https://shop.example.com/health"
        assert item["monitor_type"] == "https"
        assert item["current_status"] == "up"
        assert item["last_check_at"] is not None
        assert item["uptime_30d_pct"] == 100.0

        # Detail response carries the same enrichment.
        detail = await client.get(f"/sla-targets/{target_id}")
        assert detail.json()["url"] == "https://shop.example.com/health"
        assert detail.json()["current_status"] == "up"

    @pytest.mark.asyncio
    async def test_uptime_windows_and_mtbf(self, client: AsyncClient, db: AsyncSession):
        target_resp = await client.post(
            "/sla-targets", json={"name": "windows", "kind": "http"}
        )
        target_id = target_resp.json()["id"]

        from backend.db.repos import UptimeSampleRepo

        # 8 up + 2 down (two separate down events) → MTBF = 8*60/2 = 240.
        pattern = [True, True, False, True, True, True, False, True, True, True]
        for up in pattern:
            await UptimeSampleRepo.create(
                db, TEST_ORG_ID, target_id=uuid.UUID(target_id), up=up, source="poller"
            )
        await db.commit()

        for window in ("7d", "30d", "365d"):
            resp = await client.get(f"/sla-targets/{target_id}/uptime?window={window}")
            assert resp.status_code == 200, window
            data = resp.json()
            assert data["uptime_pct"] == 80.0
            assert data["down_events"] == 2
            assert data["mtbf_seconds"] == 240.0
            assert isinstance(data["series"], list) and len(data["series"]) > 0

    @pytest.mark.asyncio
    async def test_uptime_custom_range(self, client: AsyncClient, db: AsyncSession):
        target_resp = await client.post(
            "/sla-targets", json={"name": "custom-range", "kind": "http"}
        )
        target_id = target_resp.json()["id"]

        from backend.db.repos import UptimeSampleRepo

        await UptimeSampleRepo.create(
            db, TEST_ORG_ID, target_id=uuid.UUID(target_id), up=True, source="poller"
        )
        await db.commit()

        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=2)).isoformat()
        end = (now + timedelta(minutes=1)).isoformat()
        resp = await client.get(
            f"/sla-targets/{target_id}/uptime",
            params={"start": start, "end": end},
        )
        assert resp.status_code == 200
        assert resp.json()["total_samples"] == 1

        # Inverted range is rejected.
        bad = await client.get(
            f"/sla-targets/{target_id}/uptime",
            params={"start": end, "end": start},
        )
        assert bad.status_code == 400

    @pytest.mark.asyncio
    async def test_response_time_recent_window(
        self, client: AsyncClient, db: AsyncSession
    ):
        target_resp = await client.post(
            "/sla-targets", json={"name": "response-recent", "kind": "http"}
        )
        target_id = uuid.UUID(target_resp.json()["id"])

        from backend.db.models import UptimeSample

        now = datetime.now(timezone.utc)
        db.add_all(
            [
                UptimeSample(
                    org_id=TEST_ORG_ID,
                    target_id=target_id,
                    observed_at=now - timedelta(minutes=10),
                    up=True,
                    latency_ms=100,
                    source="poller",
                ),
                UptimeSample(
                    org_id=TEST_ORG_ID,
                    target_id=target_id,
                    observed_at=now - timedelta(minutes=5),
                    up=True,
                    latency_ms=300,
                    source="poller",
                ),
            ]
        )
        await db.commit()

        resp = await client.get(f"/sla-targets/{target_id}/response-time?window=15m")
        assert resp.status_code == 200
        data = resp.json()
        assert data["avg_latency_ms"] == 200
        assert data["min_latency_ms"] == 100
        assert data["max_latency_ms"] == 300
        assert data["total_samples"] == 2
        assert len(data["series"]) == 15

    @pytest.mark.asyncio
    async def test_response_time_365d_uses_hourly_rollups(
        self, client: AsyncClient, db: AsyncSession
    ):
        target_resp = await client.post(
            "/sla-targets", json={"name": "response-history", "kind": "http"}
        )
        target_id = uuid.UUID(target_resp.json()["id"])

        from backend.db.models import UptimeSample1h

        db.add(
            UptimeSample1h(
                org_id=TEST_ORG_ID,
                target_id=target_id,
                bucket_start=datetime.now(timezone.utc) - timedelta(days=180),
                up_pct=1.0,
                total_samples=60,
                avg_latency_ms=240,
                min_latency_ms=120,
                max_latency_ms=420,
                latency_samples=60,
            )
        )
        await db.commit()

        resp = await client.get(f"/sla-targets/{target_id}/response-time?window=365d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["avg_latency_ms"] == 240
        assert data["min_latency_ms"] == 120
        assert data["max_latency_ms"] == 420
        assert data["total_samples"] == 60
        assert len(data["series"]) == 73

    @pytest.mark.asyncio
    async def test_slo_allows_three_decimal_objective(self, client: AsyncClient):
        target_resp = await client.post(
            "/sla-targets", json={"name": "five-nines", "kind": "http"}
        )
        target_id = target_resp.json()["id"]

        resp = await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "five-nines",
                "objective_pct": 99.999,
                "window_seconds": 2592000,
            },
        )
        assert resp.status_code == 201
        # Must not be rounded to 100.0 or truncated.
        assert resp.json()["objective_pct"] == 99.999

    @pytest.mark.asyncio
    async def test_sla_summary(self, client: AsyncClient, db: AsyncSession):
        from backend.db.repos import UptimeSampleRepo

        up_target = (
            await client.post("/sla-targets", json={"name": "up-t", "kind": "http"})
        ).json()["id"]
        down_target = (
            await client.post("/sla-targets", json={"name": "down-t", "kind": "http"})
        ).json()["id"]
        # A third target with no samples → unknown.
        await client.post("/sla-targets", json={"name": "unknown-t", "kind": "http"})

        await UptimeSampleRepo.create(
            db, TEST_ORG_ID, target_id=uuid.UUID(up_target), up=True, source="poller"
        )
        await UptimeSampleRepo.create(
            db, TEST_ORG_ID, target_id=uuid.UUID(down_target), up=False, source="poller"
        )
        await db.commit()

        resp = await client.get("/sla-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_targets"] == 3
        assert data["targets_up"] == 1
        assert data["targets_down"] == 1
        assert data["targets_unknown"] == 1

    @pytest.mark.asyncio
    async def test_slo_breach_warning_only_no_incident(
        self, app_db, client: AsyncClient, db: AsyncSession
    ):
        """A v1 SLO (no burn threshold) that is breached must NOT create an incident."""
        _, factory, _ = app_db

        target_resp = await client.post(
            "/sla-targets", json={"name": "warn-only", "kind": "http"}
        )
        target_id = target_resp.json()["id"]

        # SLO with NO burn_alert_threshold (the simplified v1 UI path).
        slo_resp = await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "avail",
                "objective_pct": 99.0,
                "window_seconds": 604800,
                "burn_alert_threshold": None,
            },
        )
        assert slo_resp.status_code == 201

        from backend.db.repos import IncidentRepo, UptimeSampleRepo

        # Breach the objective: 50% uptime, well under 99%.
        for i in range(10):
            await UptimeSampleRepo.create(
                db,
                TEST_ORG_ID,
                target_id=uuid.UUID(target_id),
                up=(i < 5),
                source="poller",
            )
        await db.commit()

        # Status reports non-compliant (a warning) ...
        status_resp = await client.get(f"/slos/{slo_resp.json()['id']}/status")
        assert status_resp.json()["compliant"] is False

        # ... but the SLO poller check is a no-op for null-threshold SLOs.
        from backend.sla.poller import SLAPoller

        poller = SLAPoller(factory, config=AppConfig.load())
        await poller._check_slos(TEST_ORG_ID)

        incidents = await IncidentRepo.list_all(db, TEST_ORG_ID)
        assert all("SLO" not in (i.title or "") for i in incidents)


class TestMaintenanceCoverage:
    @pytest.mark.parametrize(
        ("variant", "covered"),
        [
            ("global", True),
            ("service", True),
            ("services", True),
            ("team", True),
            ("teams", True),
            ("unrelated-service", False),
            ("unrelated-team", False),
            ("roster", False),
            ("roster-target", False),
            ("roster-all", False),
            ("legacy-all", True),
            ("legacy-target", True),
            ("pending", False),
            ("expired", False),
            ("recurring", True),
            ("other-workspace", False),
            ("unlinked-global", True),
        ],
    )
    async def test_sample_and_slo_share_maintenance_scope(
        self, variant, covered, app_db, client: AsyncClient
    ):
        from sqlalchemy import select

        from backend.db.models import (
            Incident,
            MaintenanceWindow,
            Organization,
            SLATarget,
        )
        from backend.db.repos import RosterRepo, ServiceRepo, TeamRepo, UptimeSampleRepo
        from backend.sla.poller import SLAPoller

        _, factory, _ = app_db
        async with factory() as db:
            team = await TeamRepo.create(db, TEST_ORG_ID, name="Team", slug="team")
            other_team = await TeamRepo.create(
                db, TEST_ORG_ID, name="Other", slug="other"
            )
            service = await ServiceRepo.create(
                db, TEST_ORG_ID, team_id=team.id, name="Service", slug="service"
            )
            other_service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=other_team.id,
                name="Other service",
                slug="other-service",
            )
            roster = await RosterRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="Roster",
                anchor_date=datetime.now(timezone.utc).date(),
            )
            await db.commit()

        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "maintenance-target",
                "kind": "http",
                "service_id": (
                    None if variant == "unlinked-global" else str(service.id)
                ),
            },
        )
        assert target_resp.status_code == 201
        target_id = uuid.UUID(target_resp.json()["id"])
        now = datetime.now(timezone.utc)
        body = {
            "name": variant,
            "scope_type": "global",
            "starts_at": (now - timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(minutes=5)).isoformat(),
        }
        if variant in {"service", "services", "recurring", "unrelated-service"}:
            body["scope_type"] = "service"
            body["scope_ids"] = (
                [str(other_service.id), str(service.id)]
                if variant == "services"
                else [
                    str(
                        other_service.id
                        if variant == "unrelated-service"
                        else service.id
                    )
                ]
            )
        elif variant in {"team", "teams", "unrelated-team"}:
            body["scope_type"] = "team"
            body["scope_ids"] = (
                [str(other_team.id), str(team.id)]
                if variant == "teams"
                else [str(other_team.id if variant == "unrelated-team" else team.id)]
            )
        elif variant.startswith("roster"):
            body["scope_type"] = "roster"
            if variant == "roster":
                body["scope_id"] = str(roster.id)
            body["target_ids"] = (
                [str(target_id)]
                if variant == "roster-target"
                else ["*"]
                if variant == "roster-all"
                else []
            )
        elif variant.startswith("legacy"):
            body["scope_type"] = "service"
            body["target_ids"] = ["*"] if variant == "legacy-all" else [str(target_id)]
        if variant == "expired":
            body["ends_at"] = now.isoformat()
        elif variant == "recurring":
            body["starts_at"] = (now - timedelta(days=1, minutes=5)).isoformat()
            body["ends_at"] = (
                now - timedelta(days=1) + timedelta(minutes=5)
            ).isoformat()
            body["rrule"] = "FREQ=DAILY;COUNT=2"

        window_resp = await client.post("/maintenance-windows", json=body)
        assert window_resp.status_code == 201
        window_id = uuid.UUID(window_resp.json()["id"])
        assert window_resp.json()["approved"] is True

        async with factory() as db:
            window = await db.get(MaintenanceWindow, window_id)
            assert window.scope_type == body["scope_type"]
            expected_targets = body.get("scope_ids") or (
                [body["scope_id"]] if "scope_id" in body else body.get("target_ids", [])
            )
            assert window.target_ids == expected_targets
            if variant == "pending":
                window.approved = False
            elif variant == "other-workspace":
                other_org = Organization(name="Other", slug="other-workspace")
                db.add(other_org)
                await db.flush()
                window.org_id = other_org.id
            # A pre-window outage still burns the SLO budget during maintenance.
            sample = await UptimeSampleRepo.create(
                db, TEST_ORG_ID, target_id=target_id, up=False
            )
            sample.observed_at = now - timedelta(minutes=10)
            target = await db.get(SLATarget, target_id)
            await db.commit()

        slo_resp = await client.post(
            "/slos",
            json={
                "target_id": str(target_id),
                "name": "Maintenance burn",
                "objective_pct": 99,
                "window_seconds": 3600,
                "burn_alert_threshold": 2,
            },
        )
        assert slo_resp.status_code == 201

        poller = SLAPoller(factory, AppConfig.load())
        with (
            patch.object(poller, "_probe_target", AsyncMock(return_value=(False, 37))),
            patch(
                "backend.sla.poller.page_new_incident", new_callable=AsyncMock
            ) as page,
            patch("backend.sla.poller.should_auto_start_session", return_value=False),
        ):
            await poller._probe_and_record(TEST_ORG_ID, target)
            await poller._check_slos(TEST_ORG_ID)

            response = await client.get(f"/sla-targets/{target_id}/uptime?window=7d")
            assert response.status_code == 200
            assert response.json()["uptime_pct"] == (50.0 if covered else 0.0)
            assert response.json()["up_samples"] == int(covered)
            async with factory() as db:
                samples = await UptimeSampleRepo.query_window(
                    db, TEST_ORG_ID, target_id, since=now - timedelta(hours=1)
                )
                assert len(samples) == 2
                assert samples[-1].up is False
                assert samples[-1].latency_ms == 37
                assert samples[-1].suppressed is covered
                incidents = list((await db.scalars(select(Incident))).all())
                assert len(incidents) == (0 if covered else 1)
                if incidents:
                    assert incidents[0].target_id == target_id
                    assert (
                        incidents[0].external_source == f"slo:{slo_resp.json()['id']}"
                    )
            assert page.await_count == (0 if covered else 1)

            # At the exclusive end boundary, a new failed probe and SLO alert
            # behave normally. The recurring window's second occurrence ends then.
            after = now + timedelta(minutes=5)
            with patch("backend.sla.poller.datetime", wraps=datetime) as clock:
                clock.now.return_value = after
                await poller._probe_and_record(TEST_ORG_ID, target)
                await poller._check_slos(TEST_ORG_ID)
            async with factory() as db:
                samples = await UptimeSampleRepo.query_window(
                    db, TEST_ORG_ID, target_id, since=now - timedelta(hours=1)
                )
                assert len(samples) == 3
                assert samples[-1].suppressed is False
                incidents = list((await db.scalars(select(Incident))).all())
                assert len(incidents) == 1
            assert page.await_count == 1


@pytest.mark.integration
class TestMaintenanceCoveragePostgres(TestMaintenanceCoverage):
    @pytest.fixture
    async def app_db(self, monkeypatch):
        """Exercise the same HTTP and saved-state contract on disposable Postgres."""
        from sqlalchemy.engine import make_url

        from backend.api import deps
        from backend.db.models import Organization

        url = os.environ.get("PART4_PG_URL")
        if not url:
            pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
        if make_url(url).database == "testdb":
            pytest.fail("The owner's database must never be used for integration tests")
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(deps, "_session_factory", factory)
        app = create_app(AppConfig.load())
        app.state.session_factory = factory
        async with factory() as db:
            db.add(Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org"))
            await db.flush()
            admin = User(
                username="maintenance-admin",
                email="maintenance-admin@example.test",
                password_hash="unused",
                role="admin",
                primary_org_id=TEST_ORG_ID,
            )
            db.add(admin)
            await db.commit()
        token = create_access_token(admin.id, "admin")
        try:
            yield app, factory, token
        finally:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
            await engine.dispose()


class TestMaintenanceTargetScope:
    @pytest.mark.parametrize("operation", ["create", "update"])
    @pytest.mark.parametrize(
        "variant",
        [
            "global-specific",
            "global-mixed",
            "global-scope-ids",
            "malformed",
            "unknown",
            "other-workspace",
            "overridden-invalid",
        ],
    )
    async def test_invalid_targets_do_not_write(
        self, operation, variant, app_db, client
    ):
        from sqlalchemy import select
        from backend.db.models import MaintenanceWindow, Organization
        from backend.db.repos import SLATargetRepo, ServiceRepo, TeamRepo

        _, factory, _ = app_db
        target_response = await client.post(
            "/sla-targets", json={"name": "Scoped probe", "kind": "external"}
        )
        assert target_response.status_code == 201
        target_id = target_response.json()["id"]
        async with factory() as db:
            team = await TeamRepo.create(
                db, TEST_ORG_ID, name="Team", slug="scope-team"
            )
            service = await ServiceRepo.create(
                db, TEST_ORG_ID, team_id=team.id, name="Service", slug="scope-service"
            )
            foreign_org = Organization(name="Foreign", slug="foreign-scope")
            db.add(foreign_org)
            await db.flush()
            foreign_target = await SLATargetRepo.create(
                db, foreign_org.id, name="Foreign probe", kind="external"
            )
            await db.commit()
        now = datetime.now(timezone.utc)
        original = {
            "name": "Original window",
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
            "scope_type": "global",
            "target_ids": ["*"],
        }
        if operation == "update":
            response = await client.post("/maintenance-windows", json=original)
            assert response.status_code == 201
            window_id = uuid.UUID(response.json()["id"])
        body = {**original, "name": "Rejected change", "scope_type": "service"}
        if variant.startswith("global"):
            body["scope_type"] = "global"
            body["target_ids"] = (
                [target_id] if variant == "global-specific" else ["*", target_id]
            )
            if variant == "global-scope-ids":
                body["target_ids"] = ["*"]
                body["scope_ids"] = [str(service.id)]
        else:
            body["target_ids"] = [
                "not-a-uuid"
                if variant == "malformed"
                else str(foreign_target.id)
                if variant == "other-workspace"
                else str(uuid.uuid4())
            ]
            if variant == "overridden-invalid":
                body["scope_ids"] = [str(service.id)]
        response = await (
            client.post("/maintenance-windows", json=body)
            if operation == "create"
            else client.put(f"/maintenance-windows/{window_id}", json=body)
        )
        assert response.status_code == 422
        async with factory() as db:
            windows = list((await db.scalars(select(MaintenanceWindow))).all())
            assert len(windows) == (0 if operation == "create" else 1)
            if windows:
                assert windows[0].name == original["name"]
                assert windows[0].scope_type == "global"
                assert windows[0].target_ids == ["*"]
                assert windows[0].scope_id is None

    @pytest.mark.parametrize("variant", ["rename-legacy-global", "widen-service"])
    async def test_update_validates_the_resulting_global_scope(
        self, variant, app_db, client
    ):
        from backend.db.repos import MaintenanceWindowRepo, SLATargetRepo

        _, factory, _ = app_db
        now = datetime.now(timezone.utc)
        async with factory() as db:
            target = await SLATargetRepo.create(
                db, TEST_ORG_ID, name="Legacy", kind="external"
            )
            window = await MaintenanceWindowRepo.create(
                db,
                TEST_ORG_ID,
                name="Legacy window",
                starts_at=now,
                ends_at=now + timedelta(hours=1),
                target_ids=[str(target.id)],
                scope_type="global" if variant == "rename-legacy-global" else "service",
            )
            original_scope = window.scope_type
            await db.commit()
        body = (
            {"name": "Changed"}
            if variant == "rename-legacy-global"
            else {"scope_type": "global"}
        )
        response = await client.put(f"/maintenance-windows/{window.id}", json=body)
        assert response.status_code == 422
        async with factory() as db:
            saved = await MaintenanceWindowRepo.get_by_id(db, TEST_ORG_ID, window.id)
            assert saved.name == "Legacy window"
            assert saved.scope_type == original_scope
            assert saved.target_ids == [str(target.id)]

    @pytest.mark.parametrize("selection", ["linked", "standalone", "all"])
    async def test_selected_scope_keeps_unrelated_intake_and_pages_active(
        self, selection, app_db, client
    ):
        from sqlalchemy import select
        from backend.db.models import (
            Incident,
            IngestLog,
            MaintenanceWindow,
            UptimeSample,
        )
        from backend.db.repos import IncidentPageRepo, IncidentRepo, SLATargetRepo
        from backend.paging.dispatch import DeliveryAttempt, dispatch_page
        from backend.sla.poller import SLAPoller

        _, factory, _ = app_db
        team_response = await client.post(
            "/teams", json={"name": "Scope team", "slug": "scope-team"}
        )
        assert team_response.status_code == 201
        services = []
        for index in range(2):
            response = await client.post(
                "/services",
                json={
                    "name": f"Service {index}",
                    "slug": f"scope-{index}",
                    "team_id": team_response.json()["id"],
                    "priority": "P1",
                },
            )
            assert response.status_code == 201
            services.append(response.json())
        target_response = await client.post(
            "/sla-targets",
            json={
                "name": "Selected probe",
                "kind": "external",
                "service_id": services[0]["id"] if selection == "linked" else None,
            },
        )
        assert target_response.status_code == 201
        target_id = uuid.UUID(target_response.json()["id"])
        now = datetime.now(timezone.utc)
        response = await client.post(
            "/maintenance-windows",
            json={
                "name": "Selected window",
                "starts_at": (now - timedelta(minutes=1)).isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
                "scope_type": "global" if selection == "all" else "service",
                "scope_ids": [services[0]["id"]] if selection == "linked" else [],
                "target_ids": ["*"] if selection == "all" else [str(target_id)],
            },
        )
        assert response.status_code == 201
        window_id = uuid.UUID(response.json()["id"])
        async with factory() as db:
            saved = await db.get(MaintenanceWindow, window_id)
            assert saved.scope_type == ("global" if selection == "all" else "service")
            assert saved.scope_id == (
                uuid.UUID(services[0]["id"]) if selection == "linked" else None
            )
            assert saved.target_ids == (
                [services[0]["id"]]
                if selection == "linked"
                else ["*"]
                if selection == "all"
                else [str(target_id)]
            )

        with (
            patch(
                "backend.api.routes.ingest.dispatch_incident_created",
                new_callable=AsyncMock,
            ),
            patch(
                "backend.ingest.service.choose_model_for_incident_service",
                AsyncMock(return_value=None),
            ),
        ):
            for index, service in enumerate(services):
                intake = await client.post(
                    service["intake_url"],
                    json={
                        "title": f"Intake {index}",
                        "description": "Scope regression",
                        "severity": "high",
                        "external_id": f"scope-{index}",
                    },
                )
                assert intake.status_code == 200
                covered = selection == "all" or (selection == "linked" and index == 0)
                assert intake.json()["dedup_action"] == (
                    "skipped" if covered else "created"
                )
                assert (intake.json()["incident_id"] is None) is covered
        async with factory() as db:
            incidents = list((await db.scalars(select(Incident))).all())
            assert {row.title for row in incidents} == (
                set()
                if selection == "all"
                else {"Intake 1"}
                if selection == "linked"
                else {"Intake 0", "Intake 1"}
            )
            logs = list((await db.scalars(select(IngestLog))).all())
            assert len(logs) == 2
            assert sum(row.dedup_action == "skipped" for row in logs) == (
                2 if selection == "all" else 1 if selection == "linked" else 0
            )
            user = (await db.scalars(select(User))).one()
            for index, service in enumerate(services):
                incident = await IncidentRepo.create(
                    db,
                    TEST_ORG_ID,
                    title=f"Existing page {index}",
                    description="Scope regression",
                    service_id=uuid.UUID(service["id"]),
                    priority="P1",
                    response_mode="page",
                )
                page = await IncidentPageRepo.create(
                    db,
                    TEST_ORG_ID,
                    incident_id=incident.id,
                    user_id=user.id,
                    channel="recorded",
                    delivery_status="recorded",
                )
                fake_channel = AsyncMock()
                fake_channel.key = "email"
                fake_channel.send.return_value = DeliveryAttempt(
                    channel="email", status="sent"
                )
                result = await dispatch_page(
                    db,
                    TEST_ORG_ID,
                    incident=incident,
                    user=user,
                    page=page,
                    channel_factory=lambda key: fake_channel,
                    at=now,
                )
                await db.commit()
                covered = selection == "all" or (selection == "linked" and index == 0)
                assert result.suppressed is covered
                assert fake_channel.send.await_count == (0 if covered else 1)
                pages = await IncidentPageRepo.list_for_incident(
                    db, TEST_ORG_ID, incident.id
                )
                assert len(pages) == 2
                assert (
                    sum(
                        row.delivery_status == ("skipped" if covered else "sent")
                        for row in pages
                    )
                    == 1
                )
                assert incident.suppressed_by_maintenance_window_id == (
                    window_id if covered else None
                )
            target = await SLATargetRepo.get_by_id(db, TEST_ORG_ID, target_id)
        poller = SLAPoller(factory, AppConfig.load())
        with patch.object(poller, "_probe_target", AsyncMock(return_value=(False, 37))):
            await poller._probe_and_record(TEST_ORG_ID, target)
        async with factory() as db:
            sample = (await db.scalars(select(UptimeSample))).one()
            assert sample.target_id == target_id
            assert sample.up is False and sample.suppressed is True

    @pytest.mark.parametrize("selection", ["standalone", "all"])
    async def test_update_clears_the_old_service_scope(self, selection, app_db, client):
        from backend.db.repos import MaintenanceWindowRepo, ServiceRepo, TeamRepo

        _, factory, _ = app_db
        async with factory() as db:
            team = await TeamRepo.create(
                db, TEST_ORG_ID, name="Team", slug="scope-team"
            )
            service = await ServiceRepo.create(
                db, TEST_ORG_ID, team_id=team.id, name="Service", slug="scope-service"
            )
            await db.commit()
        target_response = await client.post(
            "/sla-targets", json={"name": "Standalone", "kind": "external"}
        )
        assert target_response.status_code == 201
        now = datetime.now(timezone.utc)
        original = await client.post(
            "/maintenance-windows",
            json={
                "name": "Original",
                "starts_at": now.isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
                "scope_type": "service",
                "scope_ids": [str(service.id)],
            },
        )
        assert original.status_code == 201
        ids = ["*"] if selection == "all" else [target_response.json()["id"]]
        response = await client.put(
            f"/maintenance-windows/{original.json()['id']}",
            json={
                "scope_type": "global" if selection == "all" else "service",
                "scope_ids": [],
                "target_ids": ids,
            },
        )
        assert response.status_code == 200
        assert response.json()["target_ids"] == ids
        assert response.json()["scope_id"] is None
        async with factory() as db:
            saved = await MaintenanceWindowRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(original.json()["id"])
            )
            assert saved.target_ids == ids
            assert saved.scope_id is None
            assert saved.scope_type == ("global" if selection == "all" else "service")


@pytest.mark.integration
class TestMaintenanceTargetScopePostgres(TestMaintenanceTargetScope):
    app_db = TestMaintenanceCoveragePostgres.app_db


class TestSLATargetServiceLink:
    """v1.2 Phase 6 - SLA target ↔ Service linkage + SLO recommendations."""

    async def _seed_service(self, db: AsyncSession) -> uuid.UUID:
        from backend.db.models import Service, Team

        team = Team(org_id=TEST_ORG_ID, name="Payments", slug="payments")
        db.add(team)
        await db.flush()
        service = Service(
            org_id=TEST_ORG_ID, team_id=team.id, name="Checkout", slug="checkout"
        )
        db.add(service)
        await db.commit()
        return service.id

    @pytest.mark.asyncio
    async def test_create_target_with_service_resolves_names(
        self, client: AsyncClient, db: AsyncSession
    ):
        service_id = await self._seed_service(db)
        resp = await client.post(
            "/sla-targets",
            json={"name": "api", "kind": "http", "service_id": str(service_id)},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["service_id"] == str(service_id)
        assert data["service_name"] == "Checkout"
        assert data["team_name"] == "Payments"

    @pytest.mark.asyncio
    async def test_create_target_rejects_unknown_service(self, client: AsyncClient):
        resp = await client.post(
            "/sla-targets",
            json={"name": "api", "kind": "http", "service_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_recommendations_for_breaching_linked_slo(
        self, client: AsyncClient, db: AsyncSession
    ):
        service_id = await self._seed_service(db)
        target_id = (
            await client.post(
                "/sla-targets",
                json={"name": "api", "kind": "http", "service_id": str(service_id)},
            )
        ).json()["id"]
        await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "avail",
                "objective_pct": 99.0,
                "window_seconds": 3600,
            },
        )
        # Breach hard: mostly-down window.
        from backend.db.repos import UptimeSampleRepo

        for i in range(10):
            await UptimeSampleRepo.create(
                db,
                TEST_ORG_ID,
                target_id=uuid.UUID(target_id),
                up=(i < 2),
                source="poller",
            )
        await db.commit()

        resp = await client.get("/sla-recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        rec = data["items"][0]
        assert rec["severity"] == "critical"
        assert rec["service_name"] == "Checkout"
        assert rec["team_name"] == "Payments"
        assert any("Checkout" in a for a in rec["actions"])

    @pytest.mark.asyncio
    async def test_recommendations_empty_when_healthy(
        self, client: AsyncClient, db: AsyncSession
    ):
        target_id = (
            await client.post("/sla-targets", json={"name": "ok", "kind": "http"})
        ).json()["id"]
        await client.post(
            "/slos",
            json={
                "target_id": target_id,
                "name": "avail",
                "objective_pct": 99.0,
                "window_seconds": 3600,
            },
        )
        from backend.db.repos import UptimeSampleRepo

        for _ in range(10):
            await UptimeSampleRepo.create(
                db,
                TEST_ORG_ID,
                target_id=uuid.UUID(target_id),
                up=True,
                source="poller",
            )
        await db.commit()

        resp = await client.get("/sla-recommendations")
        assert resp.json()["total"] == 0
