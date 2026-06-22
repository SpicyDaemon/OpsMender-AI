"""v2 Phase 7 — resumable session progress + RCA draft from the trail."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Incident, Organization, Session
from backend.db.repos import SessionRepo
from backend.services.postmortem_draft import draft_postmortem

ORG_ID = uuid.UUID("70000000-0000-0000-0000-000000000001")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG_ID, name="P7", slug="p7"))
        await db.commit()
    yield maker
    await engine.dispose()


async def test_set_status_persists_progress(factory):
    async with factory() as db:
        incident = Incident(
            org_id=ORG_ID, title="Cache outage", description="x", priority="P1"
        )
        db.add(incident)
        await db.flush()
        session = Session(
            org_id=ORG_ID, incident_id=incident.id, tier=2, status="active"
        )
        db.add(session)
        await db.commit()

        progress = {"diagnosis": "stale cache", "plan": [{"description": "flush"}]}
        await SessionRepo.set_status(
            db, ORG_ID, session.id, status="completed", summary="done", progress=progress
        )
        await db.commit()

        reloaded = await SessionRepo.get_by_id(db, ORG_ID, session.id)
        assert reloaded.status == "completed"
        assert reloaded.progress == progress


def test_draft_postmortem_assembles_sections_from_trail():
    incident = SimpleNamespace(
        title="DB down",
        priority="P0",
        status="resolved",
        created_at=datetime(2026, 6, 22, 1, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 6, 22, 1, 30, tzinfo=timezone.utc),
        updated_at=None,
    )
    session = SimpleNamespace(
        id=uuid.uuid4(),
        started_at=datetime(2026, 6, 22, 1, 5, tzinfo=timezone.utc),
        ended_at=datetime(2026, 6, 22, 1, 20, tzinfo=timezone.utc),
        status="completed",
        summary="Restarted the pod.",
        progress={
            "diagnosis": "OOMKilled",
            "plan": [{"description": "restart pod", "tool": "kubectl_rollout_restart"}],
            "observations": "memory at 99%",
        },
    )
    md = draft_postmortem(incident, [session])
    for section in (
        "## Summary",
        "## Impact",
        "## Timeline",
        "## Root cause",
        "## Resolution",
        "## Lessons learned",
        "## Memory candidates",
    ):
        assert section in md
    assert "OOMKilled" in md  # diagnosis -> root cause
    assert "restart pod" in md  # plan -> resolution
    assert "Restarted the pod." in md  # summary
    assert "memory at 99%" in md  # observations -> impact


def test_draft_postmortem_handles_empty_trail():
    incident = SimpleNamespace(
        title="No sessions",
        priority="P3",
        status="open",
        created_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
        resolved_at=None,
        updated_at=None,
    )
    md = draft_postmortem(incident, [])
    assert "## Summary" in md and "ongoing" in md
