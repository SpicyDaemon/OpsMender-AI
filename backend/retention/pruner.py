"""Sprint 53 — data retention pruner.

Per-org, per-category deletion of rows older than the configured TTL.

Hard rules
----------
- **Memories are never auto-deleted.** `incident_memories` is operator- or
  agent-curated, not log data. See D-025.
- **Per-org isolation preserved.** All deletes scope by ``org_id``.
- **Default TTL = 90 days** for every category unless the operator has
  configured otherwise (D-026, Sprint 53).
- **Explicit opt-out is honored.** ``ttl_days = NULL`` on a per-category row
  disables the pruner for that category.
- **Bounded per-run.** Each category caps at ``MAX_DELETES_PER_CATEGORY``
  per invocation so a backlog never holds a long transaction.
- **Best-effort.** Per-category failures are caught and reported in
  :class:`PrunerRunReport`; one bad category never aborts the rest.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from backend.db.models import (
    AuditEntry,
    BotActionAudit,
    IncidentMemory,
    IncidentMemoryRecallLog,
    IngestLog,
)
from backend.db.repos import (
    DEFAULT_RETENTION_TTL_DAYS,
    RETENTION_CATEGORIES,
    RetentionConfigRepo,
)

logger = logging.getLogger(__name__)

MAX_DELETES_PER_CATEGORY = 50_000
"""Hard ceiling on rows deleted per category per pruner invocation.

A backlog larger than this is split across runs so no single transaction
holds locks long enough to disrupt live traffic. Defaults to a generous
50k — the scheduler runs nightly, so a sustained ingest rate of ~500k log
rows/day would still keep pace.
"""


@dataclass
class PrunerResult:
    """Per-(org, category) outcome of a single pruner pass."""

    org_id: uuid.UUID
    category: str
    ttl_days: int | None
    cutoff: datetime | None
    deleted_count: int
    skipped_reason: str | None = None
    error: str | None = None


def _org_filter_direct(model: type[Any], org_id: uuid.UUID) -> ColumnElement[bool]:
    """Direct ``model.org_id = :org_id`` predicate."""
    return model.org_id == org_id


def _org_filter_recall_log(
    _model: type[Any], org_id: uuid.UUID
) -> ColumnElement[bool]:
    """`IncidentMemoryRecallLog` doesn't carry ``org_id`` directly; scope it
    via a sub-select against ``incident_memories``."""
    return IncidentMemoryRecallLog.memory_id.in_(
        select(IncidentMemory.id).where(IncidentMemory.org_id == org_id)
    )


@dataclass
class PrunerRunReport:
    """Aggregate of one full pruner invocation across (org, category) pairs."""

    started_at: datetime
    finished_at: datetime | None = None
    results: list[PrunerResult] = field(default_factory=list)

    @property
    def total_deleted(self) -> int:
        return sum(r.deleted_count for r in self.results)

    @property
    def total_errors(self) -> int:
        return sum(1 for r in self.results if r.error is not None)


# ---------------------------------------------------------------------------
# Per-category configuration
# ---------------------------------------------------------------------------

_CATEGORY_TABLES: tuple[tuple[str, type[Any], Any, Any], ...] = (
    ("audit_entries", AuditEntry, AuditEntry.timestamp, _org_filter_direct),
    ("ingest_log", IngestLog, IngestLog.created_at, _org_filter_direct),
    (
        "incident_memory_recall_log",
        IncidentMemoryRecallLog,
        IncidentMemoryRecallLog.surfaced_at,
        _org_filter_recall_log,
    ),
    ("bot_action_audit", BotActionAudit, BotActionAudit.created_at, _org_filter_direct),
)


def _lookup_category(category: str) -> tuple[type[Any], Any, Any]:
    for name, model, ts_col, org_filter in _CATEGORY_TABLES:
        if name == category:
            return model, ts_col, org_filter
    raise ValueError(f"Unknown retention category: {category}")


async def _delete_older_than(
    db: AsyncSession,
    *,
    model: type[Any],
    ts_col: Any,
    org_filter: Any,
    org_id: uuid.UUID,
    cutoff: datetime,
    limit: int,
) -> int:
    """Delete up to ``limit`` rows older than ``cutoff`` for this org.

    Uses a primary-key sub-select rather than a single bulk delete so the
    LIMIT is respected on both Postgres and SQLite (SQLite doesn't support
    DELETE … LIMIT directly).
    """
    scope = org_filter(model, org_id)
    pk_stmt = (
        select(model.id)
        .where(scope, ts_col < cutoff)
        .order_by(ts_col.asc())
        .limit(limit)
    )
    ids = [row for row in (await db.execute(pk_stmt)).scalars().all()]
    if not ids:
        return 0
    stmt = sql_delete(model).where(scope, model.id.in_(ids))
    result = await db.execute(stmt)
    return int(result.rowcount or len(ids))


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


async def prune_org(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> PrunerRunReport:
    """Run one pruner pass for every category on a single org.

    Memories are intentionally absent from the loop — they're operator- or
    agent-curated, never auto-deleted.
    """
    report = PrunerRunReport(started_at=datetime.now(timezone.utc))
    current = now or datetime.now(timezone.utc)

    for category in RETENTION_CATEGORIES:
        ttl_days = await RetentionConfigRepo.effective_ttl_days(
            db, org_id, category
        )
        if ttl_days is None:
            report.results.append(
                PrunerResult(
                    org_id=org_id,
                    category=category,
                    ttl_days=None,
                    cutoff=None,
                    deleted_count=0,
                    skipped_reason="disabled",
                )
            )
            continue
        cutoff = current - timedelta(days=ttl_days)
        try:
            model, ts_col, org_filter = _lookup_category(category)
            deleted = await _delete_older_than(
                db,
                model=model,
                ts_col=ts_col,
                org_filter=org_filter,
                org_id=org_id,
                cutoff=cutoff,
                limit=MAX_DELETES_PER_CATEGORY,
            )
            await RetentionConfigRepo.stamp_run(
                db, org_id, category=category, deleted_count=deleted
            )
            report.results.append(
                PrunerResult(
                    org_id=org_id,
                    category=category,
                    ttl_days=ttl_days,
                    cutoff=cutoff,
                    deleted_count=deleted,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Retention pruner failed for org=%s category=%s",
                org_id,
                category,
            )
            report.results.append(
                PrunerResult(
                    org_id=org_id,
                    category=category,
                    ttl_days=ttl_days,
                    cutoff=cutoff,
                    deleted_count=0,
                    error=str(exc),
                )
            )

    await db.commit()
    report.finished_at = datetime.now(timezone.utc)
    return report


async def estimate_storage_for_org(
    db: AsyncSession, org_id: uuid.UUID
) -> dict[str, dict[str, Any]]:
    """Best-effort per-category row-count + byte-size estimate for one org.

    Counts are exact; byte sizes use conservative per-row averages because
    cross-DB byte-accurate measurement (pg_total_relation_size on Postgres,
    page-size approximations on SQLite) would require dialect branching.
    For the operator's purpose — sizing storage and spotting unexpected
    growth — order-of-magnitude estimates are sufficient.
    """
    # Per-row byte estimates (rough; document in the UI).
    avg_bytes_per_row = {
        "audit_entries": 2_000,
        "ingest_log": 5_000,
        "incident_memory_recall_log": 200,
        "bot_action_audit": 1_000,
    }

    out: dict[str, dict[str, Any]] = {}
    for name, model, _ts_col, org_filter in _CATEGORY_TABLES:
        stmt = (
            select(func.count())
            .select_from(model)
            .where(org_filter(model, org_id))
        )
        count = int((await db.execute(stmt)).scalar() or 0)
        out[name] = {
            "row_count": count,
            "estimated_bytes": count * avg_bytes_per_row[name],
            "avg_bytes_per_row": avg_bytes_per_row[name],
        }

    # Memories are non-prunable but still helpful to surface so an operator
    # can see what's growing under their feet.
    mem_stmt = (
        select(func.count())
        .select_from(IncidentMemory)
        .where(IncidentMemory.org_id == org_id)
    )
    mem_count = int((await db.execute(mem_stmt)).scalar() or 0)
    out["incident_memories"] = {
        "row_count": mem_count,
        "estimated_bytes": mem_count * 3_000,
        "avg_bytes_per_row": 3_000,
        "non_prunable": True,
    }
    return out


async def prune_all_orgs(
    db: AsyncSession,
    org_ids: Iterable[uuid.UUID],
    *,
    now: datetime | None = None,
) -> list[PrunerRunReport]:
    """Convenience wrapper for the scheduler — runs prune_org per org."""
    reports: list[PrunerRunReport] = []
    for org_id in org_ids:
        try:
            reports.append(await prune_org(db, org_id, now=now))
        except Exception:  # noqa: BLE001
            logger.exception("Retention prune_org failed for org=%s", org_id)
            failure = PrunerRunReport(
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
            )
            failure.results.append(
                PrunerResult(
                    org_id=org_id,
                    category="*",
                    ttl_days=None,
                    cutoff=None,
                    deleted_count=0,
                    error="prune_org raised before returning",
                )
            )
            reports.append(failure)
    return reports
