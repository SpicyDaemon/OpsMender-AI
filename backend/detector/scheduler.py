"""Background scheduler for MCP-driven detector rules."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config_loader import AppConfig
from backend.db.repos import DetectorRuleRepo
from backend.detector.runner import DetectorBudgetGuard, run_detector_rule
from backend.mcp.pool import MCPServerPool

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DetectorScheduler:
    """Poll active detector rules and run any that are due."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        pool: MCPServerPool,
        config: AppConfig,
        budget_guard: DetectorBudgetGuard | None = None,
        poll_interval_seconds: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._pool = pool
        self._config = config
        self._budget_guard = budget_guard
        self._poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None
        self._running_rules: set[uuid.UUID] = set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="aim-detector-scheduler")

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
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.warning("detector.scheduler tick failed: %s", exc)
            await asyncio.sleep(self._poll_interval_seconds)

    async def _tick(self) -> None:
        async with self._session_factory() as db:
            rules = await DetectorRuleRepo.list_all_global(db, active_only=True)
            now = datetime.now(timezone.utc)
            for rule in rules:
                if rule.id in self._running_rules:
                    continue
                if rule.last_ran_at is not None:
                    due_at = _as_utc(rule.last_ran_at) + timedelta(
                        seconds=rule.interval_seconds
                    )
                    if due_at > now:
                        continue
                self._running_rules.add(rule.id)
                asyncio.create_task(self._run_rule(rule.id))

    async def _run_rule(self, rule_id: uuid.UUID) -> None:
        try:
            async with self._session_factory() as db:
                rule = await DetectorRuleRepo.get_by_id_global(db, rule_id)
                if rule is None or not rule.is_active:
                    await db.rollback()
                    return
                await run_detector_rule(
                    db,
                    rule=rule,
                    pool=self._pool,
                    config=self._config,
                    budget_guard=self._budget_guard,
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("detector.scheduler rule %s failed: %s", rule_id, exc)
        finally:
            self._running_rules.discard(rule_id)
