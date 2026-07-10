from __future__ import annotations

import csv
import io
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import (
    Base,
    Incident,
    IngestLog,
    IngestToken,
    Organization,
    Service,
    Team,
)
from backend.reports.analytics import build_response_report

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "analytics-test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=ORG_ID, name="Analytics", slug="analytics"))
        await session.commit()

    set_session_factory(factory)
    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        "OPSMENDER_MCP_SERVERS_JSON=[]\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory
    application.dependency_overrides[get_db] = _override_get_db(factory)
    yield application

    set_env_path(None)
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


async def _headers(client: AsyncClient, role: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:6]
    username = f"analytics-{role}-{suffix}"
    await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": f"{username}@test.com",
            "password": "securepass123",
            "role": role,
        },
    )
    resp = await client.post(
        "/auth/login",
        json={"username": username, "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(client: AsyncClient) -> dict[str, str]:
    return await _headers(client, "admin")


@pytest.fixture
async def viewer_headers(client: AsyncClient) -> dict[str, str]:
    return await _headers(client, "viewer")


async def _seed_known_data(app):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    async with app.state.session_factory() as db:
        team = Team(org_id=ORG_ID, name="Analytics Team", slug="analytics-team")
        db.add(team)
        await db.flush()
        service_a = Service(
            org_id=ORG_ID,
            team_id=team.id,
            name="Checkout",
            slug="checkout",
            priority="P1",
        )
        service_b = Service(
            org_id=ORG_ID,
            team_id=team.id,
            name="Search",
            slug="search",
            priority="P0",
        )
        db.add_all([service_a, service_b])
        await db.flush()
        token_a = IngestToken(
            org_id=ORG_ID,
            name="analytics-a",
            provider="auto",
            token_hash="hash-a",
            service_id=service_a.id,
        )
        token_b = IngestToken(
            org_id=ORG_ID,
            name="analytics-b",
            provider="auto",
            token_hash="hash-b",
            service_id=service_b.id,
        )
        db.add_all([token_a, token_b])
        await db.flush()

        incident_a1 = Incident(
            org_id=ORG_ID,
            title="Checkout 503",
            description="first",
            status="resolved",
            priority="P1",
            service_id=service_a.id,
            correlated_count=2,
            flapping=True,
            created_at=base + timedelta(hours=1),
            acknowledged_at=base + timedelta(hours=1, minutes=5),
            updated_at=base + timedelta(hours=1, minutes=20),
        )
        incident_b = Incident(
            org_id=ORG_ID,
            title="Search latency",
            description="open",
            status="open",
            priority="P0",
            service_id=service_b.id,
            created_at=base + timedelta(hours=4),
            acknowledged_at=base + timedelta(hours=4, minutes=15),
            updated_at=base + timedelta(hours=4, minutes=30),
        )
        incident_a2 = Incident(
            org_id=ORG_ID,
            title="Checkout dependency",
            description="second week",
            status="resolved",
            priority="P2",
            service_id=service_a.id,
            created_at=base + timedelta(days=8),
            acknowledged_at=base + timedelta(days=8, minutes=10),
            updated_at=base + timedelta(days=8, minutes=40),
        )
        db.add_all([incident_a1, incident_b, incident_a2])
        await db.flush()

        logs = [
            IngestLog(
                org_id=ORG_ID,
                ingest_token_id=token_a.id,
                provider="auto",
                raw_payload={"n": 1},
                incident_id=incident_a1.id,
                dedup_action="created",
                created_at=base + timedelta(hours=1),
            ),
            IngestLog(
                org_id=ORG_ID,
                ingest_token_id=token_a.id,
                provider="auto",
                raw_payload={"n": 2},
                incident_id=incident_a1.id,
                dedup_action="updated",
                created_at=base + timedelta(hours=2),
            ),
            IngestLog(
                org_id=ORG_ID,
                ingest_token_id=token_a.id,
                provider="auto",
                raw_payload={"n": 3},
                incident_id=incident_a1.id,
                dedup_action="skipped",
                created_at=base + timedelta(hours=3),
            ),
            IngestLog(
                org_id=ORG_ID,
                ingest_token_id=token_b.id,
                provider="auto",
                raw_payload={"n": 4},
                incident_id=incident_b.id,
                dedup_action="created",
                created_at=base + timedelta(hours=4),
            ),
            IngestLog(
                org_id=ORG_ID,
                ingest_token_id=token_b.id,
                provider="auto",
                raw_payload={"n": 5},
                incident_id=incident_b.id,
                dedup_action="updated",
                created_at=base + timedelta(hours=5),
            ),
            IngestLog(
                org_id=ORG_ID,
                ingest_token_id=token_a.id,
                provider="auto",
                raw_payload={"n": 6},
                incident_id=incident_a2.id,
                dedup_action="created",
                created_at=base + timedelta(days=8),
            ),
        ]
        db.add_all(logs)
        await db.commit()
        return service_a.id, service_b.id


def _csv_metric_rows(content: str) -> dict[str, str]:
    rows = list(csv.reader(io.StringIO(content)))
    metrics: dict[str, str] = {}
    in_metrics = False
    for row in rows:
        if row == ["metric", "value"]:
            in_metrics = True
            continue
        if in_metrics and not row:
            break
        if in_metrics and len(row) >= 2:
            metrics[row[0]] = row[1]
    return metrics


async def test_noise_and_response_analytics_exact_values(
    client: AsyncClient,
    app,
    admin_headers,
):
    service_a, _service_b = await _seed_known_data(app)
    params = "from=2026-07-01T00:00:00Z&to=2026-07-20T00:00:00Z"

    noise = await client.get(f"/api/v1/analytics/noise?{params}", headers=admin_headers)
    assert noise.status_code == 200, noise.text
    noise_body = noise.json()
    assert noise_body["inbound_alerts"] == 6
    assert noise_body["incidents_created"] == 3
    assert noise_body["dedup_breakdown"] == {"created": 3, "updated": 2, "skipped": 1}
    assert noise_body["noise_reduction_ratio"] == 0.5
    assert noise_body["grouped_alert_savings"] == 2
    assert noise_body["flapping_incident_count"] == 1
    assert noise_body["alerts_by_hour_utc"][1]["alerts"] == 1
    assert noise_body["top_noisy_services"][0]["service_name"] == "Checkout"

    response = await client.get(
        f"/api/v1/analytics/response?{params}",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    response_body = response.json()
    assert response_body["overall"]["incident_count"] == 3
    assert response_body["overall"]["mtta_seconds"] == 600
    assert response_body["overall"]["mttr_seconds"] == 1800
    p1 = next(row for row in response_body["per_priority"] if row["priority"] == "P1")
    assert p1["mtta_seconds"] == 300
    assert p1["mttr_seconds"] == 1200
    assert [row["week_start"] for row in response_body["weekly_trend"]] == [
        "2026-06-29",
        "2026-07-06",
    ]

    filtered = await client.get(
        f"/api/v1/analytics/response?{params}&service_id={service_a}",
        headers=admin_headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["overall"]["incident_count"] == 2
    assert filtered.json()["overall"]["mtta_seconds"] == 450


async def test_analytics_csv_matches_json_metrics(client, app, admin_headers):
    await _seed_known_data(app)
    params = "from=2026-07-01T00:00:00Z&to=2026-07-20T00:00:00Z"
    noise_json = (
        await client.get(f"/api/v1/analytics/noise?{params}", headers=admin_headers)
    ).json()
    noise_csv = await client.get(
        f"/api/v1/analytics/noise?{params}&format=csv",
        headers=admin_headers,
    )
    assert noise_csv.status_code == 200
    noise_metrics = _csv_metric_rows(noise_csv.text)
    assert int(noise_metrics["inbound_alerts"]) == noise_json["inbound_alerts"]
    assert (
        float(noise_metrics["noise_reduction_ratio"])
        == noise_json["noise_reduction_ratio"]
    )

    response_json = (
        await client.get(f"/api/v1/analytics/response?{params}", headers=admin_headers)
    ).json()
    response_csv = await client.get(
        f"/api/v1/analytics/response?{params}&format=csv",
        headers=admin_headers,
    )
    assert response_csv.status_code == 200
    response_metrics = _csv_metric_rows(response_csv.text)
    assert (
        int(response_metrics["overall.incident_count"])
        == response_json["overall"]["incident_count"]
    )
    assert (
        float(response_metrics["overall.mtta_seconds"])
        == response_json["overall"]["mtta_seconds"]
    )


async def test_analytics_range_and_role_guards(
    client, app, admin_headers, viewer_headers
):
    await _seed_known_data(app)
    invalid = await client.get(
        "/api/v1/analytics/noise?from=2026-07-20T00:00:00Z&to=2026-07-01T00:00:00Z",
        headers=admin_headers,
    )
    assert invalid.status_code == 422
    viewer = await client.get(
        "/api/v1/analytics/noise?from=2026-07-01T00:00:00Z&to=2026-07-20T00:00:00Z",
        headers=viewer_headers,
    )
    assert viewer.status_code == 403


async def test_response_analytics_perf_guard_10k(app):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    async with app.state.session_factory() as db:
        team = Team(org_id=ORG_ID, name="Perf Team", slug="perf-team")
        db.add(team)
        await db.flush()
        service = Service(
            org_id=ORG_ID,
            team_id=team.id,
            name="Perf Service",
            slug="perf-service",
        )
        db.add(service)
        await db.flush()
        db.add_all(
            [
                Incident(
                    org_id=ORG_ID,
                    title=f"Perf {idx}",
                    description="perf",
                    status="resolved",
                    priority="P2",
                    service_id=service.id,
                    created_at=base + timedelta(minutes=idx),
                    acknowledged_at=base + timedelta(minutes=idx, seconds=30),
                    updated_at=base + timedelta(minutes=idx, seconds=90),
                )
                for idx in range(10_000)
            ]
        )
        await db.commit()

    async with app.state.session_factory() as db:
        started = time.perf_counter()
        report = await build_response_report(
            db,
            ORG_ID,
            from_at=base,
            to_at=base + timedelta(days=10),
        )
        elapsed = time.perf_counter() - started
    assert report["overall"]["incident_count"] == 10_000
    assert elapsed < 2.0
