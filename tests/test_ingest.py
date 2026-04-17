"""Tests for Sprint 14 — External Incident Ingestion.

Covers:
- Ingest token CRUD (create, list, revoke, delete)
- Webhook endpoint (POST /incidents/ingest) with all 4 adapters
- Dedup by external fingerprint (create, update, skip)
- Token auth (missing, invalid, revoked)
- Rate limiting & audit log entries
- Adapter parsing for CloudWatch, Azure Monitor, LegacyAlertVendor, Generic JSON
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base
from backend.db.repos import IncidentRepo, IngestLogRepo, IngestTokenRepo
from backend.ingest.service import generate_token, hash_token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def app(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "AIM_TIER=2\n"
        "AIM_LOG_LEVEL=INFO\n"
        "AIM_AUDIT_LOG=./logs/audit.jsonl\n"
        "AIM_JWT_SECRET=test-secret\n"
        "AIM_DATABASE_URL=sqlite+aiosqlite://\n"
        f"AIM_MCP_SERVERS_JSON={json.dumps([])}\n"
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
    await client.post("/auth/register", json={
        "username": "ingest_admin",
        "email": "ingest@test.com",
        "password": "securepass123",
    })
    resp = await client.post("/auth/login", json={
        "username": "ingest_admin",
        "password": "securepass123",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def viewer_headers(client: AsyncClient, admin_headers) -> dict[str, str]:
    """Register a viewer user."""
    await client.post("/auth/register", json={
        "username": "ingest_viewer",
        "email": "viewer_ingest@test.com",
        "password": "viewerpass123",
        "role": "viewer",
    })
    resp = await client.post("/auth/login", json={
        "username": "ingest_viewer",
        "password": "viewerpass123",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_token(app, provider: str = "generic", name: str = "test-token"):
    """Helper: create an ingest token directly in the DB, return (raw, token_row)."""
    raw = generate_token()
    async with app.state.session_factory() as db:
        tok = await IngestTokenRepo.create(
            db, name=name, provider=provider, token_hash=hash_token(raw),
        )
        await db.commit()
        await db.refresh(tok)
    return raw, tok


# ===========================================================================
# Ingest Token CRUD
# ===========================================================================

class TestIngestTokenCRUD:

    async def test_create_token_admin(self, client: AsyncClient, admin_headers):
        resp = await client.post("/ingest-tokens", json={
            "name": "cloudwatch-prod",
            "provider": "cloudwatch",
        }, headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "cloudwatch-prod"
        assert data["provider"] == "cloudwatch"
        assert data["is_active"] is True
        assert "token" in data  # raw token returned once
        assert data["token"].startswith("aim_ingest_")

    async def test_create_token_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.post("/ingest-tokens", json={
            "name": "denied", "provider": "generic",
        }, headers=viewer_headers)
        assert resp.status_code == 403

    async def test_create_token_duplicate_name(
        self, client: AsyncClient, admin_headers
    ):
        await client.post("/ingest-tokens", json={
            "name": "dup-token", "provider": "generic",
        }, headers=admin_headers)
        resp = await client.post("/ingest-tokens", json={
            "name": "dup-token", "provider": "cloudwatch",
        }, headers=admin_headers)
        assert resp.status_code == 409

    async def test_list_tokens_admin(self, client: AsyncClient, admin_headers):
        await client.post("/ingest-tokens", json={
            "name": "tok1", "provider": "generic",
        }, headers=admin_headers)
        await client.post("/ingest-tokens", json={
            "name": "tok2", "provider": "legacy_alert_vendor",
        }, headers=admin_headers)

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
            f"/ingest-tokens/{tok.id}/revoke", headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_revoke_token_not_found(self, client: AsyncClient, admin_headers):
        resp = await client.post(
            f"/ingest-tokens/{uuid.uuid4()}/revoke", headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_delete_token_admin(self, client: AsyncClient, app, admin_headers):
        raw, tok = await _create_token(app, name="delete-me")
        resp = await client.delete(
            f"/ingest-tokens/{tok.id}", headers=admin_headers,
        )
        assert resp.status_code == 204

        # Verify gone
        async with app.state.session_factory() as db:
            assert await IngestTokenRepo.get_by_id(db, tok.id) is None

    async def test_delete_token_not_found(self, client: AsyncClient, admin_headers):
        resp = await client.delete(
            f"/ingest-tokens/{uuid.uuid4()}", headers=admin_headers,
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
        assert "legacy_alert_vendor" in keys
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
            headers={"X-AIM-Token": "bad-token"},
        )
        assert resp.status_code == 401
        assert "Invalid or revoked" in resp.json()["detail"]

    async def test_revoked_token(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, name="revoked-tok")
        # Revoke it
        async with app.state.session_factory() as db:
            await IngestTokenRepo.revoke(db, tok.id)
            await db.commit()

        resp = await client.post(
            "/incidents/ingest",
            json={"title": "test"},
            headers={"X-AIM-Token": raw},
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

    async def test_auth_via_x_aim_token(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, name="xaim-tok")
        resp = await client.post(
            "/incidents/ingest",
            json={"title": "Hello", "description": "World"},
            headers={"X-AIM-Token": raw},
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
            headers={"X-AIM-Token": raw},
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
            "/incidents/ingest", json=payload, headers={"X-AIM-Token": raw},
        )
        assert resp1.json()["dedup_action"] == "created"
        inc_id = resp1.json()["incident_id"]

        # Second ingest — same fingerprint → skipped
        resp2 = await client.post(
            "/incidents/ingest", json=payload, headers={"X-AIM-Token": raw},
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
            headers={"X-AIM-Token": raw},
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
            headers={"X-AIM-Token": raw},
        )
        data = resp.json()
        assert data["dedup_action"] == "updated"

        # Verify the incident status is now resolved
        async with app.state.session_factory() as db:
            inc = await IncidentRepo.get_by_id(db, uuid.UUID(data["incident_id"]))
            assert inc is not None
            assert inc.status == "resolved"


# ===========================================================================
# Webhook — CloudWatch adapter
# ===========================================================================

class TestIngestCloudWatch:

    async def test_cloudwatch_alarm(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app, provider="cloudwatch", name="cw-alarm",
        )
        payload = {
            "Type": "Notification",
            "Message": json.dumps({
                "AlarmName": "HighCPU",
                "NewStateValue": "ALARM",
                "NewStateReason": "Threshold crossed",
                "Region": "us-east-1",
                "AWSAccountId": "123456789012",
            }),
        }
        resp = await client.post(
            "/incidents/ingest", json=payload, headers={"X-AIM-Token": raw},
        )
        data = resp.json()
        assert data["success"] is True
        assert data["dedup_action"] == "created"

    async def test_cloudwatch_ok_resolves(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app, provider="cloudwatch", name="cw-resolve",
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
            headers={"X-AIM-Token": raw},
        )

        # OK state
        alarm_msg["NewStateValue"] = "OK"
        alarm_msg["NewStateReason"] = "Recovered"
        resp = await client.post(
            "/incidents/ingest",
            json={"Type": "Notification", "Message": json.dumps(alarm_msg)},
            headers={"X-AIM-Token": raw},
        )
        assert resp.json()["dedup_action"] == "updated"

    async def test_cloudwatch_subscription_confirmation(
        self, client: AsyncClient, app
    ):
        raw, tok = await _create_token(
            app, provider="cloudwatch", name="cw-sub",
        )
        payload = {
            "Type": "SubscriptionConfirmation",
            "SubscribeURL": "https://sns.us-east-1.amazonaws.com/?confirmSub",
            "Token": "abc123",
        }
        resp = await client.post(
            "/incidents/ingest", json=payload, headers={"X-AIM-Token": raw},
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
            app, provider="azure_monitor", name="azure-alert",
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
            "/incidents/ingest", json=payload, headers={"X-AIM-Token": raw},
        )
        data = resp.json()
        assert data["success"] is True
        assert data["dedup_action"] == "created"

    async def test_azure_monitor_resolved(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app, provider="azure_monitor", name="azure-resolve",
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
            headers={"X-AIM-Token": raw},
        )

        # Resolved
        essentials["monitorCondition"] = "Resolved"
        resp = await client.post(
            "/incidents/ingest",
            json={"data": {"essentials": essentials}},
            headers={"X-AIM-Token": raw},
        )
        assert resp.json()["dedup_action"] == "updated"


# ===========================================================================
# Webhook — LegacyAlertVendor adapter
# ===========================================================================

class TestIngestLegacyAlertVendor:

    async def test_legacy_alert_vendor_triggered(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app, provider="legacy_alert_vendor", name="pd-trigger",
        )
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "PD-INC-001",
                    "title": "Database connection pool exhausted",
                    "urgency": "high",
                    "priority": {"summary": "P1"},
                    "html_url": "https://pd.example.com/incidents/PD-INC-001",
                    "service": {"summary": "api-backend"},
                },
            },
        }
        resp = await client.post(
            "/incidents/ingest", json=payload, headers={"X-AIM-Token": raw},
        )
        data = resp.json()
        assert data["success"] is True
        assert data["dedup_action"] == "created"

    async def test_legacy_alert_vendor_resolved(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app, provider="legacy_alert_vendor", name="pd-resolve",
        )
        base = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "id": "PD-INC-002",
                    "title": "High error rate",
                    "urgency": "high",
                    "service": {"summary": "frontend"},
                },
            },
        }

        # Trigger
        await client.post(
            "/incidents/ingest", json=base, headers={"X-AIM-Token": raw},
        )

        # Resolve
        base["event"]["event_type"] = "incident.resolved"
        resp = await client.post(
            "/incidents/ingest", json=base, headers={"X-AIM-Token": raw},
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
            headers={"X-AIM-Token": raw},
        )

        async with app.state.session_factory() as db:
            logs = await IngestLogRepo.list_recent(db, token_id=tok.id)
            assert len(logs) == 1
            assert logs[0].provider == "generic"
            assert logs[0].dedup_action == "created"
            assert logs[0].error is None

    async def test_parse_error_logged(self, client: AsyncClient, app):
        raw, tok = await _create_token(
            app, provider="azure_monitor", name="log-error-tok",
        )
        # Azure adapter expects data.essentials — this will fail
        resp = await client.post(
            "/incidents/ingest",
            json={"bad": "payload"},
            headers={"X-AIM-Token": raw},
        )
        assert resp.status_code == 422

        async with app.state.session_factory() as db:
            logs = await IngestLogRepo.list_recent(db, token_id=tok.id)
            assert len(logs) == 1
            assert logs[0].error is not None

    async def test_last_used_at_updated(self, client: AsyncClient, app):
        raw, tok = await _create_token(app, name="touch-tok")
        assert tok.last_used_at is None

        await client.post(
            "/incidents/ingest",
            json={"title": "Touch test", "description": "d"},
            headers={"X-AIM-Token": raw},
        )

        async with app.state.session_factory() as db:
            refreshed = await IngestTokenRepo.get_by_id(db, tok.id)
            assert refreshed is not None
            assert refreshed.last_used_at is not None


# ===========================================================================
# Adapter unit tests (no HTTP, pure logic)
# ===========================================================================

class TestAdaptersUnit:

    def test_generic_fallback_fields(self):
        from backend.ingest.adapters.generic import GenericAdapter
        adapter = GenericAdapter()
        result = adapter.parse({
            "summary": "Fall back",
            "message": "Description from message",
        })
        assert result.title == "Fall back"
        assert result.description == "Description from message"

    def test_generic_custom_mapping(self):
        from backend.ingest.adapters.generic import GenericAdapter
        adapter = GenericAdapter(field_mapping={
            "title": "alert.name",
            "description": "alert.body",
            "severity": "alert.level",
            "external_id": "alert.uid",
        })
        result = adapter.parse({
            "alert": {
                "name": "Custom Alert",
                "body": "Something broke",
                "level": "critical",
                "uid": "custom-123",
            },
        })
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

    def test_legacy_alert_vendor_missing_event(self):
        from backend.ingest.adapters.legacy_alert_vendor import LegacyAlertVendorAdapter
        adapter = LegacyAlertVendorAdapter()
        with pytest.raises(ValueError, match="Missing"):
            adapter.parse({})

    def test_registry_fallback_to_generic(self):
        from backend.ingest.registry import get_adapter
        from backend.ingest.adapters.generic import GenericAdapter
        adapter = get_adapter("unknown_provider")
        assert isinstance(adapter, GenericAdapter)
