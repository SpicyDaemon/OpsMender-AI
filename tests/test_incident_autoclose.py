"""Incident auto-close scheduler tests.

Covers:
- `close_stale_resolved_incidents`: closes only resolved incidents older than
  the cutoff; leaves open/in_progress/recently-resolved/closed untouched.
- `IncidentAutoCloseScheduler.run_once`: walks every org and totals closures.
- env helpers: enabled default + invalid/non-positive hours fall back to default.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Incident, Organization
from backend.db.repos import IncidentRepo
from backend.incidents.autoclose import (
    IncidentAutoCloseScheduler,
    auto_close_hours_from_env,
    close_stale_resolved_incidents,
)

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as db:
        db.add(Organization(id=ORG_A, name="A", slug="a"))
        db.add(Organization(id=ORG_B, name="B", slug="b"))
        await db.commit()
    yield f
    await engine.dispose()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _seed(factory, org_id, *, status: str, age_hours: float) -> uuid.UUID:
    iid = uuid.uuid4()
    ts = _now() - timedelta(hours=age_hours)
    async with factory() as db:
        db.add(
            Incident(
                id=iid,
                org_id=org_id,
                title="t",
                description="d",
                status=status,
                created_at=ts,
                updated_at=ts,
            )
        )
        await db.commit()
    return iid


async def _status(factory, org_id, iid) -> str:
    async with factory() as db:
        inc = await IncidentRepo.get_by_id(db, org_id, iid)
        return inc.status


async def test_closes_only_stale_resolved(factory):
    cutoff = _now() - timedelta(hours=72)
    stale = await _seed(factory, ORG_A, status="resolved", age_hours=100)
    recent = await _seed(factory, ORG_A, status="resolved", age_hours=1)
    open_old = await _seed(factory, ORG_A, status="open", age_hours=100)
    in_prog_old = await _seed(factory, ORG_A, status="in_progress", age_hours=100)

    async with factory() as db:
        closed = await close_stale_resolved_incidents(db, ORG_A, older_than=cutoff)
        await db.commit()

    assert closed == 1
    assert await _status(factory, ORG_A, stale) == "closed"
    assert await _status(factory, ORG_A, recent) == "resolved"
    assert await _status(factory, ORG_A, open_old) == "open"
    assert await _status(factory, ORG_A, in_prog_old) == "in_progress"


async def test_run_once_walks_all_orgs(factory):
    a = await _seed(factory, ORG_A, status="resolved", age_hours=100)
    b = await _seed(factory, ORG_B, status="resolved", age_hours=100)
    # Recent resolved in B must survive.
    b_recent = await _seed(factory, ORG_B, status="resolved", age_hours=2)

    scheduler = IncidentAutoCloseScheduler(
        factory, auto_close_hours=72, enabled=True
    )
    total = await scheduler.run_once()

    assert total == 2
    assert await _status(factory, ORG_A, a) == "closed"
    assert await _status(factory, ORG_B, b) == "closed"
    assert await _status(factory, ORG_B, b_recent) == "resolved"
    assert scheduler.last_run_at is not None


async def test_disabled_scheduler_does_not_start(factory):
    scheduler = IncidentAutoCloseScheduler(factory, enabled=False)
    await scheduler.start()
    assert scheduler._task is None  # noqa: SLF001 — assert no loop spun up
    await scheduler.stop()  # safe no-op


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 72),
        ("0", 72),
        ("-5", 72),
        ("not-a-number", 72),
        ("24", 24),
        ("168", 168),
    ],
)
def test_auto_close_hours_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("OPSMENDER_INCIDENT_AUTO_CLOSE_HOURS", raising=False)
    else:
        monkeypatch.setenv("OPSMENDER_INCIDENT_AUTO_CLOSE_HOURS", raw)
    assert auto_close_hours_from_env() == expected
