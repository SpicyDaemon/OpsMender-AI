"""Tests for Sprint 14 — External Incident Ingestion.

Covers:
- Ingest token CRUD (create, list, revoke, delete)
- Webhook endpoint (POST /incidents/ingest) with the supported adapters
- Dedup by external fingerprint (create, update, skip)
- Token auth (missing, invalid, revoked)
- Rate limiting & audit log entries
- Adapter parsing for CloudWatch, Azure Monitor, Generic JSON, and registry fallback
"""

from __future__ import annotations

import asyncio
import json
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base
from backend.db.repos import IncidentRepo, IngestLogRepo, IngestTokenRepo, SessionRepo
from backend.ingest.service import generate_token, hash_token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "ingest-test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        from backend.db.models import Organization
        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
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


@pytest.fixture
async def admin_headers(client: AsyncClient) -> dict[str, str]:
    """Register + login an admin user."""
    await client.post(
        "/auth/register",
        json={
            "username": "ingest_admin",
            "email": "ingest@test.com",
            "password": "securepass123",
        },
    )
    # Linked automatically by /auth/register

    resp = await client.post(
        "/auth/login",
        json={
            "username": "ingest_admin",
            "password": "securepass123",
        },
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def viewer_headers(client: AsyncClient, admin_headers) -> dict[str, str]:
    """Register a viewer user."""
    await client.post(
        "/auth/register",
        json={
            "username": "ingest_viewer",
            "email": "viewer_ingest@test.com",
            "password": "viewerpass123",
            "role": "viewer",
        },
    )
    # Linked automatically by /auth/register

    resp = await client.post(
        "/auth/login",
        json={
            "username": "ingest_viewer",
            "password": "viewerpass123",
        },
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_token(app, provider: str = "generic", name: str = "test-token"):
    """Helper: create an ingest token directly in the DB, return (raw, token_row)."""
    raw = generate_token()
    async with app.state.session_factory() as db:
        tok = await IngestTokenRepo.create(
            db,
            TEST_ORG_ID,
            name=name,
            provider=provider,
            token_hash=hash_token(raw),
        )
        await db.commit()
        await db.refresh(tok)
    return raw, tok


# ===========================================================================
# Ingest Token CRUD
# ===========================================================================


class TestIngestTokenCRUD:
    async def test_create_token_admin(self, client: AsyncClient, admin_headers):
        resp = await client.post(
            "/ingest-tokens",
            json={
                "name": "cloudwatch-prod",
                "provider": "cloudwatch",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "cloudwatch-prod"
        assert data["provider"] == "cloudwatch"
        assert data["is_active"] is True
        assert "token" in data  # raw token returned once
        assert data["token"].startswith("opsmender_ingest_")

    async def test_create_token_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.post(
            "/ingest-tokens",
            json={
                "name": "denied",
                "provider": "generic",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_create_token_duplicate_name(
        self, client: AsyncClient, admin_headers
    ):
        await client.post(
            "/ingest-tokens",
            json={
                "name": "dup-token",
                "provider": "generic",
            },
            headers=admin_headers,
        )
        resp = await client.post(
            "/ingest-tokens",
            json={
                "name": "dup-token",
                "provider": "cloudwatch",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 409

    async def test_list_tokens_admin(self, client: AsyncClient, admin_headers):
        await client.post(
            "/ingest-tokens",
            json={
                "name": "tok1",
                "provider": "generic",
            },
            headers=admin_headers,
        )
        await client.post(
            "/ingest-tokens",
            json={
                "name": "tok2",
                "provider": "azure_monitor",
            },
            headers=admin_headers,
        )

        resp = await client.get("/ingest-tokens", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        # Raw token must NOT be in list response
        for item in data["items"]:
            assert "token" not in item

    async def test_list_tokens_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.get("/ingest-tokens", headers=viewer_headers)
        assert resp.status_code == 403

    async def test_revoke_token_admin(self, client: AsyncClient, app, admin_headers):
        raw, tok = await _create_token(app, name="revoke-me")
        resp = await client.post(
            f"/ingest-tokens/{tok.id}/revoke",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_revoke_token_not_found(self, client: AsyncClient, admin_headers):
        resp = await client.post(
            f"/ingest-tokens/{uuid.uuid4()}/revoke",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_delete_token_admin(self, client: AsyncClient, app, admin_headers):
        raw, tok = await _create_token(app, name="delete-me")
        resp = await client.delete(
            f"/ingest-tokens/{tok.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 204

        # Verify gone
        async with app.state.session_factory() as db:
            assert await IngestTokenRepo.get_by_id(db, TEST_ORG_ID, tok.id) is None

    async def test_delete_token_not_found(self, client: AsyncClient, admin_headers):
        resp = await client.delete(
            f"/ingest-tokens/{uuid.uuid4()}",
            headers=admin_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# Provider listing
# ===========================================================================


class TestIngestProviders:
    async def test_list_providers(self, client: AsyncClient, admin_headers):
        resp = await client.get("/ingest-providers", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        keys = [p["key"] for p in data["items"]]
        assert "cloudwatch" in keys
        assert "azure_monitor" in keys
        assert "gcp_monitoring" in keys
        assert "oci_monitoring" in keys
        assert "generic" in keys


# ===========================================================================
# Webhook — Authentication
# ===========================================================================


class TestIngestAuth:
    async def test_missing_token(self, client: AsyncClient):
        resp = await client.post("/incidents/ingest", json={"title": "test"})
        assert resp.status_code == 401
        assert "Missing ingest token" in resp.json()["detail"]

    async def test_invalid_token(self, client: AsyncClient):
        resp = await client.post(
            "/incidents/ingest",
            json={"title": "test"},
            headers={"X-OpsMender-Token": "bad-token"},
        )
        assert resp.status_code == 401
        assert "Invalid or revoked" in resp.json()["detail"]

    async def test_revoked_token(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, name="revoked-tok")
        # Revoke it
        async with app.state.session_factory() as db:
            await IngestTokenRepo.revoke(db, TEST_ORG_ID, tok.id)
            await db.commit()

        resp = await client.post(
            "/incidents/ingest",
            json={"title": "test"},
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 401

    async def test_auth_via_bearer_header(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, name="bearer-tok")
        resp = await client.post(
            "/incidents/ingest",
            json={"title": "Hello", "description": "World"},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_auth_via_x_opsmender_token(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, name="xopsmender-tok")
        resp = await client.post(
            "/incidents/ingest",
            json={"title": "Hello", "description": "World"},
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ===========================================================================
# Webhook — Generic adapter
# ===========================================================================


class TestIngestGeneric:
    async def test_ingest_generic_creates_incident(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, provider="generic", name="generic1")
        resp = await client.post(
            "/incidents/ingest",
            json={
                "title": "Disk Full on prod-db-01",
                "description": "Disk at 98% utilization",
                "severity": "high",
                "id": "alert-12345",
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["dedup_action"] == "created"
        assert data["incident_id"] is not None

    async def test_ingest_generic_dedup_skip(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, provider="generic", name="generic-dedup")

        payload = {
            "title": "CPU spike",
            "description": "CPU at 100%",
            "severity": "critical",
            "id": "cpu-spike-001",
        }

        # First ingest — creates
        resp1 = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        assert resp1.json()["dedup_action"] == "created"
        inc_id = resp1.json()["incident_id"]

        # Second ingest — same fingerprint → skipped
        resp2 = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        assert resp2.json()["dedup_action"] == "skipped"
        assert resp2.json()["incident_id"] == inc_id

    async def test_ingest_generic_dedup_update_on_resolve(
        self, client: AsyncClient, app
    ):
        raw, tok = await _create_token(app, provider="generic", name="generic-resolve")

        # Create incident
        await client.post(
            "/incidents/ingest",
            json={
                "title": "Service Down",
                "description": "HTTP 503",
                "severity": "critical",
                "id": "svc-down-001",
            },
            headers={"X-OpsMender-Token": raw},
        )

        # Resolve it
        resp = await client.post(
            "/incidents/ingest",
            json={
                "title": "Service Down",
                "description": "Recovered",
                "severity": "low",
                "id": "svc-down-001",
                "status": "resolved",
            },
            headers={"X-OpsMender-Token": raw},
        )
        data = resp.json()
        assert data["dedup_action"] == "updated"

        # Verify the incident status is now resolved
        async with app.state.session_factory() as db:
            inc = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(data["incident_id"])
            )
            assert inc is not None
            assert inc.status == "resolved"

    async def test_ingest_resolve_stops_in_progress_sessions(
        self, client: AsyncClient, app
    ):
        raw, tok = await _create_token(
            app, provider="generic", name="generic-resolve-stops"
        )

        # Create an incident via ingest, then attach a running AI session.
        created = await client.post(
            "/incidents/ingest",
            json={
                "title": "Service Down",
                "description": "HTTP 503",
                "severity": "critical",
                "id": "svc-down-stop",
            },
            headers={"X-OpsMender-Token": raw},
        )
        incident_id = uuid.UUID(created.json()["incident_id"])
        async with app.state.session_factory() as db:
            running = await SessionRepo.create(
                db, TEST_ORG_ID, tier=0, incident_id=incident_id
            )  # defaults to "active"
            await db.commit()

        # A clearing alert resolves the incident → its session is stopped.
        resp = await client.post(
            "/incidents/ingest",
            json={
                "title": "Service Down",
                "description": "Recovered",
                "severity": "low",
                "id": "svc-down-stop",
                "status": "resolved",
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.json()["dedup_action"] == "updated"

        async with app.state.session_factory() as db:
            running_after = await SessionRepo.get_by_id(db, TEST_ORG_ID, running.id)
            assert running_after.status == "stopped"
            assert running_after.ended_at is not None

    async def test_ingest_auto_start_creates_session_for_autonomous_tier(
        self, client: AsyncClient, app, admin_headers
    ):
        config_resp = await client.put(
            "/config",
            json={
                "tier": 0,
            },
            headers=admin_headers,
        )
        assert config_resp.status_code == 200

        raw, _ = await _create_token(app, provider="generic", name="generic-autostart")
        resp = await client.post(
            "/incidents/ingest",
            json={
                "title": "Primary DB unavailable",
                "description": "RDS instance stopped responding",
                "severity": "critical",
                "id": "autostart-001",
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        incident_id = uuid.UUID(resp.json()["incident_id"])

        sessions = []
        for _ in range(50):
            async with app.state.session_factory() as db:
                sessions = await SessionRepo.list_by_incident(
                    db, TEST_ORG_ID, incident_id
                )
            if sessions:
                break
            await asyncio.sleep(0.01)
        assert len(sessions) == 1
        assert sessions[0].tier == 0

    @pytest.mark.parametrize("tier", [1, 2])
    async def test_ingest_auto_start_rejects_non_autonomous_tiers(
        self, tier, client: AsyncClient, app, admin_headers
    ):
        await client.put(
            "/config",
            json={
                "tier": tier,
            },
            headers=admin_headers,
        )
        raw, _ = await _create_token(
            app, provider="generic", name=f"generic-no-autostart-t{tier}"
        )
        resp = await client.post(
            "/incidents/ingest",
            json={
                "title": f"Tier {tier} incident",
                "description": "Severity matches, but autonomy does not",
                "severity": "critical",
                "id": f"autostart-tier-{tier}",
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        incident_id = uuid.UUID(resp.json()["incident_id"])
        async with app.state.session_factory() as db:
            assert (
                await SessionRepo.list_by_incident(db, TEST_ORG_ID, incident_id)
            ) == []

    async def test_default_t2_defers_auto_start_to_ack_without_session_creation(
        self, client: AsyncClient, app, admin_headers, caplog, monkeypatch
    ):
        # The fixture's org default is T2 (advisory). Prove intake defers the
        # session to acknowledgment and never reaches SessionRepo.create.
        await client.put(
            "/config",
            json={
            },
            headers=admin_headers,
        )

        async def _unexpected_create(*args, **kwargs):
            raise AssertionError("T2 intake must not create an AI session")

        monkeypatch.setattr(SessionRepo, "create", _unexpected_create)
        caplog.set_level("INFO", logger="backend.ingest.service")
        raw, _ = await _create_token(
            app,
            provider="generic",
            name="generic-default-t2-no-autostart",
        )
        resp = await client.post(
            "/incidents/ingest",
            json={
                "title": "Default T2 incident",
                "description": "Tier remains advisory, so the session defers to ack",
                "severity": "critical",
                "id": "autostart-default-t2",
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        assert "auto_start_deferred_to_ack" in caplog.text
        assert "resolved_tier=2" in caplog.text

    async def test_ingest_auto_start_skips_for_advisory_tier(
        self, client: AsyncClient, app, admin_headers
    ):
        await client.put(
            "/config",
            json={
            },
            headers=admin_headers,
        )

        raw, _ = await _create_token(
            app, provider="generic", name="generic-no-autostart"
        )
        resp = await client.post(
            "/incidents/ingest",
            json={
                "title": "Disk warning",
                "description": "Disk is 82% full",
                "severity": "high",
                "id": "autostart-002",
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        incident_id = uuid.UUID(resp.json()["incident_id"])

        async with app.state.session_factory() as db:
            sessions = await SessionRepo.list_by_incident(db, TEST_ORG_ID, incident_id)
            assert sessions == []

    async def test_ingest_auto_start_does_not_duplicate_sessions_on_dedup(
        self, client: AsyncClient, app, admin_headers
    ):
        await client.put(
            "/config",
            json={
                "tier": 0,
            },
            headers=admin_headers,
        )

        raw, _ = await _create_token(
            app, provider="generic", name="generic-autostart-dedup"
        )
        payload = {
            "title": "API outage",
            "description": "503s spiking",
            "severity": "critical",
            "id": "autostart-003",
        }
        first = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        second = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        assert first.status_code == 200
        assert second.status_code == 200

        incident_id = uuid.UUID(first.json()["incident_id"])
        sessions = []
        for _ in range(50):
            async with app.state.session_factory() as db:
                sessions = list(
                    await SessionRepo.list_by_incident(
                        db, TEST_ORG_ID, incident_id
                    )
                )
            if sessions:
                break
            await asyncio.sleep(0.01)
        assert len(sessions) == 1


# ===========================================================================
# Webhook — CloudWatch adapter
# ===========================================================================


class TestIngestCloudWatch:
    async def test_cloudwatch_alarm(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app,
            provider="cloudwatch",
            name="cw-alarm",
        )
        payload = {
            "Type": "Notification",
            "Message": json.dumps(
                {
                    "AlarmName": "HighCPU",
                    "NewStateValue": "ALARM",
                    "NewStateReason": "Threshold crossed",
                    "Region": "us-east-1",
                    "AWSAccountId": "123456789012",
                }
            ),
        }
        resp = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        data = resp.json()
        assert data["success"] is True
        assert data["dedup_action"] == "created"

    async def test_cloudwatch_ok_resolves(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app,
            provider="cloudwatch",
            name="cw-resolve",
        )
        alarm_msg = {
            "AlarmName": "HighMem",
            "NewStateValue": "ALARM",
            "NewStateReason": "Memory exceeded",
            "Region": "us-west-2",
            "AWSAccountId": "123456789012",
        }

        # Create ALARM
        await client.post(
            "/incidents/ingest",
            json={"Type": "Notification", "Message": json.dumps(alarm_msg)},
            headers={"X-OpsMender-Token": raw},
        )

        # OK state
        alarm_msg["NewStateValue"] = "OK"
        alarm_msg["NewStateReason"] = "Recovered"
        resp = await client.post(
            "/incidents/ingest",
            json={"Type": "Notification", "Message": json.dumps(alarm_msg)},
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.json()["dedup_action"] == "updated"

    async def test_cloudwatch_subscription_confirmation(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app,
            provider="cloudwatch",
            name="cw-sub",
        )
        payload = {
            "Type": "SubscriptionConfirmation",
            "SubscribeURL": "https://sns.us-east-1.amazonaws.com/?confirmSub",
            "Token": "abc123",
        }
        resp = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        data = resp.json()
        assert data["success"] is True
        assert data["dedup_action"] == "skipped"
        assert "SubscribeURL" in data["error"]


# ===========================================================================
# Webhook — Azure Monitor adapter
# ===========================================================================


class TestIngestAzureMonitor:
    async def test_azure_monitor_alert(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app,
            provider="azure_monitor",
            name="azure-alert",
        )
        payload = {
            "data": {
                "essentials": {
                    "alertId": "/subscriptions/xxx/alerts/abc",
                    "alertRule": "HighLatency",
                    "severity": "Sev1",
                    "monitorCondition": "Fired",
                    "description": "P95 latency above 500ms",
                    "alertTargetIDs": ["/subscriptions/xxx/rg/myapp"],
                    "firedDateTime": "2026-04-16T22:00:00Z",
                },
            },
        }
        resp = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        data = resp.json()
        assert data["success"] is True
        assert data["dedup_action"] == "created"

    async def test_azure_monitor_resolved(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app,
            provider="azure_monitor",
            name="azure-resolve",
        )
        essentials = {
            "alertId": "alert-resolve-001",
            "alertRule": "HighErrors",
            "severity": "Sev2",
            "monitorCondition": "Fired",
            "description": "Error rate exceeded",
            "alertTargetIDs": [],
            "firedDateTime": "2026-04-16T22:00:00Z",
        }

        # Fired
        await client.post(
            "/incidents/ingest",
            json={"data": {"essentials": essentials}},
            headers={"X-OpsMender-Token": raw},
        )

        # Resolved
        essentials["monitorCondition"] = "Resolved"
        resp = await client.post(
            "/incidents/ingest",
            json={"data": {"essentials": essentials}},
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.json()["dedup_action"] == "updated"


# ===========================================================================
# Ingest audit log
# ===========================================================================


class TestIngestAuditLog:
    async def test_ingest_creates_log_entry(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, name="log-tok")
        await client.post(
            "/incidents/ingest",
            json={"title": "Test", "description": "Logged"},
            headers={"X-OpsMender-Token": raw},
        )

        async with app.state.session_factory() as db:
            logs = await IngestLogRepo.list_recent(db, TEST_ORG_ID, token_id=tok.id)
            assert len(logs) == 1
            assert logs[0].provider == "generic"
            assert logs[0].dedup_action == "created"
            assert logs[0].error is None

    async def test_parse_error_logged(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app,
            provider="azure_monitor",
            name="log-error-tok",
        )
        # Azure adapter expects data.essentials — this will fail
        resp = await client.post(
            "/incidents/ingest",
            json={"bad": "payload"},
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 422

        async with app.state.session_factory() as db:
            logs = await IngestLogRepo.list_recent(db, TEST_ORG_ID, token_id=tok.id)
            assert len(logs) == 1
            assert logs[0].error is not None

    async def test_last_used_at_updated(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, name="touch-tok")
        assert tok.last_used_at is None

        await client.post(
            "/incidents/ingest",
            json={"title": "Touch test", "description": "d"},
            headers={"X-OpsMender-Token": raw},
        )

        async with app.state.session_factory() as db:
            refreshed = await IngestTokenRepo.get_by_id(db, TEST_ORG_ID, tok.id)
            assert refreshed is not None
            assert refreshed.last_used_at is not None


# ===========================================================================
# Adapter unit tests (no HTTP, pure logic)
# ===========================================================================


class TestAdaptersUnit:
    def test_generic_fallback_fields(self):
        from backend.ingest.adapters.generic import GenericAdapter

        adapter = GenericAdapter()
        result = adapter.parse(
            {
                "summary": "Fall back",
                "message": "Description from message",
            }
        )
        assert result.title == "Fall back"
        assert result.description == "Description from message"

    def test_generic_custom_mapping(self):
        from backend.ingest.adapters.generic import GenericAdapter

        adapter = GenericAdapter(
            field_mapping={
                "title": "alert.name",
                "description": "alert.body",
                "severity": "alert.level",
                "external_id": "alert.uid",
            }
        )
        result = adapter.parse(
            {
                "alert": {
                    "name": "Custom Alert",
                    "body": "Something broke",
                    "level": "critical",
                    "uid": "custom-123",
                },
            }
        )
        assert result.title == "Custom Alert"
        assert result.severity == "critical"
        assert result.external_id == "custom-123"

    def test_cloudwatch_invalid_message(self):
        from backend.ingest.adapters.cloudwatch import CloudWatchAdapter

        adapter = CloudWatchAdapter()
        with pytest.raises(ValueError, match="Unsupported SNS"):
            adapter.parse({"Type": "UnknownType"})

    def test_azure_monitor_missing_essentials(self):
        from backend.ingest.adapters.azure_monitor import AzureMonitorAdapter

        adapter = AzureMonitorAdapter()
        with pytest.raises(ValueError, match="Missing"):
            adapter.parse({"data": {}})

    def test_registry_fallback_to_universal(self):
        from backend.ingest.registry import get_adapter
        from backend.ingest.adapters.universal import UniversalAdapter

        adapter = get_adapter("unknown_provider")
        assert isinstance(adapter, UniversalAdapter)


# ===========================================================================
# Rate limiter — unit tests
# ===========================================================================


class TestRateLimiterUnit:
    async def test_allows_under_limit(self):
        from backend.ingest.rate_limiter import IngestRateLimiter

        limiter = IngestRateLimiter(max_requests=5, window_seconds=60)
        token_id = uuid.uuid4()

        for _ in range(5):
            result = await limiter.check(token_id)
            assert result.allowed is True

    async def test_blocks_over_limit(self):
        from backend.ingest.rate_limiter import IngestRateLimiter

        limiter = IngestRateLimiter(max_requests=3, window_seconds=60)
        token_id = uuid.uuid4()

        for _ in range(3):
            result = await limiter.check(token_id)
            assert result.allowed is True

        # 4th request should be blocked
        result = await limiter.check(token_id)
        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after is not None
        assert result.retry_after > 0

    async def test_remaining_decreases(self):
        from backend.ingest.rate_limiter import IngestRateLimiter

        limiter = IngestRateLimiter(max_requests=5, window_seconds=60)
        token_id = uuid.uuid4()

        r1 = await limiter.check(token_id)
        assert r1.remaining == 4

        r2 = await limiter.check(token_id)
        assert r2.remaining == 3

    async def test_separate_tokens_independent(self):
        from backend.ingest.rate_limiter import IngestRateLimiter

        limiter = IngestRateLimiter(max_requests=2, window_seconds=60)
        t1 = uuid.uuid4()
        t2 = uuid.uuid4()

        # Exhaust t1
        await limiter.check(t1)
        await limiter.check(t1)
        assert (await limiter.check(t1)).allowed is False

        # t2 is unaffected
        assert (await limiter.check(t2)).allowed is True

    async def test_reset_clears_bucket(self):
        from backend.ingest.rate_limiter import IngestRateLimiter

        limiter = IngestRateLimiter(max_requests=2, window_seconds=60)
        token_id = uuid.uuid4()

        await limiter.check(token_id)
        await limiter.check(token_id)
        assert (await limiter.check(token_id)).allowed is False

        # Reset clears the bucket
        await limiter.reset(token_id)
        assert (await limiter.check(token_id)).allowed is True

    async def test_disabled_when_zero(self):
        from backend.ingest.rate_limiter import IngestRateLimiter

        limiter = IngestRateLimiter(max_requests=0, window_seconds=60)
        token_id = uuid.uuid4()

        assert limiter.disabled is True
        # Always allowed
        for _ in range(100):
            result = await limiter.check(token_id)
            assert result.allowed is True


# ===========================================================================
# Rate limiter — integration test via webhook
# ===========================================================================


class TestRateLimitIntegration:
    async def test_rate_limit_returns_429(self, client: AsyncClient, app):
        """Hit the webhook endpoint until rate limited, verify 429 + headers."""
        # Set a very low limit on the app's limiter
        from backend.ingest.rate_limiter import IngestRateLimiter

        app.state.ingest_limiter = IngestRateLimiter(
            max_requests=2,
            window_seconds=60,
        )

        raw, tok = await _create_token(app, name="rl-test")

        # First 2 should succeed
        for _ in range(2):
            resp = await client.post(
                "/incidents/ingest",
                json={"title": "RL test", "description": "d"},
                headers={"X-OpsMender-Token": raw},
            )
            assert resp.status_code == 200

        # 3rd should be rate-limited
        resp = await client.post(
            "/incidents/ingest",
            json={"title": "RL test", "description": "d"},
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 429
        body = resp.json()
        assert "Rate limit exceeded" in body["detail"]
        assert "retry_after" in body
        assert "Retry-After" in resp.headers
        assert "X-RateLimit-Limit" in resp.headers
        assert resp.headers["X-RateLimit-Limit"] == "2"
        assert resp.headers["X-RateLimit-Remaining"] == "0"


# ===========================================================================
# Universal adapter — heuristic + LLM fallback + shape cache
# ===========================================================================


class TestUniversalAdapterUnit:
    """Pure unit tests for the heuristic parser — no DB, no HTTP."""

    def test_flat_standard_fields(self):
        from backend.ingest.adapters.universal import UniversalAdapter

        result = UniversalAdapter().parse(
            {
                "title": "Disk full",
                "description": "Node01 at 98%",
                "severity": "critical",
                "id": "alert-xyz",
                "status": "triggered",
            }
        )
        assert result.title == "Disk full"
        assert result.description == "Node01 at 98%"
        assert result.severity == "critical"
        assert result.external_id == "alert-xyz"
        assert result.status == "investigating"
        assert result.needs_llm is False
        assert result.extracted_paths == {
            "title": "title",
            "description": "description",
            "severity": "severity",
            "external_id": "id",
            "status": "status",
        }

    def test_datadog_shape(self):
        """Datadog-ish webhook payload: alert_title, alert_priority, etc."""
        from backend.ingest.adapters.universal import UniversalAdapter

        result = UniversalAdapter().parse(
            {
                "alert_title": "[P2] High CPU on web-01",
                "body": "CPU sustained above 90% for 5 minutes",
                "priority": "P2",
                "alert_id": "dd-12345",
                "alert_status": "Alert",
            }
        )
        # "alert_title" isn't in TITLE_KEYS but "name"/"subject" aren't either;
        # expect LLM fallback signal when title isn't resolved.
        assert result.needs_llm is True or result.title == "[P2] High CPU on web-01"

    def test_grafana_style_envelope(self):
        """Grafana 9+ wraps payload under `alerts[0]` with commonName."""
        from backend.ingest.adapters.universal import UniversalAdapter

        result = UniversalAdapter().parse(
            {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "DBConnPoolFull"},
                        "annotations": {"summary": "Connection pool is saturated"},
                    }
                ],
                "alert": {
                    "alertname": "DBConnPoolFull",
                    "summary": "Connection pool is saturated",
                    "severity": "high",
                    "fingerprint": "abc123",
                    "status": "firing",
                },
            }
        )
        assert result.title == "DBConnPoolFull"
        assert result.description == "Connection pool is saturated"
        assert result.severity == "high"
        assert result.external_id == "abc123"
        assert result.status == "investigating"
        assert result.needs_llm is False

    def test_severity_mapping(self):
        """Sev labels and numeric priorities should map to OpsMender's 4 levels."""
        from backend.ingest.adapters.universal import UniversalAdapter

        assert (
            UniversalAdapter().parse({"title": "x", "severity": "P1"}).severity
            == "critical"
        )
        assert (
            UniversalAdapter().parse({"title": "x", "severity": "warning"}).severity
            == "medium"
        )
        assert (
            UniversalAdapter().parse({"title": "x", "severity": "2"}).severity == "high"
        )
        assert (
            UniversalAdapter().parse({"title": "x", "severity": "notice"}).severity
            == "low"
        )

    def test_status_mapping(self):
        from backend.ingest.adapters.universal import UniversalAdapter

        assert (
            UniversalAdapter().parse({"title": "x", "status": "recovery"}).status
            == "resolved"
        )
        assert (
            UniversalAdapter().parse({"title": "x", "status": "firing"}).status
            == "investigating"
        )
        assert (
            UniversalAdapter().parse({"title": "x", "state": "acknowledged"}).status
            == "investigating"
        )

    def test_unknown_shape_signals_llm_fallback(self):
        """A payload with no recognizable keys should set needs_llm=True."""
        from backend.ingest.adapters.universal import UniversalAdapter

        result = UniversalAdapter().parse(
            {"foo": {"bar": {"baz": "some content"}}, "weird_key": "xyz"}
        )
        assert result.needs_llm is True
        assert result.title == "Untitled Incident"

    def test_field_mapping_short_circuits(self):
        """Pre-learned paths should take precedence over heuristics."""
        from backend.ingest.adapters.universal import UniversalAdapter

        payload = {"alerts": [{"name": "MyAlert", "details": "boom"}]}
        adapter = UniversalAdapter(
            field_mapping={
                "title": "alerts.0.name",
                "description": "alerts.0.details",
            }
        )
        result = adapter.parse(payload)
        assert result.title == "MyAlert"
        assert result.description == "boom"
        assert result.needs_llm is False


class TestShapeHash:
    def test_same_shape_same_hash(self):
        from backend.ingest.llm_extractor import compute_shape_hash

        a = {"title": "one", "severity": "high", "id": "abc"}
        b = {"title": "two", "severity": "low", "id": "xyz"}
        assert compute_shape_hash(a) == compute_shape_hash(b)

    def test_different_keys_different_hash(self):
        from backend.ingest.llm_extractor import compute_shape_hash

        a = {"title": "one", "id": "abc"}
        b = {"title": "one", "alert_id": "abc"}
        assert compute_shape_hash(a) != compute_shape_hash(b)

    def test_nested_shape_stable(self):
        from backend.ingest.llm_extractor import compute_shape_hash

        a = {"alert": {"name": "x", "sev": "p1"}}
        b = {"alert": {"name": "y", "sev": "p3"}}
        assert compute_shape_hash(a) == compute_shape_hash(b)


class TestUniversalIngestIntegration:
    """End-to-end webhook tests through the /incidents/ingest endpoint."""

    async def test_auto_token_accepts_any_payload(self, client: AsyncClient, app):
        raw, _ = await _create_token(app, provider="auto", name="auto-token")

        resp = await client.post(
            "/incidents/ingest",
            json={
                "title": "Redis cluster degraded",
                "description": "Two replicas out of sync",
                "severity": "high",
                "id": "universal-001",
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["dedup_action"] == "created"

        # Dedup namespaced per token
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(body["incident_id"])
            )
            assert incident.external_source == "auto:auto-token"
            assert incident.external_id == "universal-001"

    async def test_auto_token_envelope_payload(self, client: AsyncClient, app):
        raw, _ = await _create_token(app, provider="auto", name="auto-envelope")

        resp = await client.post(
            "/incidents/ingest",
            json={
                "alert": {
                    "alertname": "QueueDepthHigh",
                    "summary": "Queue backed up",
                    "severity": "p2",
                    "fingerprint": "env-001",
                    "status": "firing",
                }
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(body["incident_id"])
            )
            assert incident.title == "QueueDepthHigh"
            assert incident.severity == "high"
            assert incident.external_id == "env-001"

    async def test_auto_token_dedup_across_calls(self, client: AsyncClient, app):
        raw, _ = await _create_token(app, provider="auto", name="auto-dedup")
        payload = {
            "title": "Same Alert",
            "description": "ongoing",
            "severity": "high",
            "id": "dedup-001",
        }
        first = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        second = await client.post(
            "/incidents/ingest",
            json=payload,
            headers={"X-OpsMender-Token": raw},
        )
        assert first.json()["dedup_action"] == "created"
        assert second.json()["dedup_action"] == "skipped"
        assert first.json()["incident_id"] == second.json()["incident_id"]

    async def test_auto_invokes_llm_on_unknown_shape(
        self, client: AsyncClient, app, monkeypatch
    ):
        """LLM extractor is called when heuristics can't find a title."""
        from backend.ingest import service as svc

        called: list[dict] = []

        async def fake_apply(db, org_id, *, token, payload, config):
            called.append(payload)
            # Return paths that reach into the weird payload
            paths = {
                "title": "custom.eventName",
                "description": "custom.rawBody",
                "severity": "custom.prio",
                "external_id": "custom.uid",
            }
            return paths, False

        monkeypatch.setattr(svc, "apply_shape_cache", fake_apply)

        raw, _ = await _create_token(app, provider="auto", name="auto-llm")

        resp = await client.post(
            "/incidents/ingest",
            json={
                "custom": {
                    "eventName": "StrangeAlert",
                    "rawBody": "something tripped",
                    "prio": "P2",
                    "uid": "weird-42",
                }
            },
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(called) == 1

        async with app.state.session_factory() as db:
            inc = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(body["incident_id"])
            )
            assert inc.title == "StrangeAlert"
            assert inc.severity == "high"
            assert inc.external_id == "weird-42"

    async def test_auto_shape_cache_skips_llm_on_repeat(
        self, client: AsyncClient, app, monkeypatch
    ):
        """A second payload with the same shape should not call the LLM."""
        from backend.ingest import llm_extractor
        from backend.ingest import service as svc

        call_count = {"llm": 0}

        async def fake_llm(db, org_id, *, payload, config, model_cfg=None):
            call_count["llm"] += 1
            return {
                "title": "weird.name",
                "description": "weird.detail",
            }

        monkeypatch.setattr(llm_extractor, "extract_paths_via_llm", fake_llm)

        raw, _ = await _create_token(app, provider="auto", name="auto-cache")

        payload_one = {"weird": {"name": "AlertA", "detail": "first"}}
        payload_two = {"weird": {"name": "AlertB", "detail": "second"}}

        resp1 = await client.post(
            "/incidents/ingest",
            json=payload_one,
            headers={"X-OpsMender-Token": raw},
        )
        resp2 = await client.post(
            "/incidents/ingest",
            json=payload_two,
            headers={"X-OpsMender-Token": raw},
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert call_count["llm"] == 1  # cached on 2nd call

        # The second incident reuses the same learned paths and is a distinct incident
        async with app.state.session_factory() as db:
            inc2 = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(resp2.json()["incident_id"])
            )
            assert inc2.title == "AlertB"

    async def test_llm_fallback_failure_degrades_gracefully(
        self, client: AsyncClient, app, monkeypatch
    ):
        """If the LLM returns nothing, we still create an Untitled incident rather than 500."""
        from backend.ingest import service as svc

        async def fake_apply(db, org_id, *, token, payload, config):
            return None, False

        monkeypatch.setattr(svc, "apply_shape_cache", fake_apply)

        raw, _ = await _create_token(app, provider="auto", name="auto-llm-fail")

        resp = await client.post(
            "/incidents/ingest",
            json={"foo": {"bar": "baz"}},
            headers={"X-OpsMender-Token": raw},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        async with app.state.session_factory() as db:
            inc = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(body["incident_id"])
            )
            assert inc.title == "Untitled Incident"


class TestLearnShapeAPI:
    async def test_learn_shape_endpoint_persists_paths(
        self, client: AsyncClient, app, admin_headers
    ):
        # Create an auto token via the API
        resp = await client.post(
            "/ingest-tokens",
            json={"name": "learn-test", "provider": "auto"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        token_id = resp.json()["id"]

        # Train it on a sample
        sample = {
            "title": "SampleAlert",
            "description": "sample body",
            "severity": "medium",
            "id": "sample-7",
        }
        learn = await client.post(
            f"/ingest-tokens/{token_id}/learn-shape",
            json={"payload": sample},
            headers=admin_headers,
        )
        assert learn.status_code == 200
        body = learn.json()
        assert body["preview"]["title"] == "SampleAlert"
        assert body["preview"]["severity"] == "medium"
        assert body["paths"].get("title") == "title"

        # Listing now shows shape_cache_size=1
        lst = await client.get("/ingest-tokens", headers=admin_headers)
        match = next(i for i in lst.json()["items"] if i["id"] == token_id)
        assert match["shape_cache_size"] == 1

    async def test_learn_shape_rejects_non_auto_provider(
        self, client: AsyncClient, app, admin_headers
    ):
        resp = await client.post(
            "/ingest-tokens",
            json={"name": "generic-learn", "provider": "generic"},
            headers=admin_headers,
        )
        token_id = resp.json()["id"]

        learn = await client.post(
            f"/ingest-tokens/{token_id}/learn-shape",
            json={"payload": {"title": "x"}},
            headers=admin_headers,
        )
        assert learn.status_code == 400

    async def test_create_token_with_sample_payload_prewarms_cache(
        self, client: AsyncClient, app, admin_headers
    ):
        resp = await client.post(
            "/ingest-tokens",
            json={
                "name": "pre-warmed",
                "provider": "auto",
                "sample_payload": {
                    "title": "Prewarm",
                    "severity": "low",
                    "id": "pw-1",
                },
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201

        lst = await client.get("/ingest-tokens", headers=admin_headers)
        match = next(i for i in lst.json()["items"] if i["name"] == "pre-warmed")
        assert match["shape_cache_size"] == 1
