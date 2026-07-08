"""Tests for availability signal detection in ingest adapters (Sprint 25)."""

from __future__ import annotations

import json
import uuid

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config_loader import AppConfig
from backend.db.models import Base, UptimeSample
from backend.db.repos import SLATargetRepo, UptimeSampleRepo
from backend.ingest.adapters.cloudwatch import CloudWatchAdapter
from backend.ingest.adapters.universal import (
    UniversalAdapter,
    _interpret_up,
    _to_latency_ms,
)


# ======================================================================
# Unit tests — helper functions
# ======================================================================

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")



class TestInterpretUp:
    def test_bool_true(self):
        assert _interpret_up(True) is True

    def test_bool_false(self):
        assert _interpret_up(False) is False

    def test_int_1(self):
        assert _interpret_up(1) is True

    def test_int_0(self):
        assert _interpret_up(0) is False

    def test_float_positive(self):
        assert _interpret_up(0.95) is True

    def test_string_ok(self):
        assert _interpret_up("ok") is True

    def test_string_up(self):
        assert _interpret_up("UP") is True

    def test_string_down(self):
        assert _interpret_up("down") is False

    def test_string_passed(self):
        assert _interpret_up("passed") is True

    def test_none(self):
        assert _interpret_up(None) is False


class TestToLatencyMs:
    def test_milliseconds(self):
        assert _to_latency_ms(150) == 150

    def test_seconds_to_ms(self):
        # < 10 → treated as seconds
        assert _to_latency_ms(0.250) == 250

    def test_string_number(self):
        assert _to_latency_ms("42") == 42

    def test_invalid(self):
        assert _to_latency_ms("abc") is None

    def test_none(self):
        assert _to_latency_ms(None) is None


# ======================================================================
# Unit tests — CloudWatch adapter availability
# ======================================================================


class TestCloudWatchAvailability:
    def _make_cw_payload(self, state: str = "ALARM") -> dict:
        return {
            "Type": "Notification",
            "Message": json.dumps(
                {
                    "AlarmName": "web-api-health",
                    "NewStateValue": state,
                    "NewStateReason": "Threshold crossed",
                    "Region": "us-east-1",
                    "AWSAccountId": "123456789",
                }
            ),
        }

    def test_alarm_emits_down(self):
        adapter = CloudWatchAdapter()
        result = adapter.parse(self._make_cw_payload("ALARM"))

        assert result.availability is not None
        assert result.availability.target_name == "web-api-health"
        assert result.availability.up is False
        assert result.availability.source == "cloudwatch"

    def test_ok_emits_up(self):
        adapter = CloudWatchAdapter()
        result = adapter.parse(self._make_cw_payload("OK"))

        assert result.availability is not None
        assert result.availability.up is True


# ======================================================================
# Unit tests — Universal adapter availability detection
# ======================================================================


class TestUniversalAvailability:
    def test_prometheus_probe_success(self):
        """Prometheus blackbox exporter payload should detect availability."""
        payload = {
            "alertname": "ProbeFailure",
            "title": "probe_success check failed",
            "probe_success": 0,
            "probe_duration_seconds": 0.250,
            "target": "https://api.example.com",
            "severity": "critical",
            "status": "firing",
        }
        adapter = UniversalAdapter()
        result = adapter.parse(payload)

        assert result.availability is not None
        assert result.availability.target_name == "ProbeFailure"
        assert result.availability.up is False
        assert result.availability.latency_ms == 250
        assert result.availability.source == "ingest"

    def test_generic_health_check(self):
        """Payload with is_up and healthcheck title should emit signal."""
        payload = {
            "title": "healthcheck failed for auth-service",
            "is_up": False,
            "latency_ms": 500,
            "severity": "high",
            "id": "hc-123",
        }
        adapter = UniversalAdapter()
        result = adapter.parse(payload)

        assert result.availability is not None
        assert result.availability.up is False
        assert result.availability.latency_ms == 500

    def test_datadog_synthetics(self):
        """Datadog synthetic check result should emit availability."""
        payload = {
            "title": "Synthetic check failed",
            "check_type": "api",
            "check_name": "api-health-prod",
            "org": {"name": "myorg"},
            "result": {
                "passed": False,
                "timings": {"total": 0.350},
            },
            "severity": "high",
            "status": "alarm",
        }
        adapter = UniversalAdapter()
        result = adapter.parse(payload)

        assert result.availability is not None
        assert result.availability.target_name == "api-health-prod"
        assert result.availability.up is False
        assert result.availability.latency_ms == 350
        assert result.availability.source == "datadog"

    def test_no_availability_on_normal_incident(self):
        """Normal incident payloads should not emit availability signals."""
        payload = {
            "title": "Database migration failed",
            "description": "Migration script exited with code 1",
            "severity": "high",
        }
        adapter = UniversalAdapter()
        result = adapter.parse(payload)

        assert result.availability is None

    def test_heartbeat_title_triggers_detection(self):
        """'heartbeat' in the title should trigger availability detection."""
        payload = {
            "title": "Heartbeat lost for worker-3",
            "status": "alarm",
            "severity": "critical",
        }
        adapter = UniversalAdapter()
        result = adapter.parse(payload)

        assert result.availability is not None
        assert result.availability.up is False  # alarm status → not resolved → down

    def test_up_field_true_means_up(self):
        """Payload with explicit 'up: true' should be detected as up."""
        payload = {
            "title": "ping check succeeded",
            "up": True,
            "latency_ms": 12,
        }
        adapter = UniversalAdapter()
        result = adapter.parse(payload)

        assert result.availability is not None
        assert result.availability.up is True
        assert result.availability.latency_ms == 12


# ======================================================================
# Integration test — ingest service writes uptime_samples
# ======================================================================


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


class TestIngestServiceAvailability:
    @pytest.mark.asyncio
    async def test_writes_uptime_sample_on_match(self, factory, db: AsyncSession):
        """When a matching SLA target exists, ingest should write an uptime sample."""
        from backend.ingest.service import ingest_incident
        from backend.db.models import IngestToken
        from backend.ingest.service import generate_token, hash_token

        # Create matching SLA target
        target = await SLATargetRepo.create(
            db,
            TEST_ORG_ID,
            name="web-api-health",
            kind="external",
        )

        # Create ingest token
        raw_token = generate_token()
        tok = IngestToken(
            org_id=TEST_ORG_ID,
            name="test-token",
            token_hash=hash_token(raw_token),
            provider="cloudwatch",
            is_active=True,
        )
        db.add(tok)
        await db.commit()
        await db.refresh(tok)

        config = AppConfig.load()
        cw_payload = {
            "Type": "Notification",
            "Message": json.dumps(
                {
                    "AlarmName": "web-api-health",
                    "NewStateValue": "ALARM",
                    "NewStateReason": "Threshold crossed",
                    "Region": "us-east-1",
                    "AWSAccountId": "123456789",
                }
            ),
        }

        result = await ingest_incident(
            db,
            token=tok,
            payload=cw_payload,
            config=config,
        )
        await db.commit()

        assert result.success is True

        # Check uptime sample was created
        samples = await UptimeSampleRepo.query_window(
            db,
            TEST_ORG_ID,
            target.id,
            since=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        assert len(samples) == 1
        assert samples[0].up is False
        assert samples[0].source == "cloudwatch"

    @pytest.mark.asyncio
    async def test_no_sample_when_no_target(self, factory, db: AsyncSession):
        """When no matching SLA target exists, no uptime sample should be created."""
        from backend.ingest.service import ingest_incident
        from backend.db.models import IngestToken
        from backend.ingest.service import generate_token, hash_token

        raw_token = generate_token()
        tok = IngestToken(
            org_id=TEST_ORG_ID,
            name="test-token-2",
            token_hash=hash_token(raw_token),
            provider="cloudwatch",
            is_active=True,
        )
        db.add(tok)
        await db.commit()
        await db.refresh(tok)

        config = AppConfig.load()
        cw_payload = {
            "Type": "Notification",
            "Message": json.dumps(
                {
                    "AlarmName": "no-matching-target",
                    "NewStateValue": "OK",
                    "NewStateReason": "All clear",
                    "Region": "us-west-2",
                    "AWSAccountId": "123456789",
                }
            ),
        }

        result = await ingest_incident(
            db,
            token=tok,
            payload=cw_payload,
            config=config,
        )
        await db.commit()

        assert result.success is True
        # Should have created an incident but no uptime sample
        stmt = select(UptimeSample)
        all_samples = (await db.execute(stmt)).scalars().all()
        assert len(all_samples) == 0
