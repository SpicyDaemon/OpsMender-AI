"""Audit-entry retention and S3-compatible compressed archive service."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config_loader import AuditConfig
from backend.db.models import AuditEntry, Organization
from backend.db.repos import RetentionConfigRepo

logger = logging.getLogger(__name__)


@dataclass
class AuditArchiveReport:
    candidate_count: int = 0
    archived_count: int = 0
    deleted_count: int = 0
    object_keys: list[str] = field(default_factory=list)
    deleted_by_org: dict[uuid.UUID, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _row_dict(row: AuditEntry) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "org_id": str(row.org_id),
        "session_id": str(row.session_id),
        "timestamp": _utc(row.timestamp).isoformat(),
        "tier": row.tier,
        "entry_type": row.entry_type,
        "tool_name": row.tool_name,
        "tool_parameters": row.tool_parameters,
        "result": row.result,
        "permitted": row.permitted,
        "block_reason": row.block_reason,
        "duration_ms": row.duration_ms,
    }


def _gzip_jsonl(rows: list[AuditEntry], existing: bytes | None = None) -> bytes:
    records: dict[str, dict[str, Any]] = {}
    if existing:
        try:
            raw = gzip.decompress(existing).decode("utf-8")
            for line in raw.splitlines():
                if not line:
                    continue
                item = json.loads(line)
                records[str(item["id"])] = item
        except (OSError, UnicodeDecodeError, ValueError, KeyError) as exc:
            raise ValueError("Existing audit archive object is unreadable") from exc
    for row in rows:
        item = _row_dict(row)
        records[item["id"]] = item
    content = "\n".join(
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in sorted(records.values(), key=lambda value: value["timestamp"])
    )
    return gzip.compress((content + "\n").encode("utf-8"), mtime=0)


class AuditArchiver:
    """Archive expired audit rows, then delete only confirmed uploads."""

    def __init__(
        self,
        config: AuditConfig,
        *,
        s3_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._config = config
        self._s3_client_factory = s3_client_factory or self._build_s3_client

    def _build_s3_client(self):
        kwargs: dict[str, Any] = {}
        if self._config.archive_s3_endpoint_url:
            kwargs["endpoint_url"] = self._config.archive_s3_endpoint_url
        if self._config.archive_aws_access_key_id:
            kwargs["aws_access_key_id"] = self._config.archive_aws_access_key_id
        if self._config.archive_aws_access_key_secret:
            kwargs["aws_secret_access_key"] = self._config.archive_aws_access_key_secret
        return boto3.client("s3", **kwargs)

    def _object_key(self, archive_date: str) -> str:
        return f"{self._config.archive_s3_prefix}{archive_date}.jsonl.gz"

    @staticmethod
    def _existing_object(client, bucket: str, key: str) -> bytes | None:
        try:
            response = client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return response["Body"].read()

    def _upload(self, key: str, rows: list[AuditEntry]) -> None:
        bucket = self._config.archive_s3_bucket
        if not bucket:
            raise ValueError(
                "AUDIT_ARCHIVE_S3_BUCKET is required when archival is enabled"
            )
        client = self._s3_client_factory()
        existing = self._existing_object(client, bucket, key)
        response = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=_gzip_jsonl(rows, existing),
            ContentType="application/x-ndjson",
            ContentEncoding="gzip",
        )
        status_code = int(response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if status_code < 200 or status_code >= 300:
            raise RuntimeError(f"S3 put_object returned HTTP {status_code}")

    async def _candidate_rows(
        self,
        db: AsyncSession,
        *,
        now: datetime,
        org_id: uuid.UUID | None,
    ) -> list[AuditEntry]:
        org_ids = (
            [org_id]
            if org_id is not None
            else list((await db.execute(select(Organization.id))).scalars().all())
        )
        rows: list[AuditEntry] = []
        for current_org_id in org_ids:
            ttl_days = await RetentionConfigRepo.effective_ttl_days(
                db,
                current_org_id,
                "audit_entries",
                default_ttl_days=self._config.retention_days,
            )
            if ttl_days is None:
                continue
            cutoff = now - timedelta(days=ttl_days)
            rows.extend(
                (
                    await db.execute(
                        select(AuditEntry)
                        .where(
                            AuditEntry.org_id == current_org_id,
                            AuditEntry.timestamp < cutoff,
                        )
                        .order_by(AuditEntry.timestamp.asc(), AuditEntry.id.asc())
                    )
                )
                .scalars()
                .all()
            )
        return rows

    async def _delete_rows(
        self,
        db: AsyncSession,
        rows: list[AuditEntry],
        report: AuditArchiveReport,
    ) -> None:
        if not rows:
            return
        ids = [row.id for row in rows]
        await db.execute(delete(AuditEntry).where(AuditEntry.id.in_(ids)))
        counts: dict[uuid.UUID, int] = defaultdict(int)
        for row in rows:
            counts[row.org_id] += 1
        for current_org_id, count in counts.items():
            cumulative_count = report.deleted_by_org.get(current_org_id, 0) + count
            await RetentionConfigRepo.stamp_run(
                db,
                current_org_id,
                category="audit_entries",
                deleted_count=cumulative_count,
                default_ttl_days=self._config.retention_days,
            )
            report.deleted_by_org[current_org_id] = cumulative_count
        await db.commit()
        report.deleted_count += len(rows)

    async def archive_and_prune(
        self,
        db: AsyncSession,
        *,
        now: datetime | None = None,
        org_id: uuid.UUID | None = None,
    ) -> AuditArchiveReport:
        current = now or datetime.now(timezone.utc)
        report = AuditArchiveReport()
        rows = await self._candidate_rows(db, now=current, org_id=org_id)
        report.candidate_count = len(rows)
        if not rows:
            return report

        if not self._config.archive_enabled:
            await self._delete_rows(db, rows, report)
            return report

        grouped: dict[str, list[AuditEntry]] = defaultdict(list)
        for row in rows:
            grouped[_utc(row.timestamp).date().isoformat()].append(row)

        for archive_date, date_rows in sorted(grouped.items()):
            key = self._object_key(archive_date)
            try:
                await asyncio.to_thread(self._upload, key, date_rows)
            except Exception as exc:  # noqa: BLE001 - archival must be non-fatal
                message = f"{key}: {exc}"
                report.errors.append(message)
                logger.exception("Audit archive upload failed for %s", key)
                continue
            report.object_keys.append(key)
            report.archived_count += len(date_rows)
            await self._delete_rows(db, date_rows, report)
        return report


def seconds_until_next_0200(now: datetime | None = None) -> float:
    current = now or datetime.now(timezone.utc)
    current = _utc(current)
    target = datetime.combine(current.date(), time(hour=2), tzinfo=timezone.utc)
    if target <= current:
        target += timedelta(days=1)
    return max(0.0, (target - current).total_seconds())


class AuditArchiveScheduler:
    """Run audit archival once per day at 02:00 UTC."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: AuditConfig,
        *,
        archiver: AuditArchiver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._archiver = archiver or AuditArchiver(config)
        self._task: asyncio.Task | None = None
        self._last_run_at: datetime | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(), name="opsmender-audit-archive-scheduler"
            )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(seconds_until_next_0200())
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 - scheduler must stay alive
                logger.exception("Audit archive scheduler pass failed")

    async def run_once(self) -> AuditArchiveReport:
        async with self._session_factory() as db:
            report = await self._archiver.archive_and_prune(db)
        self._last_run_at = datetime.now(timezone.utc)
        return report
