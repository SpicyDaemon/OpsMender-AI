"""M1-103: a session that cannot run a tool asks no approval.

``build_graph`` builds the tier gate with the approval service whether or not
the session has an executor. Without one (no usable MCP server or connector),
a Tier 1 plan still created approval requests that an operator could approve
while nothing could run, and the actions the gate permitted were dropped
without a record. The gate now blocks every action of such a session with
that reason, audits each like any blocked action and asks no approval.
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agent.graph import build_graph
from backend.agent.nodes import (
    NO_EXECUTOR_REASON,
    NO_TOOL_RULE,
    _build_tier_gate,
)
from backend.audit.logger import AuditLogger
from backend.skills.parser import loads
from tests.test_blocked_audit import PLAN, _blocked
from tests.test_tier2_reads import SKILL


class _NeverAsked:
    """An approval service the gate must not reach."""

    def __init__(self) -> None:
        self.calls = 0

    async def request_and_wait(self, *, session_id, action, justification=None):
        self.calls += 1
        raise AssertionError("the gate asked for an approval it cannot use")


@pytest.mark.parametrize("tier", [0, 1, 2])
async def test_the_gate_blocks_every_action_and_asks_nobody(tmp_path, tier):
    log = tmp_path / "audit.jsonl"
    approvals = _NeverAsked()
    gate = _build_tier_gate(
        tier, loads(SKILL), approvals, audit_logger=AuditLogger(log), can_run=False
    )
    session_id = str(uuid.uuid4())

    update = await gate({"session_id": session_id, "plan": PLAN})

    assert update["approved_actions"] == []
    assert update["approval_requests"] == []
    assert approvals.calls == 0
    assert [a["tool_name"] for a in update["blocked_actions"]] == [
        "get_pods",
        "restart_pod",
        "shell",
    ]
    assert all(
        a["block_reason"] == NO_EXECUTOR_REASON for a in update["blocked_actions"]
    )
    # The class still comes from the skill, so the record says what it was.
    assert [a["classification"] for a in update["blocked_actions"]][0] == "safe"
    blocked = _blocked(log)
    assert [e["tool_name"] for e in blocked] == ["get_pods", "restart_pod", "shell"]
    assert all(
        e["tier"] == tier
        and e["session_id"] == session_id
        and e["permitted"] is False
        and e["block_reason"] == NO_EXECUTOR_REASON
        for e in blocked
    )


async def test_a_session_without_a_tool_source_creates_no_approval_request(
    tmp_path,
):
    class PlanningLLM:
        def invoke(self, prompt: str) -> str:
            if "Propose a list of remediation actions" in prompt:
                return json.dumps(PLAN)
            return "[]"

    log = tmp_path / "audit.jsonl"
    approvals = _NeverAsked()
    graph = build_graph(
        tier=1,
        skill_def=loads(SKILL),
        llm=PlanningLLM(),
        audit_logger=AuditLogger(log),
        approval_service=approvals,
    )

    result = await graph.ainvoke(
        {"session_id": str(uuid.uuid4()), "tier": 1, "incident_description": "500s"}
    )

    assert approvals.calls == 0
    assert result["approval_requests"] == []
    assert result["approved_actions"] == []
    assert [a["block_reason"] for a in result["blocked_actions"]] == [
        NO_EXECUTOR_REASON
    ] * 3
    assert [e["tool_name"] for e in _blocked(log)] == [
        "get_pods",
        "restart_pod",
        "shell",
    ]
    assert result["tool_calls"] == []


async def test_a_session_with_a_tool_source_still_asks(tmp_path):
    """The gate with ``can_run`` left at its default is unchanged: an approval
    action at Tier 1 reaches the approval service."""
    from tests.test_blocked_audit import _Approvals

    approvals = _Approvals("approved")
    gate = _build_tier_gate(
        1, loads(SKILL), approvals, audit_logger=AuditLogger(tmp_path / "a.jsonl")
    )
    update = await gate({"session_id": str(uuid.uuid4()), "plan": PLAN[1:2]})
    assert [a["tool_name"] for a in update["approved_actions"]] == ["restart_pod"]
    assert len(update["approval_requests"]) == 1


def test_the_no_tool_rule_says_a_listed_action_is_recorded():
    assert "return an empty plan: []" in NO_TOOL_RULE
    assert "recorded for a person to see" in NO_TOOL_RULE
    assert "nothing runs" in NO_TOOL_RULE
