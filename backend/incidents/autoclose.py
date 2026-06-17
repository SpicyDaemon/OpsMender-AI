"""Auto-close scheduler — resolved incidents archive to ``closed`` after a
cooldown.

OpsMender's incident lifecycle is open → in_progress → resolved → closed.
``resolved`` means "active response is done"; ``closed`` is the final archival
state. Operators rarely close incidents by hand, so this scheduler does it for
them: a ``resolved`` incident that stays untouched for
``OPSMENDER_INCIDENT_AUTO_CLOSE_HOURS`` (default 72h / 3 days) transitions to
``closed``.

"Untouched" is measured by ``updated_at`` — any edit/activity that bumps the
incident (re-open, status change, severity edit, service change) resets the
clock, so only genuinely settled incidents auto-close. The transition reuses
``IncidentRepo.update_status``, which is idempotent and also stops any staged
notification escalation. Resolved incidents already have their AI sessions
stopped (the resolve path stops them) and can't auto-start new ones, so there's
nothing to cancel here.

Enabled by default (mirrors the retention scheduler) so a fresh deployment keeps
the incidents list tidy without operator action; disable with
``OPSMENDER_INCIDENT_AUTO_CLOSE_ENABLED=false``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import Organization
from backend.db.repos import IncidentRepo

logger = logging.getLogger(__name__)

_DEFAULT_AUTO_CLOSE_HOURS = 72
_CANDIDATE_BATCH = 500


def _env_truthy(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def auto_close_enabled_from_env() -> bool:
    return _env_truthy("OPSMENDER_INCIDENT_AUTO_CLOSE_ENABLED", default=True)


def auto_close_hours_from_env() -> int:
    """Cooldown in hours before a resolved incident auto-closes.

    Falls back to the default for missing/invalid/non-positive values so a
    typo can never collapse the window to "close everything immediately."
    """
    raw = os.environ.get("OPSMENDER_INCIDENT_AUTO_CLOSE_HOURS")
    if raw is None:
        return _DEFAULT_AUTO_CLOSE_HOURS
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid OPSMENDER_INCIDENT_AUTO_CLOSE_HOURS=%r; using default %d",
            raw,
            _DEFAULT_AUTO_CLOSE_HOURS,
        )
        return _DEFAULT_AUTO_CLOSE_HOURS
    if hours < 1:
        logger.warning(
            "OPSMENDER_INCIDENT_AUTO_CLOSE_HOURS=%d is below the 1h minimum; "
            "using default %d",
            hours,
            _DEFAULT_AUTO_CLOSE_HOURS,
        )
        return _DEFAULT_AUTO_CLOSE_HOURS
    return hours


async def close_stale_resolved_incidents(
    db: AsyncSession,
    org_id,
    *,
    older_than: datetime,
) -> int:
    """Close every ``resolved`` incident in *org_id* not touched since
    *older_than*. Returns the number closed. Caller commits."""
    candidates = await IncidentRepo.list_all(
        db,
        org_id,
        status="resolved",
        updated_to=older_than,
        limit=_CANDIDATE_BATCH,
    )
    closed = 0
    for incident in candidates:
        await IncidentRepo.update_status(db, org_id, incident.id, "closed")
        closed += 1
    return closed


class IncidentAutoCloseScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: int = 60 * 60,  # 1 hour
        auto_close_hours: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._auto_close_hours = (
            auto_close_hours
            if auto_close_hours is not None
            else auto_close_hours_from_env()
        )
        self._enabled = (
            enabled if enabled is not None else auto_close_enabled_from_env()
        )
        self._task: asyncio.Task | None = None
        self._last_run_at: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def auto_close_hours(self) -> int:
        return self._auto_close_hours

    @property
    def last_run_at(self) -> datetime | None:
        return self._last_run_at

    async def start(self) -> None:
        if not self._enabled:
            logger.info(
                "Incident auto-close scheduler is disabled "
                "(OPSMENDER_INCIDENT_AUTO_CLOSE_ENABLED=false)."
            )
            return
        if self._task is not None:
            return
        logger.info(
            "Incident auto-close scheduler enabled (cooldown=%dh, poll=%ds).",
            self._auto_close_hours,
            self._poll_interval_seconds,
        )
        self._task = asyncio.create_task(
            self._loop(), name="opsmender-incident-autoclose-scheduler"
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
                logger.exception("Incident auto-close pass failed")
            try:
                await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                break

    async def run_once(self) -> int:
        """One pass across every org. Returns the total incidents closed."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._auto_close_hours)
        total_closed = 0
        async with self._session_factory() as db:
            org_ids = (await db.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                closed = await close_stale_resolved_incidents(
                    db, org_id, older_than=cutoff
                )
                if closed:
                    await db.commit()
                    total_closed += closed
                    logger.info(
                        "Incident auto-close org=%s closed=%d (resolved >%dh)",
                        org_id,
                        closed,
                        self._auto_close_hours,
                    )
        self._last_run_at = datetime.now(timezone.utc)
        return total_closed
