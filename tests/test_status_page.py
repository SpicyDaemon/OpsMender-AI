from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.api.routes import status_page as status_routes
from backend.config_loader import set_env_path
from backend.db.models import Base, Organization, UptimeSample1h
from backend.db.repos import (
    AuditEntryRepo,
    IncidentRepo,
    MaintenanceWindowRepo,
    ServiceRepo,
    SLATargetRepo,
    SLORepo,
    StatusPageSubscriberRepo,
    TeamRepo,
    UserRepo,
)
from backend.ingest.rate_limiter import IngestRateLimiter


TEST_ORG_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def clear_status_page_cache():
    status_routes._STATUS_CACHE.clear()
    yield
    status_routes._STATUS_CACHE.clear()


@pytest.fixture
async def app(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)
    async with factory() as db:
        db.add(Organization(id=TEST_ORG_ID, name="Status Org", slug="status-org"))
        await db.commit()

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        "OPSMENDER_DATABASE_URL=sqlite+aiosqlite://\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)
    application = create_app()
    application.state.session_factory = factory
    application.dependency_overrides[get_db] = _override_get_db(factory)

    yield application

    set_env_path(None)
    pending = list(getattr(application.state, "session_tasks", set())) + list(
        getattr(application.state, "background_tasks", set())
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    await engine.dispose()


def _override_get_db(factory):
    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _get_db


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _register_login(
    client: AsyncClient,
    app,
    *,
    username: str,
    email: str,
    role: str,
) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "securepass123",
            "role": role,
        },
    )
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, username)
        assert user is not None
        user.primary_org_id = TEST_ORG_ID
        await db.commit()
    resp = await client.post(
        "/auth/login",
        json={"username": username, "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(client: AsyncClient, app) -> dict[str, str]:
    return await _register_login(
        client,
        app,
        username="statusadmin",
        email="status-admin@example.com",
        role="admin",
    )


@pytest.fixture
async def operator_headers(client: AsyncClient, app, admin_headers) -> dict[str, str]:
    return await _register_login(
        client,
        app,
        username="statusoperator",
        email="status-operator@example.com",
        role="operator",
    )


async def _seed_service_and_incident(app, *, priority: str = "P0"):
    async with app.state.session_factory() as db:
        suffix = uuid.uuid4().hex[:8]
        team = await TeamRepo.create(
            db,
            TEST_ORG_ID,
            name=f"Status Team {suffix}",
            slug=f"status-team-{suffix}",
        )
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name=f"Checkout {suffix}",
            slug=f"checkout-{suffix}",
            priority=priority,
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title=f"Checkout degraded {suffix}",
            description="Checkout errors are elevated.",
            priority=priority,
            service_id=service.id,
        )
        await db.commit()
        return service, incident


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_disabled_private_and_public_status_page(client, admin_headers):
    disabled = await client.get("/api/v1/status")
    assert disabled.status_code == 404

    patch = await client.patch(
        "/api/v1/status-page/settings",
        headers=admin_headers,
        json={"enabled": True, "visibility": "private", "title": "Ops Status"},
    )
    assert patch.status_code == 200
    assert patch.json()["enabled"] is True

    private_anon = await client.get("/api/v1/status")
    assert private_anon.status_code == 404

    private_auth = await client.get("/api/v1/status", headers=admin_headers)
    assert private_auth.status_code == 200
    assert private_auth.json()["title"] == "Ops Status"

    await client.patch(
        "/api/v1/status-page/settings",
        headers=admin_headers,
        json={"visibility": "public"},
    )
    public_first = await client.get("/api/v1/status")
    assert public_first.status_code == 200
    assert public_first.headers["x-status-page-cache"] == "miss"
    public_second = await client.get("/api/v1/status")
    assert public_second.status_code == 200
    assert public_second.headers["x-status-page-cache"] == "hit"


@pytest.mark.asyncio
async def test_privacy_gate_publish_status_and_audit(
    client,
    app,
    admin_headers,
    operator_headers,
):
    service, incident = await _seed_service_and_incident(app, priority="P0")
    await client.patch(
        "/api/v1/status-page/settings",
        headers=admin_headers,
        json={"enabled": True, "visibility": "public"},
    )
    await client.put(
        "/api/v1/status-page/components",
        headers=admin_headers,
        json={"components": [{"service_id": str(service.id)}]},
    )

    before_publish = (await client.get("/api/v1/status")).json()
    assert before_publish["active_incidents"] == []
    assert before_publish["components"][0]["status"] == "operational"

    published = await client.post(
        f"/api/v1/incidents/{incident.id}/status-updates",
        headers=operator_headers,
        json={
            "state": "investigating",
            "body": "We are investigating checkout errors. https://example.com/runbook",
        },
    )
    assert published.status_code == 201

    after_publish = (await client.get("/api/v1/status")).json()
    assert after_publish["overall_status"] == "major_outage"
    assert after_publish["components"][0]["status"] == "major_outage"
    assert after_publish["active_incidents"][0]["id"] == str(incident.id)

    timeline = await client.get(f"/incidents/{incident.id}/timeline", headers=operator_headers)
    assert timeline.status_code == 200
    assert any(
        item["event_type"] == "status_page_update"
        for item in timeline.json()["items"]
    )

    async with app.state.session_factory() as db:
        entries = await AuditEntryRepo.query(
            db,
            TEST_ORG_ID,
            entry_type="status_page_change",
            limit=20,
        )
    assert {entry.tool_name for entry in entries} >= {
        "status_page.settings",
        "status_page.components",
        "status_page.update.publish",
    }
    assert all(entry.session_id is None for entry in entries)


@pytest.mark.asyncio
async def test_resolved_update_moves_incident_to_recently_resolved(
    client,
    app,
    admin_headers,
    operator_headers,
):
    service, incident = await _seed_service_and_incident(app, priority="P1")
    await client.patch(
        "/api/v1/status-page/settings",
        headers=admin_headers,
        json={"enabled": True, "visibility": "public"},
    )
    await client.put(
        "/api/v1/status-page/components",
        headers=admin_headers,
        json={"components": [{"service_id": str(service.id)}]},
    )
    await client.post(
        f"/api/v1/incidents/{incident.id}/status-updates",
        headers=operator_headers,
        json={"state": "identified", "body": "Impact identified."},
    )
    await client.post(
        f"/api/v1/incidents/{incident.id}/status-updates",
        headers=operator_headers,
        json={"state": "resolved", "body": "Impact resolved."},
    )

    payload = (await client.get("/api/v1/status")).json()
    assert payload["overall_status"] == "operational"
    assert payload["active_incidents"] == []
    assert payload["recently_resolved"][0]["id"] == str(incident.id)


@pytest.mark.asyncio
async def test_maintenance_and_uptime_bars(client, app, admin_headers):
    service, _incident = await _seed_service_and_incident(app, priority="P2")
    await client.patch(
        "/api/v1/status-page/settings",
        headers=admin_headers,
        json={"enabled": True, "visibility": "public"},
    )
    await client.put(
        "/api/v1/status-page/components",
        headers=admin_headers,
        json={"components": [{"service_id": str(service.id)}]},
    )
    now = datetime.now(timezone.utc)
    async with app.state.session_factory() as db:
        await MaintenanceWindowRepo.create(
            db,
            TEST_ORG_ID,
            name="Checkout maintenance",
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(minutes=55),
            scope_type="service",
            scope_id=service.id,
            approved=True,
        )
        target = await SLATargetRepo.create(
            db,
            TEST_ORG_ID,
            name="Checkout HTTP",
            kind="http",
            config={},
            service_id=service.id,
        )
        await SLORepo.create(
            db,
            TEST_ORG_ID,
            target_id=target.id,
            name="Checkout availability",
            objective_pct=99.9,
            window_seconds=30 * 24 * 60 * 60,
        )
        db.add(
            UptimeSample1h(
                org_id=TEST_ORG_ID,
                target_id=target.id,
                bucket_start=now - timedelta(hours=1),
                up_pct=0.98765,
                total_samples=60,
            )
        )
        await db.commit()

    payload = (await client.get("/api/v1/status")).json()
    component = payload["components"][0]
    assert component["status"] == "maintenance"
    assert payload["overall_status"] == "maintenance"
    assert component["uptime_90d"][-1]["pct"] == 98.765


@pytest.mark.asyncio
async def test_subscribe_confirm_and_unsubscribe(client, app, admin_headers):
    await client.patch(
        "/api/v1/status-page/settings",
        headers=admin_headers,
        json={"enabled": True, "visibility": "public"},
    )

    subscribe = await client.post(
        "/api/v1/status/subscribe",
        json={"email": "Person@Example.com"},
    )
    assert subscribe.status_code == 202
    async with app.state.session_factory() as db:
        created = await StatusPageSubscriberRepo.get_by_email(
            db, TEST_ORG_ID, "person@example.com"
        )
    assert created is not None
    assert created.confirmed_at is None

    confirm_token = "known-confirm-token"
    unsubscribe_token = "known-unsubscribe-token"
    async with app.state.session_factory() as db:
        await StatusPageSubscriberRepo.create(
            db,
            TEST_ORG_ID,
            email="known@example.com",
            confirm_token_hash=_token_hash(confirm_token),
            unsubscribe_token_hash=_token_hash(unsubscribe_token),
        )
        await db.commit()

    confirmed = await client.get(f"/api/v1/status/confirm?token={confirm_token}")
    assert confirmed.status_code == 200
    async with app.state.session_factory() as db:
        row = await StatusPageSubscriberRepo.get_by_email(
            db, TEST_ORG_ID, "known@example.com"
        )
    assert row is not None
    assert row.confirmed_at is not None

    unsubscribed = await client.get(
        f"/api/v1/status/unsubscribe?token={unsubscribe_token}"
    )
    assert unsubscribed.status_code == 200
    async with app.state.session_factory() as db:
        row = await StatusPageSubscriberRepo.get_by_email(
            db, TEST_ORG_ID, "known@example.com"
        )
    assert row is None


@pytest.mark.asyncio
async def test_public_status_reads_are_rate_limited(client, app, admin_headers):
    await client.patch(
        "/api/v1/status-page/settings",
        headers=admin_headers,
        json={"enabled": True, "visibility": "public"},
    )
    app.state.status_page_limiter = IngestRateLimiter(max_requests=1, window_seconds=60)

    first = await client.get("/api/v1/status")
    assert first.status_code == 200
    second = await client.get("/api/v1/status")
    assert second.status_code == 429
