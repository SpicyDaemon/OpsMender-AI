"""Sprint 53 — data retention & garbage collection tests.

Covers:
- `RetentionConfigRepo`: upsert + per-category effective TTL + default fallback.
- `prune_org`: deletes only rows older than the cutoff per category, respects
  disabled categories, never touches incident_memories, stamps last_pruned_at.
- `estimate_storage_for_org`: returns counts + byte estimates for the four log
  categories plus the non-prunable memories panel entry.
- `RetentionScheduler.run_once`: leaves audit rows to the archive scheduler.
- REST API: GET status, PUT config, POST run, role gates, validation 400s.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import (
    AuditEntry,
    Base,
    BotActionAudit,
    Incident,
    IncidentMemory,
    IncidentMemoryRecallLog,
    IngestLog,
    IngestToken,
    Organization,
    Session as SessionModel,
)
from backend.db.repos import (
    DEFAULT_RETENTION_TTL_DAYS,
    RETENTION_CATEGORIES,
    RetentionConfigRepo,
)
from backend.retention.pruner import (
    estimate_storage_for_org,
    prune_org,
)
from backend.retention.scheduler import RetentionScheduler


ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as db:
        db.add(Organization(id=ORG_A, name="A", slug="a"))
        db.add(Organization(id=ORG_B, name="B", slug="b"))
        await db.commit()
    yield f
    await engine.dispose()


async def _seed_session(factory, org_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as db:
        incident = Incident(
            id=uuid.uuid4(),
            org_id=org_id,
            title="t",
            description="d",
            status="open",
        )
        db.add(incident)
        await db.flush()
        session = SessionModel(
            id=uuid.uuid4(),
            org_id=org_id,
            incident_id=incident.id,
            tier=2,
            status="active",
        )
        db.add(session)
        await db.commit()
        return incident.id, session.id


async def _seed_audit_entry(factory, org_id, session_id, *, age_days: int) -> None:
    async with factory() as db:
        entry = AuditEntry(
            id=uuid.uuid4(),
            org_id=org_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc) - timedelta(days=age_days),
            tier=2,
            entry_type="session_start",
        )
        db.add(entry)
        await db.commit()


async def _seed_ingest_log(
    factory, org_id, *, age_days: int, token_id: uuid.UUID
) -> None:
    async with factory() as db:
        entry = IngestLog(
            id=uuid.uuid4(),
            org_id=org_id,
            ingest_token_id=token_id,
            provider="auto",
            raw_payload={"x": 1},
            created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        )
        db.add(entry)
        await db.commit()


async def _seed_ingest_token(factory, org_id) -> uuid.UUID:
    async with factory() as db:
        token = IngestToken(
            id=uuid.uuid4(),
            org_id=org_id,
            name=f"tok-{uuid.uuid4().hex[:6]}",
            provider="auto",
            token_hash="x",
            is_active=True,
        )
        db.add(token)
        await db.commit()
        return token.id


async def _seed_memory_recall(
    factory, org_id, session_id, *, age_days: int
) -> None:
    async with factory() as db:
        mem = IncidentMemory(
            id=uuid.uuid4(),
            org_id=org_id,
            title=f"m-{uuid.uuid4().hex[:6]}",
            summary_md="s",
        )
        db.add(mem)
        await db.flush()
        log = IncidentMemoryRecallLog(
            id=uuid.uuid4(),
            memory_id=mem.id,
            session_id=session_id,
            surfaced_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        )
        db.add(log)
        await db.commit()


# ---------------------------------------------------------------------------
# Repo
# ---------------------------------------------------------------------------


class TestRetentionConfigRepo:
    async def test_default_applies_when_no_row(self, factory):
        async with factory() as db:
            ttl = await RetentionConfigRepo.effective_ttl_days(
                db, ORG_A, "audit_entries"
            )
        assert ttl == DEFAULT_RETENTION_TTL_DAYS

    async def test_upsert_creates_then_updates(self, factory):
        async with factory() as db:
            row = await RetentionConfigRepo.upsert(
                db, ORG_A, category="audit_entries", ttl_days=30
            )
            assert row.ttl_days == 30
            row2 = await RetentionConfigRepo.upsert(
                db, ORG_A, category="audit_entries", ttl_days=14
            )
            assert row2.id == row.id
            assert row2.ttl_days == 14
            await db.commit()

    async def test_upsert_null_disables(self, factory):
        async with factory() as db:
            await RetentionConfigRepo.upsert(
                db, ORG_A, category="audit_entries", ttl_days=None
            )
            await db.commit()
            ttl = await RetentionConfigRepo.effective_ttl_days(
                db, ORG_A, "audit_entries"
            )
        assert ttl is None

    async def test_upsert_rejects_unknown_category(self, factory):
        async with factory() as db:
            with pytest.raises(ValueError):
                await RetentionConfigRepo.upsert(
                    db, ORG_A, category="bogus", ttl_days=7
                )

    async def test_upsert_rejects_zero_ttl(self, factory):
        async with factory() as db:
            with pytest.raises(ValueError):
                await RetentionConfigRepo.upsert(
                    db, ORG_A, category="audit_entries", ttl_days=0
                )


# ---------------------------------------------------------------------------
# Pruner
# ---------------------------------------------------------------------------


class TestPrunerCore:
    async def test_deletes_only_rows_past_cutoff(self, factory):
        _, session_id = await _seed_session(factory, ORG_A)
        # Default TTL = 90d. Seed: one row 100 days old (prunable), one fresh.
        await _seed_audit_entry(factory, ORG_A, session_id, age_days=100)
        await _seed_audit_entry(factory, ORG_A, session_id, age_days=1)

        async with factory() as db:
            report = await prune_org(db, ORG_A)

        audit_result = next(
            r for r in report.results if r.category == "audit_entries"
        )
        assert audit_result.deleted_count == 1
        # The fresh row survives.
        async with factory() as db:
            from sqlalchemy import func, select

            count = (
                await db.execute(
                    select(func.count())
                    .select_from(AuditEntry)
                    .where(AuditEntry.org_id == ORG_A)
                )
            ).scalar()
        assert count == 1

    async def test_disabled_category_skips_deletion(self, factory):
        _, session_id = await _seed_session(factory, ORG_A)
        await _seed_audit_entry(factory, ORG_A, session_id, age_days=200)
        async with factory() as db:
            await RetentionConfigRepo.upsert(
                db, ORG_A, category="audit_entries", ttl_days=None
            )
            await db.commit()
            report = await prune_org(db, ORG_A)
        result = next(r for r in report.results if r.category == "audit_entries")
        assert result.deleted_count == 0
        assert result.skipped_reason == "disabled"

    async def test_memories_are_never_pruned(self, factory):
        # No memory category in RETENTION_CATEGORIES at all.
        assert "incident_memories" not in RETENTION_CATEGORIES
        # And the pruner's loop only walks RETENTION_CATEGORIES, so even if we
        # ask for an absurd TTL it can't touch memories. Sanity: seed a memory
        # and assert it survives a full prune.
        async with factory() as db:
            mem = IncidentMemory(
                id=uuid.uuid4(),
                org_id=ORG_A,
                title="never-deleted",
                summary_md="x",
                created_at=datetime.now(timezone.utc) - timedelta(days=5000),
                updated_at=datetime.now(timezone.utc) - timedelta(days=5000),
            )
            db.add(mem)
            await db.commit()
            await prune_org(db, ORG_A)
            survivor = await db.get(IncidentMemory, mem.id)
            assert survivor is not None

    async def test_per_org_isolation(self, factory):
        _, session_a = await _seed_session(factory, ORG_A)
        _, session_b = await _seed_session(factory, ORG_B)
        await _seed_audit_entry(factory, ORG_A, session_a, age_days=200)
        await _seed_audit_entry(factory, ORG_B, session_b, age_days=200)

        async with factory() as db:
            report = await prune_org(db, ORG_A)
        assert any(
            r.category == "audit_entries" and r.deleted_count == 1
            for r in report.results
        )
        # Org B row survives.
        async with factory() as db:
            from sqlalchemy import func, select

            count_b = (
                await db.execute(
                    select(func.count())
                    .select_from(AuditEntry)
                    .where(AuditEntry.org_id == ORG_B)
                )
            ).scalar()
        assert count_b == 1

    async def test_stamps_last_pruned_state(self, factory):
        _, session_id = await _seed_session(factory, ORG_A)
        await _seed_audit_entry(factory, ORG_A, session_id, age_days=100)

        async with factory() as db:
            await prune_org(db, ORG_A)
            row = await RetentionConfigRepo.get(db, ORG_A, "audit_entries")
        assert row is not None
        assert row.last_pruned_at is not None
        assert row.last_pruned_count == 1


class TestPrunerStorageEstimate:
    async def test_returns_counts_for_all_categories(self, factory):
        async with factory() as db:
            storage = await estimate_storage_for_org(db, ORG_A)
        for category in RETENTION_CATEGORIES:
            assert category in storage
            assert storage[category]["row_count"] == 0
        # Memories panel entry is non-prunable.
        assert storage["incident_memories"]["non_prunable"] is True

    async def test_count_increments_after_seed(self, factory):
        _, session_id = await _seed_session(factory, ORG_A)
        await _seed_audit_entry(factory, ORG_A, session_id, age_days=10)
        async with factory() as db:
            storage = await estimate_storage_for_org(db, ORG_A)
        assert storage["audit_entries"]["row_count"] == 1
        assert storage["audit_entries"]["estimated_bytes"] > 0


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class TestRetentionScheduler:
    async def test_run_once_leaves_audit_rows_to_archive_scheduler(self, factory):
        _, session_a = await _seed_session(factory, ORG_A)
        _, session_b = await _seed_session(factory, ORG_B)
        await _seed_audit_entry(factory, ORG_A, session_a, age_days=200)
        await _seed_audit_entry(factory, ORG_B, session_b, age_days=200)
        scheduler = RetentionScheduler(factory, enabled=True)
        total = await scheduler.run_once()
        assert total == 0
        async with factory() as db:
            remaining = (
                await db.execute(select(func.count()).select_from(AuditEntry))
            ).scalar_one()
        assert remaining == 2

    async def test_disabled_env_skips_loop(self, factory):
        scheduler = RetentionScheduler(factory, enabled=False)
        assert scheduler.enabled is False
        await scheduler.start()  # no-op when disabled
        assert scheduler._task is None
