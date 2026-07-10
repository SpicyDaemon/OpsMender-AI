"""Sprint 53 — nightly data-retention scheduler.

Sleeps ``poll_interval_seconds`` (default 6h) between passes; each pass
walks every known org and runs :func:`backend.retention.pruner.prune_org`.
Defaults to ENABLED so a fresh deployment auto-prunes from day one without
operator action — operators can disable per-category via Config →
"Storage & retention," or disable the whole loop via
``OPSMENDER_RETENTION_ENABLED=false``.

The loop is restart-safe by design: pruning is idempotent (rows older than
the cutoff are always candidates regardless of when the last run happened),
and per-(org, category) outcome is recorded on the ``retention_configs``
row so operators can see the last-pruned timestamp in the UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import Organization
from backend.retention.pruner import prune_org

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def retention_enabled_from_env() -> bool:
    # Sprint 53 D-026 default: enabled. Operator opts out via env or per-category UI.
    return _env_truthy("OPSMENDER_RETENTION_ENABLED", default=True)


class RetentionScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: int = 6 * 60 * 60,  # 6 hours
        enabled: bool | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._enabled = enabled if enabled is not None else retention_enabled_from_env()
        self._task: asyncio.Task | None = None
        self._last_run_at: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def last_run_at(self) -> datetime | None:
        return self._last_run_at

    async def start(self) -> None:
        if not self._enabled:
            logger.info(
                "Retention scheduler is disabled (OPSMENDER_RETENTION_ENABLED=false)."
            )
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(), name="opsmender-retention-scheduler"
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
                await self.run_once()
            except Exception:  # noqa: BLE001
                logger.exception("Retention scheduler pass failed")
            try:
                await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                break

    async def run_once(self) -> int:
        """One full pass across every org. Returns the total deleted count."""
        total_deleted = 0
        async with self._session_factory() as db:
            org_ids = (await db.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                report = await prune_org(db, org_id, skip_categories={"audit_entries"})
                total_deleted += report.total_deleted
                if report.total_deleted > 0 or report.total_errors > 0:
                    logger.info(
                        "Retention pruner org=%s deleted=%d errors=%d",
                        org_id,
                        report.total_deleted,
                        report.total_errors,
                    )
        self._last_run_at = datetime.now(timezone.utc)
        return total_deleted
