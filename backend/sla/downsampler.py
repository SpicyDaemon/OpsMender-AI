"""Downsampling job for uptime samples (Sprint 25).

Rolls raw 1-minute uptime samples into 5-minute and 1-hour aggregate
buckets, then prunes raw samples older than 30 days.

The job runs as a background asyncio task inside the FastAPI lifespan,
executing once per ``run_interval_seconds`` (default: 1 hour).  On each
tick it:

1. Groups un-aggregated raw samples into 5m buckets → writes to
   ``uptime_samples_5m``.
2. Groups un-aggregated 5m buckets into 1h buckets → writes to
   ``uptime_samples_1h``.
3. Deletes raw ``uptime_samples`` rows older than ``retention_days``
   (default: 30).

Idempotency: the job skips buckets that already have an aggregate row
(keyed by target_id + bucket_start) so re-runs are safe.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import UptimeSample, UptimeSample1h, UptimeSample5m

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Bucket widths
_5M = timedelta(minutes=5)
_1H = timedelta(hours=1)


def _floor_5m(dt: datetime) -> datetime:
    """Round *dt* down to the nearest 5-minute boundary."""
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def _floor_1h(dt: datetime) -> datetime:
    """Round *dt* down to the nearest hour boundary."""
    return dt.replace(minute=0, second=0, microsecond=0)


class UptimeDownsampler:
    """Background job that rolls up raw samples into aggregates."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        run_interval_seconds: int = 3600,
        retention_days: int = 30,
    ) -> None:
        self._session_factory = session_factory
        self._run_interval_seconds = run_interval_seconds
        self._retention_days = retention_days
        self._task: asyncio.Task | None = None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(), name="opsmender-uptime-downsampler"
        )
        logger.info(
            "Uptime downsampler started (interval=%ds, retention=%dd)",
            self._run_interval_seconds,
            self._retention_days,
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
        logger.info("Uptime downsampler stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("Uptime downsampler tick failed: %s", exc)
            await asyncio.sleep(self._run_interval_seconds)

    # -- public entry point (also used by tests / CLI) ----------------------

    async def run_once(self) -> dict[str, int]:
        """Execute one full downsampling pass.

        Returns a dict with counts: ``buckets_5m``, ``buckets_1h``,
        ``pruned_raw``.
        """
        async with self._session_factory() as db:
            b5 = await self._roll_to_5m(db)
            b1h = await self._roll_to_1h(db)
            pruned = await self._prune_raw(db)
            await db.commit()

        logger.info(
            "Downsampler pass: 5m_buckets=%d, 1h_buckets=%d, pruned_raw=%d",
            b5,
            b1h,
            pruned,
        )
        return {"buckets_5m": b5, "buckets_1h": b1h, "pruned_raw": pruned}

    # -- 5-minute aggregation -----------------------------------------------

    async def _roll_to_5m(self, db: AsyncSession) -> int:
        """Aggregate raw samples into 5-minute buckets.

        We find the range of raw samples that haven't been covered by a
        5m bucket yet, group by (target_id, 5m_floor), compute up_pct,
        and insert new bucket rows.
        """
        # Find the latest 5m bucket per target so we know where to start
        cutoff = datetime.now(timezone.utc) - _5M  # don't aggregate current bucket

        # Get all raw samples older than the current 5m bucket
        stmt = (
            select(UptimeSample)
            .where(UptimeSample.observed_at < cutoff)
            .order_by(UptimeSample.observed_at)
        )
        result = await db.execute(stmt)
        samples = result.scalars().all()

        if not samples:
            return 0

        # Group by (target_id, 5m bucket_start)
        buckets: dict[tuple, list] = {}
        for s in samples:
            bucket_start = _floor_5m(s.observed_at)
            key = (s.target_id, bucket_start)
            buckets.setdefault(key, []).append(s)

        inserted = 0
        for (target_id, bucket_start), group in buckets.items():
            # Check if bucket already exists (idempotency)
            existing = await db.execute(
                select(UptimeSample5m).where(
                    UptimeSample5m.target_id == target_id,
                    UptimeSample5m.bucket_start == bucket_start,
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue

            # Only count non-suppressed for up_pct
            non_suppressed = [s for s in group if not s.suppressed]
            total = len(non_suppressed)
            if total == 0:
                up_pct = 1.0
            else:
                up_pct = sum(1 for s in non_suppressed if s.up) / total

            latencies = [s.latency_ms for s in group if s.latency_ms is not None]
            avg_latency = (
                round(sum(latencies) / len(latencies), 2) if latencies else None
            )

            row = UptimeSample5m(
                org_id=group[0].org_id,
                target_id=target_id,
                bucket_start=bucket_start,
                up_pct=round(up_pct, 4),
                total_samples=len(group),
                avg_latency_ms=avg_latency,
                min_latency_ms=min(latencies) if latencies else None,
                max_latency_ms=max(latencies) if latencies else None,
                latency_samples=len(latencies),
            )
            db.add(row)
            inserted += 1

        if inserted:
            await db.flush()
        return inserted

    # -- 1-hour aggregation -------------------------------------------------

    async def _roll_to_1h(self, db: AsyncSession) -> int:
        """Aggregate 5m buckets into 1-hour buckets."""
        cutoff = datetime.now(timezone.utc) - _1H  # don't aggregate current hour

        stmt = (
            select(UptimeSample5m)
            .where(UptimeSample5m.bucket_start < cutoff)
            .order_by(UptimeSample5m.bucket_start)
        )
        result = await db.execute(stmt)
        buckets_5m = result.scalars().all()

        if not buckets_5m:
            return 0

        # Group by (target_id, 1h bucket_start)
        hourly: dict[tuple, list] = {}
        for b in buckets_5m:
            hour_start = _floor_1h(b.bucket_start)
            key = (b.target_id, hour_start)
            hourly.setdefault(key, []).append(b)

        inserted = 0
        for (target_id, hour_start), group in hourly.items():
            # Check if hour bucket already exists (idempotency)
            existing = await db.execute(
                select(UptimeSample1h).where(
                    UptimeSample1h.target_id == target_id,
                    UptimeSample1h.bucket_start == hour_start,
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue

            # Weighted average: each 5m bucket contributes proportionally
            total_samples = sum(b.total_samples for b in group)
            if total_samples == 0:
                up_pct = 1.0
            else:
                weighted_sum = sum(b.up_pct * b.total_samples for b in group)
                up_pct = weighted_sum / total_samples

            # Weight each 5m average by the number of latency-bearing raw
            # samples. This keeps the hourly average exact even when an ingest
            # source reports availability without latency.
            lat_buckets = [
                b
                for b in group
                if b.avg_latency_ms is not None and b.latency_samples > 0
            ]
            if lat_buckets:
                latency_samples = sum(b.latency_samples for b in lat_buckets)
                avg_latency = round(
                    sum(b.avg_latency_ms * b.latency_samples for b in lat_buckets)
                    / latency_samples,
                    2,
                )
                min_latency = min(
                    b.min_latency_ms
                    for b in lat_buckets
                    if b.min_latency_ms is not None
                )
                max_latency = max(
                    b.max_latency_ms
                    for b in lat_buckets
                    if b.max_latency_ms is not None
                )
            else:
                avg_latency = min_latency = max_latency = None
                latency_samples = 0

            row = UptimeSample1h(
                org_id=group[0].org_id,
                target_id=target_id,
                bucket_start=hour_start,
                up_pct=round(up_pct, 4),
                total_samples=total_samples,
                avg_latency_ms=avg_latency,
                min_latency_ms=min_latency,
                max_latency_ms=max_latency,
                latency_samples=latency_samples,
            )
            db.add(row)
            inserted += 1

        if inserted:
            await db.flush()
        return inserted

    # -- raw sample pruning -------------------------------------------------

    async def _prune_raw(self, db: AsyncSession) -> int:
        """Delete raw uptime samples older than retention_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        stmt = delete(UptimeSample).where(UptimeSample.observed_at < cutoff)
        result = await db.execute(stmt)
        return int(result.rowcount or 0)
