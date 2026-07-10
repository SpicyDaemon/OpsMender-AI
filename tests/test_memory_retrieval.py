"""Sprint 45 Steps 2 + 3 — memory retrieval + `recall` LangGraph node tests.

Covers:
- `derive_query` / `derive_tags` helpers
- `format_memories_as_markdown` block rendering
- `recall_for_session` happy path (records recall log + stamps last_used_at)
- `recall_for_session` failure path (DB error swallowed, empty result)
- `_build_recall` node (state shape + missing session_id handling)
- DEFAULT_WORKFLOW_NODE_ORDER now starts with `recall`
- `validate_workflow_node_order` allows recall
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.graph import (
    DEFAULT_WORKFLOW_NODE_ORDER,
    validate_workflow_node_order,
)
from backend.agent.nodes import _build_recall, recall as recall_stub
from backend.db.models import (
    Base,
    Incident,
    IncidentMemory,
    Organization,
    Service,
    Session as SessionModel,
    Team,
)
from backend.db.repos import (
    IncidentMemoryRecallLogRepo,
    IncidentMemoryRepo,
)
from backend.memory.retrieval import (
    derive_query,
    derive_tags,
    format_memories_as_markdown,
    recall_for_session,
)


ORG_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as session:
        session.add(Organization(id=ORG_A, name="A", slug="a"))
        await session.commit()
    yield f
    await engine.dispose()


async def _seed_service_and_session(
    factory,
) -> tuple[Service, SessionModel, Incident]:
    async with factory() as db:
        team = Team(
            id=uuid.uuid4(),
            org_id=ORG_A,
            name="team",
            slug=f"team-{uuid.uuid4().hex[:6]}",
        )
        db.add(team)
        await db.flush()
        service = Service(
            id=uuid.uuid4(),
            org_id=ORG_A,
            team_id=team.id,
            name="api",
            slug=f"api-{uuid.uuid4().hex[:6]}",
        )
        db.add(service)
        incident = Incident(
            id=uuid.uuid4(),
            org_id=ORG_A,
            title="500s on checkout",
            description="500 errors spiking on the checkout endpoint",
            status="open",
            severity="high",
            service_id=service.id,
        )
        db.add(incident)
        await db.flush()
        session = SessionModel(
            id=uuid.uuid4(),
            org_id=ORG_A,
            incident_id=incident.id,
            tier=2,
            status="active",
        )
        db.add(session)
        await db.commit()
        return service, session, incident


class TestDeriveHelpers:
    def test_derive_query_strips_stop_words_and_short_tokens(self):
        assert (
            derive_query({"title": "The pod is OOMKilled in production"})
            == "pod oomkilled production"
        )

    def test_derive_query_returns_none_for_empty_title(self):
        assert derive_query({"title": ""}) is None
        assert derive_query({}) is None
        assert derive_query(None) is None

    def test_derive_query_handles_punctuation(self):
        assert (
            derive_query({"title": "kubectl: pod[0] CrashLoopBackOff!"})
            == "kubectl pod crashloopbackoff"
        )

    def test_derive_tags_lifts_severity(self):
        assert derive_tags({"severity": "Critical"}) == ["severity-critical"]
        assert derive_tags({"severity": None}) == []
        assert derive_tags({}) == []


class TestFormatMemoriesAsMarkdown:
    def test_empty_returns_empty_string(self):
        assert format_memories_as_markdown([]) == ""

    def test_includes_title_tags_and_summary(self):
        mem = IncidentMemory(
            id=uuid.uuid4(),
            org_id=ORG_A,
            title="Pod OOMKilled",
            summary_md="Increase requests + limits; root cause leak.",
            tags=["k8s", "oom"],
        )
        block = format_memories_as_markdown([(mem, 3.0)])
        assert "### Past lessons from similar incidents" in block
        assert "#### Pod OOMKilled" in block
        assert "_tags: k8s, oom_" in block
        assert "Increase requests" in block

    def test_long_summaries_get_truncated(self):
        mem = IncidentMemory(
            id=uuid.uuid4(),
            org_id=ORG_A,
            title="t",
            summary_md="x" * 2000,
            tags=[],
        )
        block = format_memories_as_markdown([(mem, 1.0)])
        # 1200 chars + ellipsis.
        assert "x" * 1200 in block
        assert "…" in block


class TestRecallForSession:
    async def test_happy_path_records_log_and_stamps_used(self, factory):
        service, session, incident = await _seed_service_and_session(factory)
        async with factory() as db:
            mem = await IncidentMemoryRepo.create(
                db,
                org_id=ORG_A,
                service_id=service.id,
                title="checkout 500s",
                summary_md="check the upstream payment service",
                tags=["severity-high"],
            )
            await db.commit()

        result = await recall_for_session(
            factory,
            org_id=ORG_A,
            session_id=session.id,
            service_id=service.id,
            incident={
                "title": incident.title,
                "description": incident.description,
                "severity": incident.severity,
            },
            limit=5,
        )

        assert not result.is_empty
        assert len(result.memories) == 1
        assert result.memory_ids == [str(mem.id)]
        assert "checkout 500s" in result.context_block

        # Side effects: log row + last_used_at touched.
        async with factory() as db:
            logs = await IncidentMemoryRecallLogRepo.list_for_session(db, session.id)
            assert len(logs) == 1
            assert logs[0].memory_id == mem.id
            refreshed = await IncidentMemoryRepo.get_by_id(db, mem.id, ORG_A)
            assert refreshed is not None
            assert refreshed.last_used_at is not None

    async def test_empty_when_no_memories(self, factory):
        service, session, incident = await _seed_service_and_session(factory)
        result = await recall_for_session(
            factory,
            org_id=ORG_A,
            session_id=session.id,
            service_id=service.id,
            incident={"title": incident.title, "severity": incident.severity},
        )
        assert result.is_empty
        assert result.context_block == ""
        assert result.memory_ids == []

    async def test_none_factory_returns_empty(self):
        result = await recall_for_session(
            None,
            org_id=ORG_A,
            session_id=uuid.uuid4(),
            service_id=None,
            incident={"title": "x"},
        )
        assert result.is_empty

    async def test_db_failure_returns_empty_and_does_not_raise(self, factory):
        # Provide a factory whose session raises on open. The retrieval must
        # swallow the exception and degrade to an empty result.
        class _BrokenFactory:
            def __call__(self):
                raise RuntimeError("db is down")

        result = await recall_for_session(
            _BrokenFactory(),
            org_id=ORG_A,
            session_id=uuid.uuid4(),
            service_id=None,
            incident={"title": "anything"},
        )
        assert result.is_empty


class TestRecallNode:
    async def test_stub_recall_is_noop(self):
        assert recall_stub({"session_id": str(uuid.uuid4())}) == {}

    async def test_built_recall_returns_context_when_memory_exists(self, factory):
        service, session, incident = await _seed_service_and_session(factory)
        async with factory() as db:
            await IncidentMemoryRepo.create(
                db,
                org_id=ORG_A,
                service_id=service.id,
                title="ticket about checkout",
                summary_md="payments-service occasionally drops connections",
                tags=["severity-high"],
            )
            await db.commit()

        node = _build_recall(factory, org_id=ORG_A, service_id=service.id)
        out = await node(
            {
                "session_id": str(session.id),
                "incident": {
                    "title": incident.title,
                    "description": incident.description,
                    "severity": incident.severity,
                },
            }
        )
        assert "memory_context" in out
        assert "recalled_memory_ids" in out
        assert "Past lessons" in out["memory_context"]
        assert len(out["recalled_memory_ids"]) == 1

    async def test_built_recall_returns_empty_when_no_session_id(self, factory):
        node = _build_recall(factory, org_id=ORG_A, service_id=None)
        out = await node({})
        assert out == {}

    async def test_built_recall_returns_empty_when_session_id_invalid(self, factory):
        node = _build_recall(factory, org_id=ORG_A, service_id=None)
        out = await node({"session_id": "not-a-uuid"})
        assert out == {}

    async def test_built_recall_returns_empty_when_no_memories(self, factory):
        _, session, incident = await _seed_service_and_session(factory)
        node = _build_recall(factory, org_id=ORG_A, service_id=None)
        out = await node(
            {
                "session_id": str(session.id),
                "incident": {
                    "title": incident.title,
                    "severity": incident.severity,
                },
            }
        )
        assert out == {}


class TestWorkflowOrder:
    def test_default_order_starts_with_recall(self):
        assert DEFAULT_WORKFLOW_NODE_ORDER[0] == "recall"

    def test_validate_allows_recall(self):
        order = validate_workflow_node_order(["recall", "observe", "summarize"])
        assert order == ["recall", "observe", "summarize"]

    def test_validate_allows_orders_without_recall(self):
        # Existing workflow profiles saved before Sprint 45 must keep
        # working — recall is optional, not required.
        order = validate_workflow_node_order(["observe", "summarize"])
        assert order == ["observe", "summarize"]
