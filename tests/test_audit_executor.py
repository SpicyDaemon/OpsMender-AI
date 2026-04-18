"""Tests for the audited tool-call executor (backend/audit/executor.py)."""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.audit.executor import AuditedToolResult, audited_tool_call
from backend.audit.logger import AuditEntryType, AuditLogger
from backend.skills.parser import OperationClassification, SkillDefinition


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SESSION_ID = "test-session-exec-001"
TIER = 2


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


def _mock_content(text: str = "ok") -> Any:
    """Create a mock content item with .type and .text attributes."""
    item = MagicMock()
    item.type = "text"
    item.text = text
    return item


def _mock_mcp_result(text: str = "ok", is_error: bool = False) -> MagicMock:
    """Create a mock CallToolResult."""
    result = MagicMock()
    result.content = [_mock_content(text)]
    result.isError = is_error
    return result


@pytest.fixture()
def audit_log(tmp_path: pathlib.Path) -> AuditLogger:
    return AuditLogger(tmp_path / "audit.jsonl")


@pytest.fixture()
def mock_session() -> AsyncMock:
    """Return a mock MCP ClientSession."""
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=_mock_mcp_result())
    return session


# ---------------------------------------------------------------------------
# Permitted tool calls
# ---------------------------------------------------------------------------


class TestPermittedToolCall:
    @pytest.mark.asyncio
    async def test_safe_tool_at_tier_2_is_permitted(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        result = await audited_tool_call(
            session=mock_session,
            tool_name="get_pods",
            tool_parameters={"namespace": "default"},
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        assert result.permitted is True
        assert result.enforcement.permitted is True
        assert result.enforcement.classification == "safe"
        assert result.result is not None
        assert result.error is None
        assert result.duration_ms is not None and result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_caution_tool_at_tier_2_is_permitted(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        result = await audited_tool_call(
            session=mock_session,
            tool_name="scale_deployment",
            tool_parameters={"replicas": 3},
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        assert result.permitted is True
        assert result.enforcement.classification == "caution"

    @pytest.mark.asyncio
    async def test_permitted_call_logs_start_and_end(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        await audited_tool_call(
            session=mock_session,
            tool_name="get_pods",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        entries = audit_log.read_all()
        assert len(entries) == 2
        assert entries[0].entry_type == AuditEntryType.TOOL_CALL_START
        assert entries[1].entry_type == AuditEntryType.TOOL_CALL_END

    @pytest.mark.asyncio
    async def test_start_entry_has_tool_name_and_params(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        await audited_tool_call(
            session=mock_session,
            tool_name="get_pods",
            tool_parameters={"namespace": "kube-system"},
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        start = audit_log.read_all()[0]
        assert start.tool_name == "get_pods"
        assert start.tool_parameters == {"namespace": "kube-system"}

    @pytest.mark.asyncio
    async def test_end_entry_has_result_and_duration(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        await audited_tool_call(
            session=mock_session,
            tool_name="get_pods",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        end = audit_log.read_all()[1]
        assert end.result is not None
        assert end.duration_ms is not None
        assert end.permitted is True

    @pytest.mark.asyncio
    async def test_mcp_call_tool_receives_correct_args(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        params = {"namespace": "prod", "label": "app=api"}
        await audited_tool_call(
            session=mock_session,
            tool_name="get_pods",
            tool_parameters=params,
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        mock_session.call_tool.assert_awaited_once_with("get_pods", params)

    @pytest.mark.asyncio
    async def test_custom_tool_caller_is_used(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        sandbox_caller = AsyncMock(return_value=_mock_mcp_result())
        await audited_tool_call(
            session=mock_session,
            tool_name="get_pods",
            tool_parameters={"namespace": "default"},
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
            tool_caller=sandbox_caller,
        )
        sandbox_caller.assert_awaited_once_with(
            mock_session, "get_pods", {"namespace": "default"}
        )
        mock_session.call_tool.assert_not_awaited()


# ---------------------------------------------------------------------------
# Blocked tool calls
# ---------------------------------------------------------------------------


class TestBlockedToolCall:
    @pytest.mark.asyncio
    async def test_destructive_tool_at_tier_2_is_blocked(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        result = await audited_tool_call(
            session=mock_session,
            tool_name="delete_pod",
            tool_parameters={"pod": "api-server"},
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        assert result.permitted is False
        assert result.enforcement.permitted is False
        assert result.enforcement.classification == "destructive"
        assert result.result is None
        assert result.duration_ms is None

    @pytest.mark.asyncio
    async def test_blocked_call_does_not_execute_mcp(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        await audited_tool_call(
            session=mock_session,
            tool_name="delete_pod",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        mock_session.call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocked_call_logs_start_and_blocked(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        await audited_tool_call(
            session=mock_session,
            tool_name="delete_pod",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        entries = audit_log.read_all()
        assert len(entries) == 2
        assert entries[0].entry_type == AuditEntryType.TOOL_CALL_START
        assert entries[1].entry_type == AuditEntryType.TOOL_CALL_BLOCKED

    @pytest.mark.asyncio
    async def test_blocked_entry_has_reason(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        await audited_tool_call(
            session=mock_session,
            tool_name="delete_pod",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        blocked = audit_log.read_all()[1]
        assert blocked.permitted is False
        assert blocked.block_reason is not None
        assert "destructive" in blocked.block_reason.lower() or "deny" in blocked.block_reason.lower()

    @pytest.mark.asyncio
    async def test_unknown_tool_is_blocked(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        result = await audited_tool_call(
            session=mock_session,
            tool_name="totally_unknown_tool",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        assert result.permitted is False
        assert result.enforcement.classification == "unknown"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_mcp_error_is_captured(
        self, audit_log: AuditLogger
    ):
        session = AsyncMock()
        session.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))
        result = await audited_tool_call(
            session=session,
            tool_name="get_pods",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        assert result.permitted is True  # tier allowed it
        assert result.error == "connection lost"
        assert result.duration_ms is not None

    @pytest.mark.asyncio
    async def test_mcp_error_still_logs_end_entry(
        self, audit_log: AuditLogger
    ):
        session = AsyncMock()
        session.call_tool = AsyncMock(side_effect=RuntimeError("timeout"))
        await audited_tool_call(
            session=session,
            tool_name="get_pods",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        entries = audit_log.read_all()
        assert len(entries) == 2
        assert entries[1].entry_type == AuditEntryType.TOOL_CALL_END
        assert entries[1].result == {"error": "timeout"}

    @pytest.mark.asyncio
    async def test_default_empty_params(
        self, mock_session: AsyncMock, audit_log: AuditLogger
    ):
        """When tool_parameters is None, an empty dict is passed."""
        await audited_tool_call(
            session=mock_session,
            tool_name="get_pods",
            session_id=SESSION_ID,
            tier=TIER,
            skill_def=_skill_def(),
            logger=audit_log,
        )
        mock_session.call_tool.assert_awaited_once_with("get_pods", {})


# ---------------------------------------------------------------------------
# AuditedToolResult dataclass
# ---------------------------------------------------------------------------


class TestAuditedToolResult:
    def test_defaults(self):
        from backend.tiers.enforcement import EnforcementResult

        r = AuditedToolResult(
            permitted=True,
            enforcement=EnforcementResult(
                permitted=True, classification="safe", tier=2, reason="ok"
            ),
        )
        assert r.result is None
        assert r.error is None
        assert r.duration_ms is None
