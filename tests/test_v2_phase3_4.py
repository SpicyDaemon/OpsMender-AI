"""v2 Phase 3 (orchestration overview data) + Phase 4 (lifecycle comments)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import (
    Base,
    Incident,
    IncidentComment,
    ModelConfig,
    Organization,
    Service,
    Session,
)
from backend.db.repos import IncidentCommentRepo, SessionRepo
from backend.services.incident_timeline import (
    LIFECYCLE_SOURCE,
    record_lifecycle_comment,
)

ORG_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG_ID, name="P34", slug="p34"))
        await db.commit()
    yield maker
    await engine.dispose()


async def _seed(db):
    model = ModelConfig(
        org_id=ORG_ID,
        name="cap-model",
        provider="ollama",
        model_id="cap-model",
        max_concurrent_sessions=2,
        is_default=True,
    )
    db.add(model)
    await db.flush()
    service = Service(
        org_id=ORG_ID,
        team_id=uuid.uuid4(),
        name="svc",
        slug="svc",
        priority="P1",
        preferred_model_config_ids=[str(model.id)],
    )
    db.add(service)
    await db.flush()
    incident = Incident(
        org_id=ORG_ID,
        title="AWS Critical",
        description="x",
        priority="P0",
        service_id=service.id,
    )
    db.add(incident)
    await db.flush()
    return model, service, incident


async def test_list_live_with_incident_splits_and_counts(factory):
    async with factory() as db:
        model, service, incident = await _seed(db)
        # one active session on the model
        db.add(
            Session(
                org_id=ORG_ID,
                incident_id=incident.id,
                tier=1,
                status="active",
                model_config_id=model.id,
                model_provider=model.provider,
                model_id=model.model_id,
            )
        )
        # one queued session (no model assigned yet)
        db.add(
            Session(
                org_id=ORG_ID,
                incident_id=incident.id,
                tier=2,
                status="queued",
                queued_at=_now(),
                queue_reason="All preferred models at capacity",
            )
        )
        # a terminal session that must NOT appear
        db.add(
            Session(org_id=ORG_ID, incident_id=incident.id, tier=2, status="completed")
        )
        await db.commit()

        rows = await SessionRepo.list_live_with_incident(db, ORG_ID)
        statuses = sorted(s.status for s, _ in rows)
        assert statuses == ["active", "queued"]
        # incident is joined through for title/priority
        for session, inc in rows:
            assert inc is not None and inc.title == "AWS Critical"

        occ = await SessionRepo.active_occupancy_by_model_config(db, ORG_ID)
        assert occ.get(model.id) == 1  # queued + terminal excluded


async def test_record_lifecycle_comment_creates_and_skips(factory):
    async with factory() as db:
        _, _, incident = await _seed(db)
        author = uuid.uuid4()
        await record_lifecycle_comment(
            db,
            ORG_ID,
            incident_id=incident.id,
            body="Resolved the incident.",
            author_user_id=author,
        )
        # no-op when there is no incident
        assert (
            await record_lifecycle_comment(
                db, ORG_ID, incident_id=None, body="ignored"
            )
            is None
        )
        await db.commit()

        comments = await IncidentCommentRepo.list_for_incident(db, ORG_ID, incident.id)
        assert len(comments) == 1
        assert comments[0].source == LIFECYCLE_SOURCE
        assert comments[0].body == "Resolved the incident."
        assert comments[0].author_user_id == author
