"""Background scheduler for escalation chains (Sprint 34).

Wakes up every ``poll_interval_seconds`` (default 10s), opens a session, and
calls :func:`backend.paging.escalation.tick_all_due`. Idempotent - chain
state transitions are guarded by the ``status`` column, so restart-safe.

Each wake-up also starts the sweep that posts held Notification Channel cards
(M1-100), in its own task and never two at once, so a slow workflow never
delays an Escalation Chain step.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.paging.channel_factory import build_channel_factory
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
        self._held_cards: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._loop(), name="opsmender-escalation-scheduler"
        )

    async def stop(self) -> None:
        for task in (self._task, self._held_cards):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._held_cards = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.warning("escalation.scheduler tick failed: %s", exc)
            self._start_held_card_sweep()
            await asyncio.sleep(self._poll_interval_seconds)

    def _start_held_card_sweep(self) -> None:
        """Start one held-card sweep unless the last one is still running."""
        if self._held_cards is not None and not self._held_cards.done():
            return
        from backend.bots.notifier import post_held_cards

        self._held_cards = asyncio.create_task(
            post_held_cards(self._session_factory),
            name="opsmender-held-channel-cards",
        )

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        factory = build_channel_factory()
        async with self._session_factory() as db:
            try:
                # Personal notification steps go first, so a level that
                # waits for its people sees their latest step (M1-22).
                from backend.paging import notification_escalation as _ne

                staged = await _ne.tick_all_due(
                    db, at=now, sender=_ne.build_notification_sender(factory)
                )
                advanced = await tick_all_due(db, at=now, channel_factory=factory)
                # Requests to join as a responder expire after 30 minutes.
                from backend.paging import responders as _responders

                expired = await _responders.expire_due_requests(db, at=now)
                # An operator's Maintenance Window announces its start once.
                from backend.paging.window_notice import announce_started

                announced = await announce_started(db, at=now)
                if advanced or staged or expired or announced:
                    await db.commit()
            except Exception:
                await db.rollback()
                raise
