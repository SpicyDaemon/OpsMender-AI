"""Background scheduler for audit runs (Sprint 39 step 2).

Wakes every ``poll_interval_seconds`` (default 60s), polls
``audit_schedules`` for rows whose ``next_run_at`` has passed, creates a
queued ``audit_runs`` row, fans out through :func:`run_audit`, and
advances the schedule's ``last_run_at`` / ``next_run_at``.

Mirrors the shape of :class:`backend.paging.scheduler.EscalationScheduler`
— restart-safe via the schedule's own ``next_run_at`` watermark.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.auditor.runner import run_audit
from backend.config_loader import AppConfig
from backend.db.repos import AuditRunRepo, AuditScheduleRepo
from backend.mcp.pool import MCPServerPool


logger = logging.getLogger(__name__)


class AuditScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        pool: MCPServerPool,
        config: AppConfig,
        poll_interval_seconds: int = 60,
    ) -> None:
        self._session_factory = session_factory
        self._pool = pool
        self._config = config
        self._poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(), name="opsmender-audit-scheduler"
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
                await self.tick(now=datetime.now(timezone.utc))
            except Exception as exc:  # noqa: BLE001
                logger.warning("auditor.scheduler tick failed: %s", exc)
            await asyncio.sleep(self._poll_interval_seconds)

    async def tick(self, *, now: datetime) -> int:
        """Fire every due schedule. Returns the count fired.

        Exposed for tests so they can drive the scheduler synchronously
        without spinning up the asyncio loop.
        """

        fired = 0
        async with self._session_factory() as db:
            try:
                due = await AuditScheduleRepo.list_due(db, now=now)
                for schedule in due:
                    await self._fire(db, schedule, now=now)
                    fired += 1
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return fired

    async def _fire(
        self, db: AsyncSession, schedule, *, now: datetime
    ) -> None:
        """Create a queued audit run, execute it, advance the schedule."""

        analyzer_params: dict[str, dict] = {}
        if schedule.mcp_server_name:
            for key in schedule.analyzers or []:
                params: dict = {"mcp_server_name": schedule.mcp_server_name}
                if schedule.focus_areas:
                    params["focus_areas"] = list(schedule.focus_areas)
                analyzer_params[key] = params

        run = await AuditRunRepo.create(
            db,
            schedule.org_id,
            analyzers=list(schedule.analyzers or []),
            created_by=schedule.created_by,
            status="queued",
        )
        await db.flush()

        try:
            await run_audit(
                db,
                run_id=run.id,
                org_id=schedule.org_id,
                pool=self._pool,
                config=self._config,
                analyzer_params=analyzer_params,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "auditor.scheduler: run %s for schedule %s failed: %s",
                run.id,
                schedule.id,
                exc,
            )

        await AuditScheduleRepo.mark_run(db, schedule, now=now)
