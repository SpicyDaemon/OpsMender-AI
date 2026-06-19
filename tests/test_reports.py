from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Organization
from backend.db.repos import ReportScheduleRepo
from backend.reports.scheduler import ReportScheduler, advance_cadence


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=uuid.uuid4(), name="Reports", slug="reports"))
        await db.commit()
    yield maker
    await engine.dispose()


def test_advance_cadence_handles_month_end():
    value = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert advance_cadence(value, "monthly") == datetime(
        2026, 2, 28, tzinfo=timezone.utc
    )
    assert advance_cadence(value, "quarterly").month == 4


async def test_scheduler_fires_boundary_and_emails(factory, monkeypatch):
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    async with factory() as db:
        org_id = (await db.execute(select(Organization.id))).scalar_one()
        schedule = await ReportScheduleRepo.create(
            db,
            org_id,
            name="Weekly",
            cadence="weekly",
            recipients=["ops@example.com"],
            filters={},
            format="csv",
            next_run_at=now,
        )
        await db.commit()
        schedule_id = schedule.id

    sent = []

    class FakeChannel:
        async def send_with_attachment(self, **kwargs):
            sent.append(kwargs)
            return SimpleNamespace(status="sent", error=None)

    async def fake_settings(db, org_id):
        return SimpleNamespace()

    async def fake_report(*args, **kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("backend.reports.scheduler.resolve_email_settings", fake_settings)
    monkeypatch.setattr("backend.reports.scheduler.build_email_channel", lambda settings: FakeChannel())
    monkeypatch.setattr("backend.reports.scheduler.build_incident_report", fake_report)
    monkeypatch.setattr("backend.reports.scheduler.render_report", lambda report, format: (b"csv", "text/csv"))

    assert await ReportScheduler(factory).tick(now=now) == 1
    assert sent[0]["recipient"] == "ops@example.com"
    async with factory() as db:
        row = await ReportScheduleRepo.get_by_id(db, org_id, schedule_id)
        assert row.last_run_at.replace(tzinfo=timezone.utc) == now
        assert row.next_run_at.replace(tzinfo=timezone.utc) == now + timedelta(days=7)
        assert row.last_error is None


async def test_scheduler_records_render_failure_and_advances(factory, monkeypatch):
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    async with factory() as db:
        org_id = (await db.execute(select(Organization.id))).scalar_one()
        schedule = await ReportScheduleRepo.create(
            db,
            org_id,
            name="Broken PDF",
            cadence="monthly",
            recipients=["ops@example.com"],
            filters={},
            format="pdf",
            next_run_at=now,
        )
        await db.commit()
        schedule_id = schedule.id

    async def fake_settings(db, org_id):
        return SimpleNamespace()

    async def fake_report(*args, **kwargs):
        return SimpleNamespace()

    monkeypatch.setattr("backend.reports.scheduler.resolve_email_settings", fake_settings)
    monkeypatch.setattr("backend.reports.scheduler.build_incident_report", fake_report)
    monkeypatch.setattr(
        "backend.reports.scheduler.render_report",
        lambda report, format: (_ for _ in ()).throw(RuntimeError("render failed")),
    )

    assert await ReportScheduler(factory).tick(now=now) == 1
    async with factory() as db:
        row = await ReportScheduleRepo.get_by_id(db, org_id, schedule_id)
        assert row.last_error == "render failed"
        assert row.next_run_at.replace(tzinfo=timezone.utc) > now
