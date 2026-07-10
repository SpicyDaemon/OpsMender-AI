"""Tests for backend.workflow.rollback — Tier 0 compensating-inverse replay."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.skills.parser import SkillDefinition
from tests.skill_policy_helpers import explicit_operation
from backend.workflow.rollback import (
    RollbackReport,
    RollbackStep,
    reconstruct_tool_calls,
    replay_compensating_inverses,
)


@pytest.fixture()
def skill_def():
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            explicit_operation("get_pods", "safe"),
            explicit_operation(
                tool="cordon_node",
                classification="caution",
                reversible=True,
                compensating_inverse="uncordon_node",
            ),
            explicit_operation(
                tool="uncordon_node",
                classification="caution",
                reversible=True,
                compensating_inverse="cordon_node",
            ),
            explicit_operation(
                tool="rollout_restart",
                classification="caution",
                reversible=True,
                # no compensating_inverse: skipped
            ),
        ],
    )


def _call(tool, params, *, permitted=True, error=None):
    return {
        "tool_name": tool,
        "tool_parameters": params,
        "permitted": permitted,
        "error": error,
    }


class TestReverseOrder:
    async def test_inverses_called_in_reverse_order(self, skill_def):
        caller = AsyncMock(return_value={"ok": True})
        tool_calls = [
            _call("cordon_node", {"node": "n1"}),
            _call("cordon_node", {"node": "n2"}),
        ]
        report = await replay_compensating_inverses(
            session_id="s1",
            tier=0,
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=caller,
        )
        # Reverse order: n2 first, then n1.
        assert caller.await_args_list[0].args == ("uncordon_node", {"node": "n2"})
        assert caller.await_args_list[1].args == ("uncordon_node", {"node": "n1"})
        assert report.succeeded == 2
        assert report.failed == 0


class TestSkipBehaviour:
    async def test_skips_tools_without_declared_inverse(self, skill_def):
        caller = AsyncMock()
        tool_calls = [_call("rollout_restart", {"deploy": "api"})]
        report = await replay_compensating_inverses(
            session_id="s1",
            tier=0,
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=caller,
        )
        caller.assert_not_awaited()
        assert report.skipped == 1
        assert report.steps[0].status == "skipped_no_inverse"

    async def test_skips_blocked_calls(self, skill_def):
        caller = AsyncMock()
        tool_calls = [
            _call("cordon_node", {"node": "n1"}, permitted=False),
        ]
        report = await replay_compensating_inverses(
            session_id="s1",
            tier=0,
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=caller,
        )
        caller.assert_not_awaited()
        assert report.steps[0].status == "skipped_not_permitted"

    async def test_skips_errored_calls(self, skill_def):
        caller = AsyncMock()
        tool_calls = [_call("cordon_node", {"node": "n1"}, error="boom")]
        report = await replay_compensating_inverses(
            session_id="s1",
            tier=0,
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=caller,
        )
        caller.assert_not_awaited()
        assert report.steps[0].status == "skipped_not_permitted"


class TestFailureHandling:
    async def test_one_failure_does_not_abort_the_rest(self, skill_def):
        call_results = [RuntimeError("first boom"), {"ok": True}]

        async def flaky_caller(tool, params):
            r = call_results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        tool_calls = [
            _call("cordon_node", {"node": "n1"}),
            _call("cordon_node", {"node": "n2"}),
        ]
        report = await replay_compensating_inverses(
            session_id="s1",
            tier=0,
            tool_calls=tool_calls,
            skill_def=skill_def,
            caller=flaky_caller,
        )
        assert report.attempted == 2
        assert report.succeeded == 1
        assert report.failed == 1
        # Reverse order — n2 first (failed), then n1 (succeeded).
        assert report.steps[0].original_tool == "cordon_node"
        assert report.steps[0].status == "failed"
        assert "first boom" in (report.steps[0].error or "")
        assert report.steps[1].status == "succeeded"


class TestAuditIntegration:
    async def test_logs_start_and_end_per_successful_step(self, skill_def):
        caller = AsyncMock(return_value={"ok": True})
        logger = MagicMock()
        logger.log_tool_call_start = MagicMock()
        logger.log_tool_call_end = MagicMock()
        logger.log_tool_call_blocked = MagicMock()

        await replay_compensating_inverses(
            session_id="s1",
            tier=0,
            tool_calls=[_call("cordon_node", {"node": "n1"})],
            skill_def=skill_def,
            caller=caller,
            audit_logger=logger,
        )
        logger.log_tool_call_start.assert_called_once()
        start_kwargs = logger.log_tool_call_start.call_args.kwargs
        assert start_kwargs["tool_name"] == "uncordon_node"
        assert start_kwargs["tool_parameters"]["_rollback_of"] == "cordon_node"
        logger.log_tool_call_end.assert_called_once()

    async def test_logs_blocked_for_skipped_no_inverse(self, skill_def):
        caller = AsyncMock()
        logger = MagicMock()
        logger.log_tool_call_blocked = MagicMock()

        await replay_compensating_inverses(
            session_id="s1",
            tier=0,
            tool_calls=[_call("rollout_restart", {"deploy": "api"})],
            skill_def=skill_def,
            caller=caller,
            audit_logger=logger,
        )
        logger.log_tool_call_blocked.assert_called_once()
        kwargs = logger.log_tool_call_blocked.call_args.kwargs
        assert kwargs["tool_name"] == "rollout_restart"
        assert "no compensating inverse" in kwargs["block_reason"]

    async def test_broken_logger_does_not_abort_rollback(self, skill_def):
        caller = AsyncMock(return_value={"ok": True})
        logger = MagicMock()
        logger.log_tool_call_start = MagicMock(side_effect=RuntimeError("audit down"))
        logger.log_tool_call_end = MagicMock()

        report = await replay_compensating_inverses(
            session_id="s1",
            tier=0,
            tool_calls=[_call("cordon_node", {"node": "n1"})],
            skill_def=skill_def,
            caller=caller,
            audit_logger=logger,
        )
        assert report.succeeded == 1


class TestReportMath:
    def test_counts(self):
        r = RollbackReport(
            steps=[
                RollbackStep("cordon_node", "uncordon_node", {}, "succeeded"),
                RollbackStep("cordon_node", "uncordon_node", {}, "failed", "boom"),
                RollbackStep("rollout_restart", None, {}, "skipped_no_inverse"),
                RollbackStep("delete_pod", None, {}, "skipped_not_permitted"),
            ]
        )
        assert r.attempted == 2
        assert r.succeeded == 1
        assert r.failed == 1
        assert r.skipped == 2


class TestReconstructToolCalls:
    def test_reconstructs_only_successful_calls(self):
        entries = [
            {
                "entry_type": "tool_call_start",
                "tool_name": "cordon_node",
                "tool_parameters": {"node": "n1"},
                "permitted": True,
            },
            {
                "entry_type": "tool_call_end",
                "tool_name": "cordon_node",
            },
            {
                "entry_type": "tool_call_start",
                "tool_name": "delete_pod",
                "tool_parameters": {"pod": "api"},
                "permitted": True,
            },
            {
                "entry_type": "tool_call_blocked",
                "tool_name": "delete_pod",
                "permitted": False,
            },
        ]
        tool_calls = reconstruct_tool_calls(entries)
        assert tool_calls == [
            {
                "tool_name": "cordon_node",
                "tool_parameters": {"node": "n1"},
                "permitted": True,
                "error": None,
            }
        ]
