"""Sprint 45 Step 1 — IncidentMemoryRepo tests.

Covers per-org isolation, retrieval scoring, feedback counters, delete, and
recall logging.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.db.models import (
    Base,
    Incident,
    Organization,
    Service,
    Session as SessionModel,
    Team,
)
from backend.db.repos import (
    IncidentMemoryRecallLogRepo,
    IncidentMemoryRepo,
)


ORG_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
ORG_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed two orgs.
    async with factory() as session:
        session.add(Organization(id=ORG_A, name="A", slug="a"))
        session.add(Organization(id=ORG_B, name="B", slug="b"))
        await session.commit()

    async with factory() as session:
        yield session
    await engine.dispose()


async def _make_service(db: AsyncSession, org_id: uuid.UUID, name: str) -> Service:
    team = Team(
        id=uuid.uuid4(),
        org_id=org_id,
        name=f"team-{name}",
        slug=f"team-{name}-{uuid.uuid4().hex[:6]}",
    )
    db.add(team)
    await db.flush()
    service = Service(
        id=uuid.uuid4(),
        org_id=org_id,
        team_id=team.id,
        name=name,
        slug=f"{name}-{uuid.uuid4().hex[:6]}",
    )
    db.add(service)
    await db.flush()
    return service


class TestCreateAndGet:
    async def test_create_writes_org_scoped_row(self, db: AsyncSession):
        m = await IncidentMemoryRepo.create(
            db,
            org_id=ORG_A,
            title="OOMKilled on api-gateway",
            summary_md="Bumped requests/limits; root cause was a leak in jwt parsing.",
            tags=["k8s", "oom"],
        )
        await db.flush()

        assert m.org_id == ORG_A
        assert m.title.startswith("OOMKilled")
        assert m.tags == ["k8s", "oom"]
        assert m.helpful_count == 0
        assert m.unhelpful_count == 0

    async def test_get_respects_org_boundary(self, db: AsyncSession):
        m = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="t", summary_md="s"
        )
        await db.flush()

        same_org = await IncidentMemoryRepo.get_by_id(db, m.id, ORG_A)
        cross_org = await IncidentMemoryRepo.get_by_id(db, m.id, ORG_B)
        assert same_org is not None
        assert cross_org is None


class TestList:
    async def test_list_filters_by_service(self, db: AsyncSession):
        svc = await _make_service(db, ORG_A, "checkout")
        other_svc = await _make_service(db, ORG_A, "billing")

        m1 = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, service_id=svc.id, title="a", summary_md="x"
        )
        m2 = await IncidentMemoryRepo.create(
            db,
            org_id=ORG_A,
            service_id=other_svc.id,
            title="b",
            summary_md="x",
        )
        m3 = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, service_id=svc.id, title="c", summary_md="x"
        )
        await db.flush()

        listed = await IncidentMemoryRepo.list_for_org(
            db, ORG_A, service_id=svc.id
        )

        assert {m.id for m in listed} == {m1.id, m3.id}
        assert all(m.id != m2.id for m in listed)

    async def test_count_is_scoped_per_service_and_global(self, db: AsyncSession):
        svc = await _make_service(db, ORG_A, "checkout")
        for i in range(3):
            await IncidentMemoryRepo.create(
                db,
                org_id=ORG_A,
                service_id=svc.id,
                title=f"t{i}",
                summary_md="x",
            )
        await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="global", summary_md="x"
        )
        await db.flush()

        assert await IncidentMemoryRepo.count_for_service(db, ORG_A, svc.id) == 3
        assert await IncidentMemoryRepo.count_for_service(db, ORG_A, None) == 1


class TestFindRelevant:
    async def test_service_match_scores_higher_than_global(
        self, db: AsyncSession
    ):
        svc = await _make_service(db, ORG_A, "checkout")
        on_service = await IncidentMemoryRepo.create(
            db,
            org_id=ORG_A,
            service_id=svc.id,
            title="checkout latency",
            summary_md="add a connection pool",
        )
        global_memory = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="generic latency", summary_md="check the network"
        )
        await db.flush()

        results = await IncidentMemoryRepo.find_relevant(
            db,
            org_id=ORG_A,
            service_id=svc.id,
            query="latency",
            tags=None,
            limit=5,
        )
        # Service-matched memory must be ranked first.
        assert results[0][0].id == on_service.id
        assert results[0][1] > results[1][1]
        # Both memories surface (service match for the first, global lift for the second).
        assert {m.id for m, _ in results} == {on_service.id, global_memory.id}

    async def test_tag_overlap_adds_score(self, db: AsyncSession):
        svc = await _make_service(db, ORG_A, "checkout")
        a = await IncidentMemoryRepo.create(
            db,
            org_id=ORG_A,
            service_id=svc.id,
            title="A",
            summary_md="aaa",
            tags=["oom", "memory"],
        )
        b = await IncidentMemoryRepo.create(
            db,
            org_id=ORG_A,
            service_id=svc.id,
            title="B",
            summary_md="bbb",
            tags=["network"],
        )
        await db.flush()

        results = await IncidentMemoryRepo.find_relevant(
            db,
            org_id=ORG_A,
            service_id=svc.id,
            query=None,
            tags=["oom", "memory"],
            limit=5,
        )
        ranked = {m.id: score for m, score in results}
        assert ranked[a.id] > ranked[b.id]

    async def test_helpful_ratio_breaks_ties(self, db: AsyncSession):
        svc = await _make_service(db, ORG_A, "checkout")
        liked = await IncidentMemoryRepo.create(
            db,
            org_id=ORG_A,
            service_id=svc.id,
            title="liked",
            summary_md="payload",
            tags=["t"],
        )
        disliked = await IncidentMemoryRepo.create(
            db,
            org_id=ORG_A,
            service_id=svc.id,
            title="disliked",
            summary_md="payload",
            tags=["t"],
        )
        await db.flush()

        # Tie up helpful counters
        for _ in range(5):
            await IncidentMemoryRepo.record_feedback(
                db, memory_id=liked.id, org_id=ORG_A, helpful=True
            )
        for _ in range(5):
            await IncidentMemoryRepo.record_feedback(
                db, memory_id=disliked.id, org_id=ORG_A, helpful=False
            )
        await db.flush()

        results = await IncidentMemoryRepo.find_relevant(
            db,
            org_id=ORG_A,
            service_id=svc.id,
            query=None,
            tags=["t"],
            limit=5,
        )
        assert results[0][0].id == liked.id

    async def test_cross_org_memories_never_surface(self, db: AsyncSession):
        svc_a = await _make_service(db, ORG_A, "checkout")
        svc_b = await _make_service(db, ORG_B, "checkout")
        await IncidentMemoryRepo.create(
            db,
            org_id=ORG_B,
            service_id=svc_b.id,
            title="from org B",
            summary_md="should not appear in A",
        )
        await db.flush()

        results = await IncidentMemoryRepo.find_relevant(
            db,
            org_id=ORG_A,
            service_id=svc_a.id,
            query="appear",
            tags=None,
        )
        assert results == []


class TestFeedback:
    async def test_thumbs_up_increments(self, db: AsyncSession):
        m = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="t", summary_md="x"
        )
        await db.flush()

        await IncidentMemoryRepo.record_feedback(
            db, memory_id=m.id, org_id=ORG_A, helpful=True
        )
        await IncidentMemoryRepo.record_feedback(
            db, memory_id=m.id, org_id=ORG_A, helpful=False
        )
        await db.flush()

        refreshed = await IncidentMemoryRepo.get_by_id(db, m.id, ORG_A)
        assert refreshed is not None
        assert refreshed.helpful_count == 1
        assert refreshed.unhelpful_count == 1

    async def test_feedback_respects_org_boundary(self, db: AsyncSession):
        m = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="t", summary_md="x"
        )
        await db.flush()

        result = await IncidentMemoryRepo.record_feedback(
            db, memory_id=m.id, org_id=ORG_B, helpful=True
        )
        assert result is None


class TestUpdateAndDelete:
    async def test_update_fields(self, db: AsyncSession):
        m = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="old", summary_md="old"
        )
        await db.flush()

        await IncidentMemoryRepo.update(
            db,
            memory_id=m.id,
            org_id=ORG_A,
            title="new",
            summary_md="new",
            tags=["x"],
        )
        await db.flush()

        refreshed = await IncidentMemoryRepo.get_by_id(db, m.id, ORG_A)
        assert refreshed.title == "new"
        assert refreshed.summary_md == "new"
        assert refreshed.tags == ["x"]

    async def test_delete_removes_row(self, db: AsyncSession):
        m = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="t", summary_md="x"
        )
        await db.flush()

        ok = await IncidentMemoryRepo.delete(
            db, memory_id=m.id, org_id=ORG_A
        )
        await db.flush()
        assert ok is True
        assert await IncidentMemoryRepo.get_by_id(db, m.id, ORG_A) is None

    async def test_delete_respects_org_boundary(self, db: AsyncSession):
        m = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="t", summary_md="x"
        )
        await db.flush()
        ok = await IncidentMemoryRepo.delete(
            db, memory_id=m.id, org_id=ORG_B
        )
        assert ok is False
        assert await IncidentMemoryRepo.get_by_id(db, m.id, ORG_A) is not None

    async def test_delete_many_is_org_scoped(self, db: AsyncSession):
        a1 = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="a1", summary_md="x"
        )
        a2 = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="a2", summary_md="x"
        )
        b1 = await IncidentMemoryRepo.create(
            db, org_id=ORG_B, title="b1", summary_md="x"
        )
        await db.flush()

        deleted = await IncidentMemoryRepo.delete_many(
            db, memory_ids=[a1.id, a2.id, b1.id], org_id=ORG_A
        )
        assert deleted == 2
        assert await IncidentMemoryRepo.get_by_id(db, b1.id, ORG_B) is not None


class TestRecallLog:
    async def test_record_and_list(self, db: AsyncSession):
        # Need an incident + session to satisfy FKs.
        incident = Incident(
            id=uuid.uuid4(),
            org_id=ORG_A,
            title="t",
            description="d",
            status="resolved",
        )
        db.add(incident)
        await db.flush()
        session = SessionModel(
            id=uuid.uuid4(),
            org_id=ORG_A,
            incident_id=incident.id,
            tier=2,
            status="resolved",
        )
        db.add(session)
        await db.flush()

        m = await IncidentMemoryRepo.create(
            db, org_id=ORG_A, title="t", summary_md="x"
        )
        await db.flush()

        await IncidentMemoryRecallLogRepo.record(
            db, memory_id=m.id, session_id=session.id, score=2.5
        )
        await IncidentMemoryRecallLogRepo.record(
            db, memory_id=m.id, session_id=session.id, score=1.0
        )
        await db.flush()

        rows = await IncidentMemoryRecallLogRepo.list_for_session(
            db, session.id
        )
        assert len(rows) == 2
        scores = sorted(float(r.score) for r in rows)
        assert scores == [1.0, 2.5]
