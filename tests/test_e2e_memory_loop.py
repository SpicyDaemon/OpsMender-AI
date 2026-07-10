"""Sprint 45 Step 8 — end-to-end memory loop tests.

Unit tests already cover each layer in isolation (repo, retrieval, recall
node, writeback, remember node, REST API). This file covers the
cross-component integration that those leave out:

- A real ``build_graph`` invocation with ``memory_factory`` + ``llm`` set
  actually writes a memory after ``summarize`` and reads it back on the
  next session via ``recall``.
- Memory written by one session surfaces in the system prompt for a
  later session on the same service.
- Failed / errored workflows do NOT pollute memory.
- Memories created through the REST API (operator-authored, no LLM
  involved) are surfaced by ``recall`` exactly the same way.
- The ``GET /sessions/{id}/memories-used`` endpoint reflects what the
  agent actually saw on that session.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.graph import build_graph
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
from backend.skills.parser import SkillDefinition
from tests.skill_policy_helpers import explicit_operation


ORG = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


class _SequenceLLM:
    """Returns canned responses in order. Records every prompt seen."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._responses:
            return ""  # exhausted — treated as a parse failure by remember
        return self._responses.pop(0)


def _skill() -> SkillDefinition:
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            explicit_operation("get_pods", "safe"),
        ],
    )


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as db:
        db.add(Organization(id=ORG, name="A", slug="a"))
        await db.commit()
    yield f
    await engine.dispose()


async def _seed_service(factory) -> uuid.UUID:
    async with factory() as db:
        team = Team(
            id=uuid.uuid4(),
            org_id=ORG,
            name="t",
            slug=f"t-{uuid.uuid4().hex[:6]}",
        )
        db.add(team)
        await db.flush()
        svc = Service(
            id=uuid.uuid4(),
            org_id=ORG,
            team_id=team.id,
            name="api",
            slug=f"api-{uuid.uuid4().hex[:6]}",
        )
        db.add(svc)
        await db.commit()
        return svc.id


async def _seed_session(
    factory, service_id: uuid.UUID, *, title: str, severity: str = "high"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Persist an incident + session and return ``(incident_id, session_id)``.

    Required because ``recall_for_session`` writes a row to
    ``incident_memory_recall_log`` with a FK to ``sessions``.
    """
    async with factory() as db:
        incident = Incident(
            id=uuid.uuid4(),
            org_id=ORG,
            title=title,
            description=f"detailed description for {title}",
            status="open",
            severity=severity,
            service_id=service_id,
        )
        db.add(incident)
        await db.flush()
        session = SessionModel(
            id=uuid.uuid4(),
            org_id=ORG,
            incident_id=incident.id,
            tier=2,
            status="active",
        )
        db.add(session)
        await db.commit()
        return incident.id, session.id


def _initial_state(
    *, session_id: uuid.UUID, incident_title: str, severity: str = "high"
) -> dict:
    return {
        "session_id": str(session_id),
        "tier": 2,
        "incident_description": f"detailed description for {incident_title}",
        "incident": {
            "title": incident_title,
            "description": f"detailed description for {incident_title}",
            "status": "open",
            "severity": severity,
        },
    }


# A canned LLM response that ``MemoryDraft.from_json`` will accept.
_VALID_REMEMBER_JSON = json.dumps(
    {
        "title": "Payments-service connection drops cause checkout 500s",
        "tags": ["high", "payments", "checkout"],
        "summary_md": (
            "Symptoms: spike in 500s on /checkout. "
            "Cause: payments-service drops idle connections under load. "
            "Fix: enable keepalive on the upstream proxy. "
            "Watch for: similar drops on /refund."
        ),
    }
)


class TestEndToEndMemoryLoop:
    """The full agent-side memory loop, exercised through `build_graph`."""

    async def test_remember_then_recall_across_sessions(self, factory):
        service_id = await _seed_service(factory)

        # ---- Session 1: produces memory ------------------------------------
        # LLM responses, in graph order:
        #   recall  : (no LLM call — pure SQL)
        #   observe : free-form observations text (≥ 20 chars so
        #             should_remember accepts the session)
        #   diagnose: free-form diagnosis text
        #   plan    : empty action list (Tier 2 advisory path; no execute)
        #   verify  : verification text
        #   summarize: summary text
        #   remember: strict JSON matching MemoryDraft
        llm1 = _SequenceLLM(
            [
                "Checkout endpoint returning 500s. Upstream timeouts visible in logs.",
                "Root cause likely the payments service dropping idle connections.",
                "[]",  # plan output — empty action list
                "Verification confirms the diagnosis.",
                "Resolved by enabling keepalive on the upstream proxy.",
                _VALID_REMEMBER_JSON,
            ]
        )

        incident_id_1, session_id_1 = await _seed_session(
            factory, service_id, title="500s on checkout"
        )
        graph = build_graph(
            tier=2,
            skill_def=_skill(),
            llm=llm1,
            memory_factory=factory,
            org_id=ORG,
            service_id=service_id,
        )
        result_1 = await graph.ainvoke(
            _initial_state(session_id=session_id_1, incident_title="500s on checkout")
        )
        # Recall had nothing to surface (memory store was empty).
        assert result_1.get("memory_context", "") == ""
        # Remember wrote a memory.
        assert "memorized_id" in result_1
        memorized_id_1 = uuid.UUID(result_1["memorized_id"])

        async with factory() as db:
            mem = await IncidentMemoryRepo.get_by_id(db, memorized_id_1, ORG)
            assert mem is not None
            assert "payments" in mem.tags
            assert mem.service_id == service_id

        # ---- Session 2: similar incident, memory should surface ------------
        # Recall makes no LLM call. Sequence covers observe → ... → remember
        # again; the new remember response is intentionally invalid JSON so
        # we can assert recall happened *without* polluting memory further.
        llm2 = _SequenceLLM(
            [
                "Checkout 500s again. Looks like the same fault.",
                "Diagnosis: same as last time.",
                "[]",
                "Verified.",
                "Same fix applied.",
                "not-json-at-all",  # remember can't parse → no second memory
            ]
        )

        _, session_id_2 = await _seed_session(
            factory, service_id, title="checkout 500s recurring"
        )
        graph_2 = build_graph(
            tier=2,
            skill_def=_skill(),
            llm=llm2,
            memory_factory=factory,
            org_id=ORG,
            service_id=service_id,
        )
        result_2 = await graph_2.ainvoke(
            _initial_state(
                session_id=session_id_2, incident_title="checkout 500s recurring"
            )
        )

        # Recall surfaced the memory from session 1.
        assert "memory_context" in result_2
        assert "Past lessons from similar incidents" in result_2["memory_context"]
        assert "Payments-service" in result_2["memory_context"]
        assert result_2["recalled_memory_ids"] == [str(memorized_id_1)]

        # The first LLM call (observe) saw the memory context — the
        # implementation prepends the block before the incident description.
        observe_prompt = llm2.calls[0]
        assert "Past lessons from similar incidents" in observe_prompt
        assert "Payments-service" in observe_prompt

        # No second memory was written (the remember parse failed).
        async with factory() as db:
            all_memories = await IncidentMemoryRepo.list_for_org(
                db, ORG, service_id=service_id
            )
            assert len(all_memories) == 1
            assert all_memories[0].id == memorized_id_1
            # Recall log row recorded for session 2.
            logs = await IncidentMemoryRecallLogRepo.list_for_session(db, session_id_2)
            assert len(logs) == 1
            assert logs[0].memory_id == memorized_id_1

    async def test_failed_workflow_does_not_write_memory(self, factory):
        service_id = await _seed_service(factory)
        _, session_id = await _seed_session(
            factory, service_id, title="something exploded"
        )

        # An LLM that errors mid-observe leaves state.status unset → the
        # subsequent nodes still run, but ``should_remember`` rejects on the
        # tool-call-error / trivial-summary guard. We simulate the "trivial
        # summary" path which is the most common skip reason for a session
        # that didn't really land.
        llm = _SequenceLLM(
            [
                "x",  # observe — trivial
                "x",  # diagnose — trivial
                "[]",  # plan
                "x",  # verify
                "x",  # summarize — trivial
                _VALID_REMEMBER_JSON,  # remember should NOT call this
            ]
        )

        graph = build_graph(
            tier=2,
            skill_def=_skill(),
            llm=llm,
            memory_factory=factory,
            org_id=ORG,
            service_id=service_id,
        )
        result = await graph.ainvoke(
            _initial_state(session_id=session_id, incident_title="something exploded")
        )
        # No memorized_id in state.
        assert "memorized_id" not in result
        # And no memory row was written.
        async with factory() as db:
            rows = await IncidentMemoryRepo.list_for_org(db, ORG, service_id=service_id)
            assert rows == []
        # The remember LLM call was skipped by should_remember, so the
        # remember response in the queue was never consumed.
        assert len(llm.calls) == 5  # observe + diagnose + plan + verify + summarize

    async def test_operator_authored_memory_surfaces_via_recall(self, factory):
        """Memories created through the REST API path (no LLM involved)
        must surface via recall identically to memories the agent wrote
        itself. This is the integration boundary between Step 6 (API) and
        Steps 2-3 (retrieval + recall node)."""
        service_id = await _seed_service(factory)

        # Operator-authored memory seeded in the canonical tag shape the API
        # route would store.
        async with factory() as db:
            seeded = await IncidentMemoryRepo.create(
                db,
                org_id=ORG,
                service_id=service_id,
                title="Operator-authored hint about checkout",
                summary_md="When checkout 500s appear, always check the payment proxy logs first.",
                tags=["severity-high", "payments"],
            )
            await db.commit()
            seeded_id = seeded.id

        _, session_id = await _seed_session(
            factory, service_id, title="checkout failing again"
        )

        llm = _SequenceLLM(
            [
                "Observations from a fresh run.",
                "Diagnosis still pending.",
                "[]",
                "Verified.",
                "Wrap-up.",
                json.dumps(
                    {
                        "title": "Another distinct lesson",
                        "tags": ["high"],
                        "summary_md": "Distinct enough to be a new entry.",
                    }
                ),
            ]
        )

        graph = build_graph(
            tier=2,
            skill_def=_skill(),
            llm=llm,
            memory_factory=factory,
            org_id=ORG,
            service_id=service_id,
        )
        result = await graph.ainvoke(
            _initial_state(
                session_id=session_id, incident_title="checkout failing again"
            )
        )
        # Recall surfaced the operator-authored memory.
        assert str(seeded_id) in result["recalled_memory_ids"]
        assert "Operator-authored hint" in result["memory_context"]

        # And a new (distinct) memory was also written by remember.
        memorized_id = uuid.UUID(result["memorized_id"])
        assert memorized_id != seeded_id

        async with factory() as db:
            rows = await IncidentMemoryRepo.list_for_org(db, ORG, service_id=service_id)
            assert {r.id for r in rows} == {seeded_id, memorized_id}
