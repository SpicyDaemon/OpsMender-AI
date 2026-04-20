"""Tests for the LangGraph incident response workflow.

Covers:
- Graph structure (nodes, edges)
- Stub node behavior (no LLM)
- LLM-powered node behavior (with StubLLM / mock)
- Tier gate (hard programmatic check — core enforcement contract)
- Execute node (with mocked MCP session + audit logger)
- Full graph invocation (end-to-end with and without LLM / MCP)
- LLM protocol and StubLLM
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.graph import build_graph, validate_workflow_node_order
from backend.agent.llm import LLM, StubLLM
from backend.agent.nodes import (
    _build_diagnose,
    _build_execute,
    _build_observe,
    _build_plan,
    _build_summarize,
    _build_tier_gate,
    _build_verify,
    diagnose,
    execute,
    observe,
    plan,
    summarize,
    verify,
)
from backend.agent.state import IncidentState
from backend.approvals import ApprovalService
from backend.audit.logger import AuditLogger
from backend.db.models import Base
from backend.db.repos import ApprovalRequestRepo, SessionRepo
from backend.skills.parser import OperationClassification, SkillDefinition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill_def() -> SkillDefinition:
    """Return a minimal skill definition for testing."""
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            OperationClassification(tool="get_pods", classification="safe"),
            OperationClassification(tool="scale_deployment", classification="caution"),
            OperationClassification(tool="delete_pod", classification="destructive"),
        ],
    )


def _base_state(**overrides) -> IncidentState:
    """Return a minimal IncidentState dict for testing."""
    state: IncidentState = {
        "session_id": "test-session-001",
        "tier": 2,
        "incident_description": "pods crashing in production",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# LLM protocol / StubLLM
# ---------------------------------------------------------------------------


class TestStubLLM:
    def test_returns_fixed_response(self):
        llm = StubLLM(response="hello")
        assert llm.invoke("anything") == "hello"

    def test_tracks_calls(self):
        llm = StubLLM()
        llm.invoke("prompt 1")
        llm.invoke("prompt 2")
        assert len(llm.calls) == 2
        assert llm.calls[0] == "prompt 1"

    def test_echo_mode(self):
        llm = StubLLM(echo=True)
        assert llm.invoke("echo this") == "echo this"

    def test_satisfies_protocol(self):
        assert isinstance(StubLLM(), LLM)


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------


class TestGraphStructure:
    def test_graph_compiles_without_llm(self):
        graph = build_graph(tier=2, skill_def=_skill_def())
        assert graph is not None

    def test_graph_compiles_with_llm(self):
        graph = build_graph(tier=2, skill_def=_skill_def(), llm=StubLLM())
        assert graph is not None

    def test_graph_has_all_nodes(self):
        graph = build_graph(tier=2, skill_def=_skill_def())
        node_names = set(graph.nodes.keys())
        expected = {"observe", "diagnose", "plan", "tier_gate", "execute", "verify", "summarize", "__start__"}
        assert expected.issubset(node_names)

    def test_graph_supports_custom_node_order(self):
        graph = build_graph(
            tier=2,
            skill_def=_skill_def(),
            node_order=["diagnose", "plan", "tier_gate", "execute", "summarize"],
        )
        node_names = set(graph.nodes.keys())
        assert "observe" not in node_names
        assert {"diagnose", "plan", "tier_gate", "execute", "summarize"}.issubset(node_names)

    def test_validate_workflow_node_order_rejects_execute_without_gate(self):
        with pytest.raises(ValueError):
            validate_workflow_node_order(["plan", "execute", "summarize"])


# ---------------------------------------------------------------------------
# Stub node tests (no LLM — backward compatibility)
# ---------------------------------------------------------------------------


class TestObserveNodeStub:
    def test_returns_observations(self):
        result = observe(_base_state())
        assert "observations" in result
        assert "pods crashing" in result["observations"]

    def test_sets_active_status(self):
        result = observe(_base_state())
        assert result["status"] == "active"

    def test_empty_description(self):
        result = observe(_base_state(incident_description=""))
        assert "observations" in result


class TestDiagnoseNodeStub:
    def test_returns_diagnosis(self):
        state = _base_state(observations="high CPU on node-1")
        result = diagnose(state)
        assert "diagnosis" in result
        assert "high CPU" in result["diagnosis"]


class TestPlanNodeStub:
    def test_returns_empty_plan(self):
        state = _base_state(diagnosis="pod OOMKilled")
        result = plan(state)
        assert "plan" in result
        assert isinstance(result["plan"], list)


class TestExecuteNode:
    def test_returns_empty_tool_calls(self):
        state = _base_state(approved_actions=[])
        result = execute(state)
        assert "tool_calls" in result
        assert result["tool_calls"] == []


class TestVerifyNodeStub:
    def test_returns_verification(self):
        state = _base_state(tool_calls=[])
        result = verify(state)
        assert "verification" in result
        assert "0 tool call" in result["verification"]

    def test_counts_tool_calls(self):
        state = _base_state(tool_calls=[{"tool_name": "a"}, {"tool_name": "b"}])
        result = verify(state)
        assert "2 tool call" in result["verification"]


class TestSummarizeNodeStub:
    def test_returns_summary(self):
        state = _base_state(diagnosis="OOM", verification="ok")
        result = summarize(state)
        assert "summary" in result
        assert "OOM" in result["summary"]

    def test_sets_completed_status(self):
        result = summarize(_base_state())
        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# LLM-powered node tests
# ---------------------------------------------------------------------------


class TestObserveWithLLM:
    def test_sends_prompt_with_description(self):
        llm = StubLLM(response="LLM observations here")
        node = _build_observe(llm)
        result = node(_base_state())
        assert result["observations"] == "LLM observations here"
        assert result["status"] == "active"
        # Check prompt contains the incident description
        assert "pods crashing" in llm.calls[0]

    def test_prompt_includes_sre_context(self):
        llm = StubLLM()
        node = _build_observe(llm)
        node(_base_state())
        assert "SRE" in llm.calls[0] or "Site Reliability" in llm.calls[0]


class TestDiagnoseWithLLM:
    def test_sends_observations_in_prompt(self):
        llm = StubLLM(response="root cause: OOM")
        node = _build_diagnose(llm)
        result = node(_base_state(observations="pod restarting 5 times"))
        assert result["diagnosis"] == "root cause: OOM"
        assert "pod restarting" in llm.calls[0]

    def test_prompt_asks_for_root_cause(self):
        llm = StubLLM()
        node = _build_diagnose(llm)
        node(_base_state(observations="test"))
        assert "root cause" in llm.calls[0].lower()


class TestPlanWithLLM:
    def test_parses_json_response(self):
        actions = [{"tool_name": "get_pods", "tool_parameters": {}, "justification": "check"}]
        llm = StubLLM(response=json.dumps(actions))
        node = _build_plan(llm, tier=2, skill_def=_skill_def())
        result = node(_base_state(diagnosis="OOM"))
        assert result["plan"] == actions

    def test_invalid_json_returns_empty_plan(self):
        llm = StubLLM(response="not valid json")
        node = _build_plan(llm, tier=2, skill_def=_skill_def())
        result = node(_base_state(diagnosis="test"))
        assert result["plan"] == []

    def test_non_list_json_returns_empty_plan(self):
        llm = StubLLM(response='{"not": "a list"}')
        node = _build_plan(llm, tier=2, skill_def=_skill_def())
        result = node(_base_state(diagnosis="test"))
        assert result["plan"] == []

    def test_prompt_includes_available_tools(self):
        llm = StubLLM(response="[]")
        node = _build_plan(llm, tier=2, skill_def=_skill_def())
        node(_base_state(diagnosis="test"))
        assert "get_pods" in llm.calls[0]
        assert "scale_deployment" in llm.calls[0]
        assert "delete_pod" in llm.calls[0]

    def test_prompt_includes_tier(self):
        llm = StubLLM(response="[]")
        node = _build_plan(llm, tier=2, skill_def=_skill_def())
        node(_base_state(diagnosis="test"))
        assert "tier: 2" in llm.calls[0].lower() or "tier 2" in llm.calls[0].lower()


class TestVerifyWithLLM:
    def test_sends_results_in_prompt(self):
        llm = StubLLM(response="incident resolved")
        node = _build_verify(llm)
        result = node(_base_state(
            diagnosis="OOM",
            tool_calls=[{"tool_name": "get_pods", "error": None}],
        ))
        assert result["verification"] == "incident resolved"

    def test_no_tool_calls(self):
        llm = StubLLM(response="no actions taken")
        node = _build_verify(llm)
        result = node(_base_state(diagnosis="test", tool_calls=[]))
        assert result["verification"] == "no actions taken"
        assert "no actions executed" in llm.calls[0]


class TestSummarizeWithLLM:
    def test_returns_summary(self):
        llm = StubLLM(response="Summary: incident resolved")
        node = _build_summarize(llm)
        result = node(_base_state(
            diagnosis="OOM",
            verification="resolved",
            tool_calls=[],
            blocked_actions=[],
        ))
        assert result["summary"] == "Summary: incident resolved"
        assert result["status"] == "completed"

    def test_prompt_includes_all_context(self):
        llm = StubLLM()
        node = _build_summarize(llm)
        node(_base_state(
            incident_description="pods down",
            diagnosis="OOM kill",
            verification="all ok",
            tool_calls=[{"tool_name": "a"}],
            blocked_actions=[{"tool_name": "b"}],
        ))
        prompt = llm.calls[0]
        assert "pods down" in prompt
        assert "OOM kill" in prompt
        assert "all ok" in prompt


# ---------------------------------------------------------------------------
# Tier gate (hard programmatic check)
# ---------------------------------------------------------------------------


class TestTierGate:
    """The tier gate is the most critical node — it MUST enforce the
    tier/skill matrix programmatically, not via LLM reasoning."""

    def test_safe_action_permitted_at_tier_2(self):
        gate = _build_tier_gate(tier=2, skill_def=_skill_def())
        state = _base_state(plan=[
            {"tool_name": "get_pods", "tool_parameters": {"ns": "default"}},
        ])
        result = gate(state)
        assert len(result["approved_actions"]) == 1
        assert len(result["blocked_actions"]) == 0

    def test_caution_action_permitted_at_tier_2(self):
        gate = _build_tier_gate(tier=2, skill_def=_skill_def())
        state = _base_state(plan=[
            {"tool_name": "scale_deployment", "tool_parameters": {"replicas": 3}},
        ])
        result = gate(state)
        assert len(result["approved_actions"]) == 1
        assert len(result["blocked_actions"]) == 0

    def test_destructive_action_blocked_at_tier_2(self):
        gate = _build_tier_gate(tier=2, skill_def=_skill_def())
        state = _base_state(plan=[
            {"tool_name": "delete_pod", "tool_parameters": {"pod": "api"}},
        ])
        result = gate(state)
        assert len(result["approved_actions"]) == 0
        assert len(result["blocked_actions"]) == 1

    def test_blocked_action_has_reason(self):
        gate = _build_tier_gate(tier=2, skill_def=_skill_def())
        state = _base_state(plan=[
            {"tool_name": "delete_pod", "tool_parameters": {}},
        ])
        result = gate(state)
        blocked = result["blocked_actions"][0]
        assert "block_reason" in blocked
        assert blocked["classification"] == "destructive"

    def test_unknown_tool_blocked(self):
        gate = _build_tier_gate(tier=2, skill_def=_skill_def())
        state = _base_state(plan=[
            {"tool_name": "totally_unknown", "tool_parameters": {}},
        ])
        result = gate(state)
        assert len(result["blocked_actions"]) == 1
        assert result["blocked_actions"][0]["classification"] == "unknown"

    def test_mixed_actions_split_correctly(self):
        gate = _build_tier_gate(tier=2, skill_def=_skill_def())
        state = _base_state(plan=[
            {"tool_name": "get_pods", "tool_parameters": {}},
            {"tool_name": "delete_pod", "tool_parameters": {}},
            {"tool_name": "scale_deployment", "tool_parameters": {}},
        ])
        result = gate(state)
        assert len(result["approved_actions"]) == 2
        assert len(result["blocked_actions"]) == 1

    def test_empty_plan(self):
        gate = _build_tier_gate(tier=2, skill_def=_skill_def())
        state = _base_state(plan=[])
        result = gate(state)
        assert result["approved_actions"] == []
        assert result["blocked_actions"] == []

    def test_tier_3_blocks_everything(self):
        gate = _build_tier_gate(tier=3, skill_def=_skill_def())
        state = _base_state(plan=[
            {"tool_name": "get_pods", "tool_parameters": {}},
            {"tool_name": "scale_deployment", "tool_parameters": {}},
            {"tool_name": "delete_pod", "tool_parameters": {}},
        ])
        result = gate(state)
        assert len(result["approved_actions"]) == 0
        assert len(result["blocked_actions"]) == 3

    def test_tier_1_without_approval_service_blocks_destructive_action(self):
        gate = _build_tier_gate(tier=1, skill_def=_skill_def())
        state = _base_state(plan=[
            {"tool_name": "delete_pod", "tool_parameters": {}},
        ])
        result = gate(state)
        assert len(result["approved_actions"]) == 0
        assert len(result["blocked_actions"]) == 1
        assert "approval service" in result["blocked_actions"][0]["block_reason"].lower()

    async def test_tier_1_approved_action_waits_and_executes(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as db:
            session = await SessionRepo.create(db, tier=1)
            await db.commit()
            await db.refresh(session)

        service = ApprovalService(factory, poll_interval_seconds=0.01)
        gate = _build_tier_gate(tier=1, skill_def=_skill_def(), approval_service=service)
        state = _base_state(
            session_id=str(session.id),
            tier=1,
            plan=[{"tool_name": "delete_pod", "tool_parameters": {}}],
        )

        task = asyncio.create_task(gate(state))
        await asyncio.sleep(0.05)

        async with factory() as db:
            pending = await ApprovalRequestRepo.list_pending(db, session_id=session.id)
            assert len(pending) == 1
            await ApprovalRequestRepo.resolve(db, pending[0].id, status="approved")
            await db.commit()

        result = await task
        assert len(result["approved_actions"]) == 1
        assert len(result["blocked_actions"]) == 0
        assert result["approval_requests"][0]["status"] == "approved"

        await engine.dispose()

    async def test_tier_1_rejected_action_is_blocked(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as db:
            session = await SessionRepo.create(db, tier=1)
            await db.commit()
            await db.refresh(session)

        service = ApprovalService(factory, poll_interval_seconds=0.01)
        gate = _build_tier_gate(tier=1, skill_def=_skill_def(), approval_service=service)
        state = _base_state(
            session_id=str(session.id),
            tier=1,
            plan=[{"tool_name": "delete_pod", "tool_parameters": {}}],
        )

        task = asyncio.create_task(gate(state))
        await asyncio.sleep(0.05)

        async with factory() as db:
            pending = await ApprovalRequestRepo.list_pending(db, session_id=session.id)
            await ApprovalRequestRepo.resolve(db, pending[0].id, status="rejected")
            await db.commit()

        result = await task
        assert len(result["approved_actions"]) == 0
        assert len(result["blocked_actions"]) == 1
        assert "rejected" in result["blocked_actions"][0]["block_reason"].lower()

        await engine.dispose()

    async def test_tier_1_timeout_marks_state_timed_out(self):
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as db:
            session = await SessionRepo.create(db, tier=1)
            await db.commit()
            await db.refresh(session)

        service = ApprovalService(factory, timeout_seconds=0, poll_interval_seconds=0.01)
        gate = _build_tier_gate(tier=1, skill_def=_skill_def(), approval_service=service)
        result = await gate(_base_state(
            session_id=str(session.id),
            tier=1,
            plan=[{"tool_name": "delete_pod", "tool_parameters": {}}],
        ))
        assert result["status"] == "timed_out"
        assert len(result["blocked_actions"]) == 1
        assert "timed out" in result["blocked_actions"][0]["block_reason"].lower()

        await engine.dispose()


# ---------------------------------------------------------------------------
# Full graph invocation — stub mode (no LLM)
# ---------------------------------------------------------------------------


class TestFullGraphStub:
    def test_invoke_returns_completed(self):
        graph = build_graph(tier=2, skill_def=_skill_def())
        result = graph.invoke({
            "session_id": "e2e-stub-001",
            "tier": 2,
            "incident_description": "pods crashing",
        })
        assert result["status"] == "completed"

    def test_invoke_has_all_fields(self):
        graph = build_graph(tier=2, skill_def=_skill_def())
        result = graph.invoke({
            "session_id": "e2e-stub-002",
            "tier": 2,
            "incident_description": "high latency",
        })
        for key in ("observations", "diagnosis", "plan", "approved_actions",
                     "blocked_actions", "verification", "summary"):
            assert key in result

    def test_invoke_preserves_session_id(self):
        graph = build_graph(tier=2, skill_def=_skill_def())
        result = graph.invoke({
            "session_id": "e2e-stub-003",
            "tier": 2,
            "incident_description": "test",
        })
        assert result["session_id"] == "e2e-stub-003"


# ---------------------------------------------------------------------------
# Full graph invocation — LLM mode
# ---------------------------------------------------------------------------


class TestFullGraphWithLLM:
    def test_invoke_with_llm(self):
        llm = StubLLM(response="[]")  # plan returns [] → no actions
        graph = build_graph(tier=2, skill_def=_skill_def(), llm=llm)
        result = graph.invoke({
            "session_id": "e2e-llm-001",
            "tier": 2,
            "incident_description": "pods crashing",
        })
        assert result["status"] == "completed"
        # LLM was called for: observe, diagnose, plan, verify, summarize = 5
        assert len(llm.calls) == 5

    def test_invoke_with_planned_actions(self):
        """LLM returns a plan with actions → tier gate splits them."""
        actions = json.dumps([
            {"tool_name": "get_pods", "tool_parameters": {}, "justification": "check"},
            {"tool_name": "delete_pod", "tool_parameters": {}, "justification": "cleanup"},
        ])
        call_count = 0

        class PlanningLLM:
            def __init__(self):
                self.calls = []

            def invoke(self, prompt: str) -> str:
                self.calls.append(prompt)
                # Return the plan JSON only for the plan node prompt
                if "remediation" in prompt.lower():
                    return actions
                return "[stub response]"

        llm = PlanningLLM()
        graph = build_graph(tier=2, skill_def=_skill_def(), llm=llm)
        result = graph.invoke({
            "session_id": "e2e-llm-002",
            "tier": 2,
            "incident_description": "test",
        })
        # get_pods approved, delete_pod blocked at tier 2
        assert len(result["approved_actions"]) == 1
        assert len(result["blocked_actions"]) == 1
        assert result["blocked_actions"][0]["tool_name"] == "delete_pod"


# ---------------------------------------------------------------------------
# Execute node with mocked MCP
# ---------------------------------------------------------------------------


def _mock_content(text: str = "ok"):
    item = MagicMock()
    item.type = "text"
    item.text = text
    return item


def _mock_mcp_result(text: str = "ok", is_error: bool = False):
    result = MagicMock()
    result.content = [_mock_content(text)]
    result.isError = is_error
    return result


class TestExecuteWithMCP:
    """Tests for _build_execute with a mocked MCP session."""

    async def test_executes_approved_action(self, tmp_path):
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_mock_mcp_result("pods listed"))
        logger = AuditLogger(tmp_path / "audit.jsonl")

        node = _build_execute(session, _skill_def(), logger)
        result = await node(_base_state(
            approved_actions=[{"tool_name": "get_pods", "tool_parameters": {"ns": "default"}}],
        ))
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["tool_name"] == "get_pods"
        assert tc["permitted"] is True
        assert tc["result"] is not None
        assert tc["error"] is None
        assert tc["duration_ms"] is not None

    async def test_multiple_actions(self, tmp_path):
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_mock_mcp_result())
        logger = AuditLogger(tmp_path / "audit.jsonl")

        node = _build_execute(session, _skill_def(), logger)
        result = await node(_base_state(
            approved_actions=[
                {"tool_name": "get_pods", "tool_parameters": {}},
                {"tool_name": "scale_deployment", "tool_parameters": {"replicas": 3}},
            ],
        ))
        assert len(result["tool_calls"]) == 2
        assert session.call_tool.await_count == 2

    async def test_mcp_error_captured(self, tmp_path):
        session = AsyncMock()
        session.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))
        logger = AuditLogger(tmp_path / "audit.jsonl")

        node = _build_execute(session, _skill_def(), logger)
        result = await node(_base_state(
            approved_actions=[{"tool_name": "get_pods", "tool_parameters": {}}],
        ))
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["error"] == "connection lost"
        assert tc["permitted"] is True  # tier allowed it, MCP just failed

    async def test_empty_approved_actions(self, tmp_path):
        session = AsyncMock()
        logger = AuditLogger(tmp_path / "audit.jsonl")

        node = _build_execute(session, _skill_def(), logger)
        result = await node(_base_state(approved_actions=[]))
        assert result["tool_calls"] == []
        session.call_tool.assert_not_awaited()

    async def test_audit_log_entries_created(self, tmp_path):
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_mock_mcp_result())
        logger = AuditLogger(tmp_path / "audit.jsonl")

        node = _build_execute(session, _skill_def(), logger)
        await node(_base_state(
            approved_actions=[{"tool_name": "get_pods", "tool_parameters": {}}],
        ))
        entries = logger.read_all()
        # audited_tool_call logs: start + end = 2 entries per call
        assert len(entries) == 2


# ---------------------------------------------------------------------------
# Full graph with MCP execution
# ---------------------------------------------------------------------------


class TestFullGraphWithMCP:
    async def test_e2e_with_mcp_execution(self, tmp_path):
        """Full pipeline: LLM plans → tier gate filters → execute calls MCP."""
        actions = json.dumps([
            {"tool_name": "get_pods", "tool_parameters": {"ns": "default"}, "justification": "check"},
            {"tool_name": "delete_pod", "tool_parameters": {"pod": "x"}, "justification": "cleanup"},
        ])

        class PlanningLLM:
            def __init__(self):
                self.calls = []
            def invoke(self, prompt: str) -> str:
                self.calls.append(prompt)
                if "remediation" in prompt.lower():
                    return actions
                return "[stub]"

        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_mock_mcp_result("pods ok"))
        logger = AuditLogger(tmp_path / "audit.jsonl")

        graph = build_graph(
            tier=2,
            skill_def=_skill_def(),
            llm=PlanningLLM(),
            mcp_session=session,
            audit_logger=logger,
        )
        result = await graph.ainvoke({
            "session_id": "e2e-mcp-001",
            "tier": 2,
            "incident_description": "pods crashing",
        })

        # get_pods approved and executed, delete_pod blocked
        assert len(result["approved_actions"]) == 1
        assert len(result["blocked_actions"]) == 1
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["tool_name"] == "get_pods"
        assert result["tool_calls"][0]["permitted"] is True
        assert result["status"] == "completed"

        # MCP was called exactly once (only for get_pods)
        session.call_tool.assert_awaited_once()

        # Audit log was written
        entries = logger.read_all()
        assert len(entries) >= 2  # start + end for get_pods
