"""Leader-safe scheduled incident report delivery."""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.repos import ReportScheduleRepo
from backend.reports.email import build_email_channel, resolve_email_settings
from backend.reports.service import build_incident_report, render_report

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def local_anchor(value: datetime, time_zone: str) -> tuple[int, str]:
    """The day of the month and local "HH:MM" of ``value`` in ``time_zone``:
    what a schedule keeps from its first run."""
    local = _utc(value).astimezone(ZoneInfo(time_zone or "UTC"))
    return local.day, f"{local.hour:02d}:{local.minute:02d}"


def advance_cadence(
    value: datetime,
    cadence: str,
    steps: int = 1,
    *,
    time_zone: str = "UTC",
    run_day: int | None = None,
    run_time: str | None = None,
) -> datetime:
    """The run ``steps`` cadences after ``value`` (before it when negative).

    Runs are local: weekly keeps the weekday and local time across clock
    changes, and monthly and quarterly keep the configured day, using the
    last day of shorter months and coming back to it after (R-18).
    """
    tz = ZoneInfo(time_zone or "UTC")
    local = _utc(value).astimezone(tz)
    day_of_month, clock = local_anchor(value, time_zone)
    hour, minute = (int(part) for part in (run_time or clock).split(":"))
    if cadence == "weekly":
        day = local.date() + timedelta(days=7 * steps)
    else:
        months = (1 if cadence == "monthly" else 3) * steps
        month_index = local.year * 12 + local.month - 1 + months
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        wanted = run_day or day_of_month
        day = date(year, month, min(wanted, calendar.monthrange(year, month)[1]))
    run = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    return run.astimezone(timezone.utc)


def _advance(schedule, value: datetime, steps: int = 1) -> datetime:
    return advance_cadence(
        value,
        schedule.cadence,
        steps,
        time_zone=schedule.time_zone or "UTC",
        run_day=schedule.run_day,
        run_time=schedule.run_time,
    )


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
                        start = _advance(schedule, end, -1)
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
                if schedule.run_day is None or schedule.run_time is None:
                    # A schedule from before R-18 keeps its next run's day and time.
                    schedule.run_day, schedule.run_time = local_anchor(
                        next_run_at, schedule.time_zone or "UTC"
                    )
                while next_run_at <= now:
                    next_run_at = _advance(schedule, next_run_at)
                schedule.next_run_at = next_run_at
                fired += 1
            await db.commit()
        return fired
