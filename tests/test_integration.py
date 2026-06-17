"""Integration tests for the full OpsMender pipeline.

Two categories:

1. **Simulated** (always runs) — mocked MCP session + StubLLM, validates the
   full wiring from CLI through graph to audit log.

2. **Live** (requires real K8s MCP server) — marked ``@pytest.mark.integration``,
   skipped unless ``--run-integration`` is passed to pytest.
   Requires: kubectl configured, npx available,
   ``@anthropic/mcp-server-k8s`` working.

Run simulated only (default):
    uv run pytest tests/test_integration.py

Run all including live:
    uv run pytest tests/test_integration.py --run-integration
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from backend.agent.graph import build_graph
from backend.agent.llm import StubLLM
from backend.audit.logger import AuditLogger
from backend.mcp.client import connect
from backend.config_loader import Config, MCPServerConfig
from backend.skills.parser import load as load_skill_def


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_mcp_result(text="ok"):
    from unittest.mock import MagicMock

    content_item = MagicMock()
    content_item.type = "text"
    content_item.text = text
    result = MagicMock()
    result.content = [content_item]
    result.isError = False
    return result


def _make_plan_llm(actions: list[dict]) -> StubLLM:
    """Return a StubLLM that returns JSON for the plan node and stubs elsewhere."""
    import json as _json

    class _PlanAwareLLM:
        def __init__(self):
            self.calls: list[str] = []

        def invoke(self, prompt: str) -> str:
            self.calls.append(prompt)
            if "remediation" in prompt.lower() or "plan" in prompt.lower():
                return _json.dumps(actions)
            return "[stub response]"

    return _PlanAwareLLM()


# ---------------------------------------------------------------------------
# 1. Simulated integration test (always runs)
# ---------------------------------------------------------------------------


class TestSimulatedEndToEnd:
    """Full pipeline with mocked MCP + StubLLM — validates wiring."""

    async def test_full_pipeline_with_safe_actions(self, tmp_path):
        """At Tier 1, every write — including a safe action — is routed through
        the approval gate; once the operator approves, it executes. (Advisory
        Tier 2 would block it outright.)"""
        audit_path = tmp_path / "audit.jsonl"
        skill_def = load_skill_def("examples/SKILL.md")

        # Mock MCP session
        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_mock_mcp_result("3 pods running"))

        # LLM that proposes a safe action
        llm = _make_plan_llm(
            [
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {"namespace": "default"},
                    "justification": "Check pods",
                },
            ]
        )
        sid = "11111111-1111-1111-1111-111111111111"
        logger = AuditLogger(audit_path)
        logger.log_session_start(sid, 1)

        # Tier 1 is interactive — supply an approval service that approves so
        # the safe action proceeds to execution.
        import uuid as _uuid
        from datetime import datetime as _dt
        from types import SimpleNamespace
        from backend.approvals.service import ApprovalResolution

        class _AutoApprove:
            async def request_and_wait(self, *, session_id, action, justification=None):
                req = SimpleNamespace(
                    id=_uuid.uuid4(),
                    status="approved",
                    action=action,
                    expires_at=_dt(2030, 1, 1),
                    resolution_note=None,
                )
                return ApprovalResolution(request=req)

        graph = build_graph(
            tier=1,
            skill_def=skill_def,
            llm=llm,
            mcp_session=session,
            audit_logger=logger,
            approval_service=_AutoApprove(),
        )

        result = await graph.ainvoke(
            {
                "session_id": sid,
                "tier": 1,
                "incident_description": "Pods crashing in namespace default",
            }
        )

        logger.log_session_end(sid, 1)

        # Assertions on workflow state
        assert result["status"] == "completed"
        assert result["session_id"] == sid
        assert result["observations"] != ""
        assert result["diagnosis"] != ""
        assert len(result["approved_actions"]) == 1
        assert len(result["blocked_actions"]) == 0
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["tool_name"] == "get_pods"
        assert result["tool_calls"][0]["permitted"] is True
        assert result["verification"] != ""
        assert result["summary"] != ""

        # MCP was called
        session.call_tool.assert_awaited_once_with("get_pods", {"namespace": "default"})

        # Audit log has entries
        entries = logger.read_all()
        types = [e.entry_type.value for e in entries]
        assert "session_start" in types
        assert "session_end" in types
        assert "tool_call_start" in types
        assert "tool_call_end" in types

    async def test_all_actions_blocked_at_tier_2_advisory(self, tmp_path):
        """Tier 2 is advisory only — every planned remediation action is
        blocked by the tier gate (read-only observation happens earlier in the
        observe node, not the plan/execute phase)."""
        audit_path = tmp_path / "audit.jsonl"
        skill_def = load_skill_def("examples/SKILL.md")

        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=_mock_mcp_result())

        llm = _make_plan_llm(
            [
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {},
                    "justification": "Read",
                },
                {
                    "tool_name": "delete_pod",
                    "tool_parameters": {"name": "bad-pod"},
                    "justification": "Kill it",
                },
            ]
        )
        logger = AuditLogger(audit_path)

        graph = build_graph(
            tier=2,
            skill_def=skill_def,
            llm=llm,
            mcp_session=session,
            audit_logger=logger,
        )

        result = await graph.ainvoke(
            {
                "session_id": "integration-test-002",
                "tier": 2,
                "incident_description": "Bad pod needs removal",
            }
        )

        assert result["status"] == "completed"
        # Advisory tier: nothing from the plan executes — both actions blocked.
        assert len(result["approved_actions"]) == 0
        assert len(result["blocked_actions"]) == 2
        blocked_tools = {a["tool_name"] for a in result["blocked_actions"]}
        assert blocked_tools == {"get_pods", "delete_pod"}

        # No remediation actions were executed under advisory tier.
        assert len(result["tool_calls"]) == 0

    async def test_tier_3_blocks_everything(self, tmp_path):
        """Legacy Tier 3 normalizes to advisory Tier 2 — all actions blocked."""
        audit_path = tmp_path / "audit.jsonl"
        skill_def = load_skill_def("examples/SKILL.md")

        session = AsyncMock()
        llm = _make_plan_llm(
            [
                {
                    "tool_name": "get_pods",
                    "tool_parameters": {},
                    "justification": "Look",
                },
            ]
        )
        logger = AuditLogger(audit_path)

        graph = build_graph(
            tier=3,
            skill_def=skill_def,
            llm=llm,
            mcp_session=session,
            audit_logger=logger,
        )

        result = await graph.ainvoke(
            {
                "session_id": "integration-test-003",
                "tier": 3,
                "incident_description": "Advise only test",
            }
        )

        assert result["status"] == "completed"
        assert len(result["approved_actions"]) == 0
        assert len(result["blocked_actions"]) == 1
        assert len(result["tool_calls"]) == 0
        session.call_tool.assert_not_awaited()

    async def test_no_mcp_stub_mode(self, tmp_path):
        """Without MCP, execute node produces no tool calls."""
        skill_def = load_skill_def("examples/SKILL.md")
        llm = StubLLM()

        graph = build_graph(
            tier=2,
            skill_def=skill_def,
            llm=llm,
        )

        result = await graph.ainvoke(
            {
                "session_id": "integration-test-004",
                "tier": 2,
                "incident_description": "Offline test",
            }
        )

        assert result["status"] == "completed"
        assert len(result["tool_calls"]) == 0

    def test_cli_dry_run_end_to_end(self, tmp_path, capsys):
        """opsmender run --dry-run exercises full CLI → graph → output path.

        This test is synchronous because ``main()`` calls ``asyncio.run()``
        internally, which cannot nest inside pytest-asyncio's event loop.
        We shell out to a subprocess instead.
        """
        out_file = tmp_path / "result.json"
        cfg_file = tmp_path / ".env"
        audit_file = tmp_path / "audit.jsonl"

        cfg_file.write_text(
            f"OPSMENDER_TIER=2\nOPSMENDER_LOG_LEVEL=INFO\nOPSMENDER_AUDIT_LOG={audit_file}\n"
        )

        import subprocess, sys

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli.opsmender",
                "--config",
                str(cfg_file),
                "run",
                "--incident",
                "Integration test incident",
                "--dry-run",
                "--output",
                str(out_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Output file written
        data = json.loads(out_file.read_text())
        assert data["status"] == "completed"
        assert data["incident_description"] == "Integration test incident"

        # Audit log created
        lines = audit_file.read_text().strip().splitlines()
        first = json.loads(lines[0])
        last = json.loads(lines[-1])
        assert first["entry_type"] == "session_start"
        assert last["entry_type"] == "session_end"
        assert first["session_id"] == last["session_id"]

        # Console output
        assert "INCIDENT RESPONSE COMPLETE" in result.stdout


# ---------------------------------------------------------------------------
# 2. Live integration test (requires real K8s MCP server)
# ---------------------------------------------------------------------------


def _has_kubectl_access() -> bool:
    """Check if kubectl can reach a cluster."""
    try:
        result = subprocess.run(
            ["kubectl", "cluster-info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _has_npx() -> bool:
    return shutil.which("npx") is not None


@pytest.mark.integration
class TestLiveK8sMCP:
    """Live tests against a real K8s MCP server via stdio.

    These tests connect to ``@anthropic/mcp-server-k8s`` via stdio,
    list tools, and call a safe read-only tool.

    Skipped unless ``--run-integration`` is passed.
    """

    @pytest.fixture
    def k8s_server(self):
        if not _has_npx():
            pytest.skip("npx not available")
        if not _has_kubectl_access():
            pytest.skip("no kubectl cluster access")
        return MCPServerConfig(
            name="k8s-test",
            transport="stdio",
            command="npx",
            args=["-y", "@anthropic/mcp-server-k8s"],
        )

    async def test_connect_and_list_tools(self, k8s_server):
        """Connect to the K8s MCP server and verify tools are listed."""
        from backend.mcp.client import list_tools

        async with connect(k8s_server) as session:
            tools = await list_tools(session)
            assert len(tools) > 0
            tool_names = [t.name for t in tools]
            # The K8s MCP server should expose at least some k8s tools
            assert any("get" in name or "list" in name for name in tool_names), (
                f"Expected k8s read tools, got: {tool_names}"
            )

    async def test_full_pipeline_live(self, k8s_server, tmp_path):
        """Run the full workflow against a live K8s MCP server (tier 2, safe only)."""
        audit_path = tmp_path / "audit.jsonl"
        skill_def = load_skill_def("examples/SKILL.md")
        logger = AuditLogger(audit_path)

        async with connect(k8s_server) as session:
            # Use stub LLM — we're testing MCP integration, not LLM quality
            llm = _make_plan_llm(
                [
                    {
                        "tool_name": "get_pods",
                        "tool_parameters": {"namespace": "default"},
                        "justification": "List pods",
                    },
                ]
            )

            logger.log_session_start("live-test-001", 2)

            graph = build_graph(
                tier=2,
                skill_def=skill_def,
                llm=llm,
                mcp_session=session,
                audit_logger=logger,
            )

            result = await graph.ainvoke(
                {
                    "session_id": "live-test-001",
                    "tier": 2,
                    "incident_description": "Live integration test — list pods",
                }
            )

            logger.log_session_end("live-test-001", 2)

        assert result["status"] == "completed"
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["tool_name"] == "get_pods"
        assert tc["permitted"] is True
        assert tc["error"] is None

        # Audit log should have the full lifecycle
        entries = logger.read_all()
        assert len(entries) >= 4  # start, tool_start, tool_end, end
