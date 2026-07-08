"""Audit retention and S3-compatible archive tests."""

from __future__ import annotations

import gzip
import json
import uuid
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes.retention import _to_config_items
from backend.config_loader import AppConfig, AuditConfig
from backend.db.models import (
    AuditEntry,
    Base,
    Incident,
    Organization,
    Session as SessionModel,
)
from backend.services.audit_archiver import AuditArchiver, seconds_until_next_0200

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        org = Organization(id=ORG_ID, name="Archive Test", slug="archive-test")
        incident = Incident(
            id=uuid.uuid4(),
            org_id=ORG_ID,
            title="Archive test incident",
            description="test",
            status="open",
        )
        db.add_all([org, incident])
        await db.flush()
        session = SessionModel(
            id=uuid.uuid4(),
            org_id=ORG_ID,
            incident_id=incident.id,
            tier=2,
            status="active",
        )
        db.add(session)
        await db.commit()
        session_id = session.id
    yield session_factory, session_id
    await engine.dispose()


async def _seed_entry(
    session_factory,
    session_id: uuid.UUID,
    *,
    timestamp: datetime,
    tool_name: str,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            AuditEntry(
                id=entry_id,
                org_id=ORG_ID,
                session_id=session_id,
                timestamp=timestamp,
                tier=2,
                entry_type="tool_call_end",
                tool_name=tool_name,
                tool_parameters={"command": "status"},
                result={"ok": True},
                permitted=True,
                duration_ms=12,
            )
        )
        await db.commit()
    return entry_id


async def _entry_ids(session_factory) -> set[uuid.UUID]:
    async with session_factory() as db:
        return set((await db.execute(select(AuditEntry.id))).scalars().all())


async def test_archive_then_prune_respects_retention_boundary(factory):
    with mock_aws():
        session_factory, session_id = factory
        now = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
        expired_id = await _seed_entry(
            session_factory,
            session_id,
            timestamp=now - timedelta(days=91),
            tool_name="expired",
        )
        retained_id = await _seed_entry(
            session_factory,
            session_id,
            timestamp=now - timedelta(days=89),
            tool_name="retained",
        )
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        client.create_bucket(Bucket="audit-archive")
        config = AuditConfig(
            retention_days=90,
            archive_enabled=True,
            archive_s3_bucket="audit-archive",
            archive_s3_prefix="opsmender/audit/",
            archive_aws_access_key_id="test",
            archive_aws_access_key_secret="test",
        )

        async with session_factory() as db:
            report = await AuditArchiver(
                config, s3_client_factory=lambda: client
            ).archive_and_prune(db, now=now)

        key = f"opsmender/audit/{(now - timedelta(days=91)).date()}.jsonl.gz"
        archived = client.get_object(Bucket="audit-archive", Key=key)["Body"].read()
        records = [
            json.loads(line)
            for line in gzip.decompress(archived).decode("utf-8").splitlines()
        ]
        assert report.candidate_count == 1
        assert report.archived_count == 1
        assert report.deleted_count == 1
        assert report.object_keys == [key]
        assert records[0]["id"] == str(expired_id)
        assert records[0]["tool_name"] == "expired"
        assert await _entry_ids(session_factory) == {retained_id}


async def test_archive_disabled_prunes_without_creating_s3_client(factory):
    session_factory, session_id = factory
    now = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
    expired_id = await _seed_entry(
        session_factory,
        session_id,
        timestamp=now - timedelta(days=91),
        tool_name="expired",
    )

    def fail_if_called():
        raise AssertionError("S3 client must not be created when archival is disabled")

    async with session_factory() as db:
        report = await AuditArchiver(
            AuditConfig(retention_days=90, archive_enabled=False),
            s3_client_factory=fail_if_called,
        ).archive_and_prune(db, now=now)

    assert report.candidate_count == 1
    assert report.archived_count == 0
    assert report.deleted_count == 1
    assert expired_id not in await _entry_ids(session_factory)


async def test_upload_failure_does_not_delete_rows(factory):
    with mock_aws():
        session_factory, session_id = factory
        now = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
        expired_id = await _seed_entry(
            session_factory,
            session_id,
            timestamp=now - timedelta(days=91),
            tool_name="must-survive",
        )
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        client.create_bucket(Bucket="audit-archive")

        class FailingUploadClient:
            def get_object(self, **kwargs):
                return client.get_object(**kwargs)

            def put_object(self, **kwargs):
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": "failed"}},
                    "PutObject",
                )

        config = AuditConfig(
            retention_days=90,
            archive_enabled=True,
            archive_s3_bucket="audit-archive",
        )
        async with session_factory() as db:
            report = await AuditArchiver(
                config, s3_client_factory=FailingUploadClient
            ).archive_and_prune(db, now=now)

        assert report.candidate_count == 1
        assert report.archived_count == 0
        assert report.deleted_count == 0
        assert len(report.errors) == 1
        assert await _entry_ids(session_factory) == {expired_id}


def test_next_archive_run_is_0200_utc():
    assert (
        seconds_until_next_0200(datetime(2026, 6, 20, 1, 30, tzinfo=timezone.utc))
        == 30 * 60
    )
    assert (
        seconds_until_next_0200(datetime(2026, 6, 20, 2, 0, tzinfo=timezone.utc))
        == 24 * 60 * 60
    )


def test_audit_timestamp_index_is_declared():
    assert "ix_audit_entries_timestamp" in {
        index.name for index in AuditEntry.__table__.indexes
    }


def test_audit_archive_environment_config(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AUDIT_RETENTION_DAYS=45",
                "AUDIT_ARCHIVE_ENABLED=true",
                "AUDIT_ARCHIVE_S3_BUCKET=audit-bucket",
                "AUDIT_ARCHIVE_S3_PREFIX=archives/",
                "AUDIT_ARCHIVE_S3_ENDPOINT_URL=https://objects.example.test",
                "AUDIT_ARCHIVE_AWS_ACCESS_KEY_ID=access",
                "AUDIT_ARCHIVE_AWS_ACCESS_KEY_SECRET=secret",
            ]
        ),
        encoding="utf-8",
    )

    config = AppConfig.load(env_file).audit

    assert config.retention_days == 45
    assert config.archive_enabled is True
    assert config.archive_s3_bucket == "audit-bucket"
    assert config.archive_s3_prefix == "archives/"
    assert config.archive_s3_endpoint_url == "https://objects.example.test"
    assert config.archive_aws_access_key_id == "access"
    assert config.archive_aws_access_key_secret == "secret"


def test_retention_status_uses_configured_audit_default():
    items = _to_config_items({}, audit_default_ttl_days=45)
    by_category = {item.category: item for item in items}

    assert by_category["audit_entries"].ttl_days == 45
    assert by_category["ingest_log"].ttl_days == 90
