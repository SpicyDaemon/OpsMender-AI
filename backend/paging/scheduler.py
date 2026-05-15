"""Background scheduler for escalation chains (Sprint 34).

Wakes up every ``poll_interval_seconds`` (default 10s), opens a session, and
calls :func:`backend.paging.escalation.tick_all_due`. Idempotent — chain
state transitions are guarded by the ``status`` column, so restart-safe.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.paging.escalation import tick_all_due


logger = logging.getLogger(__name__)


class EscalationScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: int = 10,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(), name="opsmender-escalation-scheduler"
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
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.warning("escalation.scheduler tick failed: %s", exc)
            await asyncio.sleep(self._poll_interval_seconds)

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as db:
            try:
                advanced = await tick_all_due(db, at=now)
                if advanced:
                    await db.commit()
            except Exception:
                await db.rollback()
                raise
