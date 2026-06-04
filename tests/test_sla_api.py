"""Tests for SLA / SLO / Maintenance Window CRUD APIs (Sprint 25)."""

from __future__ import annotations

import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.auth import create_access_token, hash_password
from backend.config_loader import AppConfig
from backend.db.models import Base, User


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

        now = datetime.now(timezone.utc)
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
    async def test_uptime_suppressed_excluded(
        self, client: AsyncClient, db: AsyncSession
    ):
        """Suppressed samples should be excluded from uptime calculation."""
        target_resp = await client.post(
            "/sla-targets",
            json={
                "name": "uptime-suppressed",
                "kind": "http",
            },
        )
        target_id = target_resp.json()["id"]

        from backend.db.repos import UptimeSampleRepo

        # 8 up, 2 down but suppressed = 100% effective uptime
        for i in range(8):
            await UptimeSampleRepo.create(
                db,
                TEST_ORG_ID,
                target_id=uuid.UUID(target_id),
                up=True,
                source="poller",
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
        data = resp.json()
        assert data["total_samples"] == 10
        assert data["up_samples"] == 8
        assert data["uptime_pct"] == 100.0  # all non-suppressed are up
        assert data["suppressed_seconds"] == 120  # 2 * 60s

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
# Reliability v1 cleanup — enriched targets, uptime windows, summary, SLO precision
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
    async def test_uptime_windows_and_mtbf(
        self, client: AsyncClient, db: AsyncSession
    ):
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
            resp = await client.get(
                f"/sla-targets/{target_id}/uptime?window={window}"
            )
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
                db, TEST_ORG_ID, target_id=uuid.UUID(target_id), up=(i < 5),
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
