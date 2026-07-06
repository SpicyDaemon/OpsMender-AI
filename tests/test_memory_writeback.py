"""Sprint 45 Steps 4 + 5 — `remember` node + auto-compaction tests."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.graph import (
    DEFAULT_WORKFLOW_NODE_ORDER,
    validate_workflow_node_order,
)
from backend.agent.nodes import _build_remember, remember as remember_stub
from backend.db.models import (
    Base,
    Incident,
    Organization,
    Service,
    Session as SessionModel,
    Team,
)
from backend.db.repos import IncidentMemoryRepo
from backend.memory.writeback import (
    COMPACTION_THRESHOLD,
    MAX_COMPACTION_OPS,
    MemoryDraft,
    maybe_compact,
    remember_for_session,
    should_remember,
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


async def _seed_service(factory) -> Service:
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
        await db.commit()
        return service


class _ScriptedLLM:
    """Records every invocation and returns a scripted response queue."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._responses:
            return ""
        return self._responses.pop(0)

    def stream(self, prompt: str):  # pragma: no cover — not used in tests
        return iter([self.invoke(prompt)])


# ---------------------------------------------------------------------------
# MemoryDraft.from_json
# ---------------------------------------------------------------------------


class TestMemoryDraftFromJson:
    def test_parses_minimal_object(self):
        raw = json.dumps({"title": "Pod OOM", "tags": ["k8s"], "summary_md": "fix it"})
        draft = MemoryDraft.from_json(raw)
        assert draft is not None
        assert draft.title == "Pod OOM"
        assert draft.tags == ["k8s"]
        assert draft.summary_md == "fix it"

    def test_strips_code_fence(self):
        raw = "```json\n" + json.dumps({"title": "t", "summary_md": "s"}) + "\n```"
        draft = MemoryDraft.from_json(raw)
        assert draft is not None
        assert draft.title == "t"

    def test_rejects_missing_title(self):
        raw = json.dumps({"tags": [], "summary_md": "s"})
        assert MemoryDraft.from_json(raw) is None

    def test_rejects_garbage(self):
        assert MemoryDraft.from_json("not json") is None
        assert MemoryDraft.from_json("") is None
        assert MemoryDraft.from_json("[1,2,3]") is None  # array not object

    def test_normalises_tags(self):
        raw = json.dumps(
            {
                "title": "t",
                "tags": [" K8s ", "K8S", "", 42, "outage"],
                "summary_md": "s",
            }
        )
        draft = MemoryDraft.from_json(raw)
        assert draft is not None
        # Lower-cased + deduped + non-string filtered.
        assert draft.tags == ["k8s", "outage"]

    def test_canonicalises_severity_tags(self):
        raw = json.dumps(
            {
                "title": "t",
                "tags": [" high ", "severity-high", "critical", "payments"],
                "summary_md": "s",
            }
        )
        draft = MemoryDraft.from_json(raw)
        assert draft is not None
        assert draft.tags == ["severity-high", "severity-critical", "payments"]

    def test_caps_tag_count(self):
        tags = [f"tag{i}" for i in range(10)]
        raw = json.dumps({"title": "t", "tags": tags, "summary_md": "s"})
        draft = MemoryDraft.from_json(raw)
        assert draft is not None
        assert len(draft.tags) == 5


# ---------------------------------------------------------------------------
# should_remember
# ---------------------------------------------------------------------------


class TestShouldRemember:
    def test_ok_when_completed_with_summary(self):
        ok, _ = should_remember(
            {
                "status": "completed",
                "summary": "Cleaned up dangling connections in payments-service.",
            }
        )
        assert ok

    def test_skips_failed_workflow(self):
        ok, reason = should_remember({"status": "failed"})
        assert not ok
        assert "failed" in reason

    def test_skips_timed_out(self):
        ok, _ = should_remember({"status": "timed_out"})
        assert not ok

    def test_skips_on_tool_error(self):
        ok, reason = should_remember(
            {
                "status": "completed",
                "summary": "x" * 50,
                "tool_calls": [
                    {"tool_name": "kubectl", "error": "boom"},
                ],
            }
        )
        assert not ok
        assert "tool call" in reason

    def test_blocked_tool_call_does_not_skip(self):
        ok, _ = should_remember(
            {
                "status": "completed",
                "summary": "x" * 50,
                "tool_calls": [
                    {"tool_name": "kubectl", "permitted": False},
                ],
            }
        )
        assert ok

    def test_skips_when_summary_and_diagnosis_trivial(self):
        ok, reason = should_remember(
            {"status": "completed", "summary": "ok", "diagnosis": ""}
        )
        assert not ok
        assert "trivial" in reason


# ---------------------------------------------------------------------------
# remember_for_session
# ---------------------------------------------------------------------------


class TestRememberForSession:
    async def test_writes_memory_on_success(self, factory):
        service = await _seed_service(factory)
        llm = _ScriptedLLM(
            [
                json.dumps(
                    {
                        "title": "checkout 500s caused by payments tcp drops",
                        "tags": ["payments", "high"],
                        "summary_md": "Restart the payments-service pods.",
                    }
                )
            ]
        )

        new_id = await remember_for_session(
            factory,
            llm=llm,
            org_id=ORG_A,
            service_id=service.id,
            source_incident_id=None,
            state={
                "status": "completed",
                "summary": "x" * 50,
                "diagnosis": "y" * 50,
                "incident": {"title": "checkout 500s", "severity": "high"},
            },
        )
        assert new_id is not None
        assert len(llm.calls) == 1

        async with factory() as db:
            memory = await IncidentMemoryRepo.get_by_id(db, new_id, ORG_A)
            assert memory is not None
            assert memory.service_id == service.id
            assert memory.tags == ["payments", "severity-high"]
            assert "Restart" in memory.summary_md

    async def test_skipped_when_should_not_remember(self, factory):
        service = await _seed_service(factory)
        llm = _ScriptedLLM(["unused"])
        result = await remember_for_session(
            factory,
            llm=llm,
            org_id=ORG_A,
            service_id=service.id,
            source_incident_id=None,
            state={"status": "failed"},
        )
        assert result is None
        # LLM must not be called when the gate skips.
        assert llm.calls == []

    async def test_skipped_on_unparseable_llm_response(self, factory):
        service = await _seed_service(factory)
        llm = _ScriptedLLM(["this is not json at all"])
        result = await remember_for_session(
            factory,
            llm=llm,
            org_id=ORG_A,
            service_id=service.id,
            source_incident_id=None,
            state={
                "status": "completed",
                "summary": "x" * 50,
                "incident": {"title": "t"},
            },
        )
        assert result is None
        async with factory() as db:
            rows = await IncidentMemoryRepo.list_for_org(db, ORG_A)
        assert rows == []

    async def test_none_factory_or_llm_returns_none(self):
        assert (
            await remember_for_session(
                None,
                llm=_ScriptedLLM([]),
                org_id=ORG_A,
                service_id=None,
                source_incident_id=None,
                state={"status": "completed", "summary": "x" * 50},
            )
            is None
        )


# ---------------------------------------------------------------------------
# remember LangGraph node
# ---------------------------------------------------------------------------


class TestRememberNode:
    async def test_stub_is_noop(self):
        assert remember_stub({"status": "completed"}) == {}

    async def test_built_node_returns_memorized_id_on_success(self, factory):
        service = await _seed_service(factory)
        llm = _ScriptedLLM(
            [
                json.dumps(
                    {
                        "title": "checkout fix",
                        "tags": ["high"],
                        "summary_md": "...",
                    }
                )
            ]
        )
        node = _build_remember(
            llm,
            factory,
            org_id=ORG_A,
            service_id=service.id,
            source_incident_id=None,
        )
        out = await node(
            {
                "status": "completed",
                "summary": "y" * 50,
                "incident": {"title": "checkout 500s", "severity": "high"},
            }
        )
        assert "memorized_id" in out

    async def test_built_node_returns_empty_when_skipped(self, factory):
        service = await _seed_service(factory)
        llm = _ScriptedLLM([])
        node = _build_remember(
            llm,
            factory,
            org_id=ORG_A,
            service_id=service.id,
            source_incident_id=None,
        )
        assert await node({"status": "failed"}) == {}


# ---------------------------------------------------------------------------
# Auto-compaction (Step 5)
# ---------------------------------------------------------------------------


class TestAutoCompaction:
    async def test_noop_under_threshold(self, factory):
        service = await _seed_service(factory)
        async with factory() as db:
            await IncidentMemoryRepo.create(
                db, org_id=ORG_A, service_id=service.id,
                title="t1", summary_md="s",
            )
            await db.commit()
        report = await maybe_compact(
            factory, llm=None, org_id=ORG_A, service_id=service.id
        )
        assert report["exact_deleted"] == 0
        assert report["llm_deleted"] == 0
        assert report["total_after"] == 1

    async def test_exact_title_dedup_keeps_newest(self, factory):
        service = await _seed_service(factory)
        # Seed threshold+2 memories, with the last two sharing a title so
        # exact-title dedup will fire.
        async with factory() as db:
            for i in range(COMPACTION_THRESHOLD):
                await IncidentMemoryRepo.create(
                    db, org_id=ORG_A, service_id=service.id,
                    title=f"unique-{i}", summary_md="x",
                )
            await IncidentMemoryRepo.create(
                db, org_id=ORG_A, service_id=service.id,
                title="DUPE", summary_md="older",
            )
            await IncidentMemoryRepo.create(
                db, org_id=ORG_A, service_id=service.id,
                title="DUPE",  # same title, will trigger dedup
                summary_md="newer",
            )
            await db.commit()

        report = await maybe_compact(
            factory, llm=None, org_id=ORG_A, service_id=service.id
        )
        assert report["exact_deleted"] == 1
        # The newer one must survive — summary "newer".
        async with factory() as db:
            rows = await IncidentMemoryRepo.list_for_org(
                db, ORG_A, service_id=service.id
            )
            dupes = [r for r in rows if r.title == "DUPE"]
            assert len(dupes) == 1
            assert dupes[0].summary_md == "newer"

    async def test_global_compaction_does_not_touch_service_memories(
        self, factory
    ):
        service = await _seed_service(factory)
        async with factory() as db:
            await IncidentMemoryRepo.create(
                db, org_id=ORG_A, service_id=None,
                title="DUPE", summary_md="older global",
            )
            await IncidentMemoryRepo.create(
                db, org_id=ORG_A, service_id=None,
                title="DUPE", summary_md="newer global",
            )
            service_memory = await IncidentMemoryRepo.create(
                db, org_id=ORG_A, service_id=service.id,
                title="DUPE", summary_md="service-specific",
            )
            await db.commit()
            service_memory_id = service_memory.id

        report = await maybe_compact(
            factory, llm=None, org_id=ORG_A, service_id=None, threshold=1
        )
        assert report["exact_deleted"] == 1

        async with factory() as db:
            global_rows = await IncidentMemoryRepo.list_for_org(
                db, ORG_A, global_only=True
            )
            service_rows = await IncidentMemoryRepo.list_for_org(
                db, ORG_A, service_id=service.id
            )
        assert len(global_rows) == 1
        assert global_rows[0].summary_md == "newer global"
        assert [row.id for row in service_rows] == [service_memory_id]

    async def test_llm_compaction_applies_deletes_within_cap(self, factory):
        service = await _seed_service(factory)
        ids: list[uuid.UUID] = []
        async with factory() as db:
            for i in range(COMPACTION_THRESHOLD + 1):
                m = await IncidentMemoryRepo.create(
                    db, org_id=ORG_A, service_id=service.id,
                    title=f"unique-{i}", summary_md="s",
                )
                ids.append(m.id)
            await db.commit()

        # Ask the LLM to delete the first three.
        delete_ops = [
            {"action": "delete", "id": str(ids[0]), "reason": "dup"},
            {"action": "delete", "id": str(ids[1]), "reason": "dup"},
            {"action": "delete", "id": str(ids[2]), "reason": "dup"},
            # Junk ops that must be ignored:
            {"action": "delete", "id": "not-a-uuid"},
            {"action": "merge", "ids": [str(ids[3]), str(ids[4])]},
        ]
        llm = _ScriptedLLM([json.dumps(delete_ops)])

        report = await maybe_compact(
            factory, llm=llm, org_id=ORG_A, service_id=service.id
        )
        assert report["llm_deleted"] == 3
        assert llm.calls, "expected one LLM call"

        async with factory() as db:
            remaining = await IncidentMemoryRepo.count_for_service(
                db, ORG_A, service.id
            )
        assert remaining == COMPACTION_THRESHOLD + 1 - 3

    async def test_llm_op_count_capped(self, factory):
        service = await _seed_service(factory)
        ids: list[uuid.UUID] = []
        async with factory() as db:
            for i in range(COMPACTION_THRESHOLD + 10):
                m = await IncidentMemoryRepo.create(
                    db, org_id=ORG_A, service_id=service.id,
                    title=f"u-{i}", summary_md="s",
                )
                ids.append(m.id)
            await db.commit()

        # Try to delete MAX_COMPACTION_OPS + 2 — only MAX_COMPACTION_OPS apply.
        delete_ops = [
            {"action": "delete", "id": str(i)}
            for i in ids[: MAX_COMPACTION_OPS + 2]
        ]
        llm = _ScriptedLLM([json.dumps(delete_ops)])
        report = await maybe_compact(
            factory, llm=llm, org_id=ORG_A, service_id=service.id
        )
        assert report["llm_deleted"] == MAX_COMPACTION_OPS


# ---------------------------------------------------------------------------
# Workflow order
# ---------------------------------------------------------------------------


class TestWorkflowOrderWithRemember:
    def test_default_ends_with_remember(self):
        assert DEFAULT_WORKFLOW_NODE_ORDER[-1] == "remember"

    def test_validator_accepts_remember(self):
        order = validate_workflow_node_order(
            ["recall", "observe", "summarize", "remember"]
        )
        assert order[-1] == "remember"

    def test_validator_accepts_legacy_orders_without_remember(self):
        order = validate_workflow_node_order(
            ["observe", "diagnose", "summarize"]
        )
        assert "remember" not in order
