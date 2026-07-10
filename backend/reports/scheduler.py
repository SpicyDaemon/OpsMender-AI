"""Leader-safe scheduled incident report delivery."""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.repos import ReportScheduleRepo
from backend.reports.email import build_email_channel, resolve_email_settings
from backend.reports.service import build_incident_report, render_report

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def advance_cadence(value: datetime, cadence: str, steps: int = 1) -> datetime:
    if cadence == "weekly":
        from datetime import timedelta

        return value + timedelta(days=7 * steps)
    months = (1 if cadence == "monthly" else 3) * steps
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


class ReportScheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval_seconds: int = 60,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(), name="opsmender-report-scheduler"
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
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled report pass failed")
            await asyncio.sleep(self._poll_interval_seconds)

    async def tick(self, *, now: datetime) -> int:
        fired = 0
        async with self._session_factory() as db:
            due = await ReportScheduleRepo.list_due(db, now=now)
            for schedule in due:
                error: str | None = None
                try:
                    settings = await resolve_email_settings(db, schedule.org_id)
                    if settings is None:
                        error = "SMTP not configured"
                    else:
                        end = _utc(schedule.next_run_at)
                        start = advance_cadence(end, schedule.cadence, -1)
                        report = await build_incident_report(
                            db,
                            schedule.org_id,
                            from_at=start,
                            to_at=end,
                            filters=schedule.filters,
                        )
                        content, _ = render_report(report, schedule.format)
                        channel = build_email_channel(settings)
                        failures: list[str] = []
                        for recipient in schedule.recipients:
                            attempt = await channel.send_with_attachment(
                                recipient=recipient,
                                subject=f"OpsMender incident report: {schedule.name}",
                                body=f"Attached: {schedule.cadence} incident report.",
                                attachment=content,
                                attachment_name=f"opsmender-report.{schedule.format}",
                                attachment_subtype=(
                                    "pdf" if schedule.format == "pdf" else "csv"
                                ),
                            )
                            if attempt.status != "sent":
                                failures.append(f"{recipient}: {attempt.error}")
                        error = "; ".join(failures) or None
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                    logger.exception("Scheduled report %s failed", schedule.id)
                schedule.last_run_at = now
                schedule.last_error = error
                next_run_at = _utc(schedule.next_run_at)
                while next_run_at <= now:
                    next_run_at = advance_cadence(next_run_at, schedule.cadence)
                schedule.next_run_at = next_run_at
                fired += 1
            await db.commit()
        return fired
