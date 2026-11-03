"""M1-105: an approval service may decline to ask, because the session it
belongs to has ended. The tier gate and a workflow step block that action with
the service's reason and go on, so what the same step already recorded is
kept and the rest of the plan is still gated."""

from __future__ import annotations

import json
import uuid

from backend.agent.nodes import _build_tier_gate
from backend.agent.workflow_executor import WorkflowExecutor
from backend.approvals import ApprovalNotAsked
from backend.audit.logger import AuditLogger
from backend.skills.parser import loads
from tests.test_cli_run import SKILL_T1, WORKFLOW_T1

REASON = "the session was stopped; the approval was not asked"


class _Declines:
    def __init__(self) -> None:
        self.asked = 0

    async def request_and_wait(self, **kwargs):
        self.asked += 1
        raise ApprovalNotAsked(REASON)


def _entries(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def test_the_gate_blocks_an_action_nobody_was_asked_about_and_goes_on(
    tmp_path,
):
    log = tmp_path / "audit.jsonl"
    approvals = _Declines()
    gate = _build_tier_gate(
        1, loads(SKILL_T1), approvals, audit_logger=AuditLogger(log)
    )

    update = await gate(
        {
            "session_id": str(uuid.uuid4()),
            "plan": [
                {"tool_name": "restart_pod", "tool_parameters": {"pod": "a"}},
                {"tool_name": "get_pods", "tool_parameters": {}},
                {"tool_name": "restart_pod", "tool_parameters": {"pod": "b"}},
            ],
        }
    )

    assert approvals.asked == 2
    assert [a["tool_name"] for a in update["approved_actions"]] == ["get_pods"]
    assert [
        (a["tool_parameters"], a["block_reason"]) for a in update["blocked_actions"]
    ] == [({"pod": "a"}, REASON), ({"pod": "b"}, REASON)]
    assert all("approval_request_id" not in a for a in update["blocked_actions"])
    assert update["approval_requests"] == []
    # Nobody was asked, so nothing timed out.
    assert update["status"] is None and update["error"] is None
    blocked = [e for e in _entries(log) if e["entry_type"] == "tool_call_blocked"]
    assert [(e["tool_name"], e["block_reason"]) for e in blocked] == [
        ("restart_pod", REASON),
        ("restart_pod", REASON),
    ]


async def test_a_workflow_step_nobody_was_asked_about_is_blocked_and_the_rest_runs(
    tmp_path,
):
    calls: list[str] = []

    async def caller(_session, tool_name, params):
        calls.append(tool_name)
        return {"content": [{"type": "text", "text": "2 pods"}], "isError": False}

    executor = WorkflowExecutor(
        mcp_session=object(),
        skill_def=loads(SKILL_T1 + WORKFLOW_T1),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        tier=1,
        approval_service=_Declines(),
        tool_caller=caller,
    )

    result = await executor.execute(session_id=str(uuid.uuid4()), incident={})

    assert [(o.step_id, o.status) for o in result.outcomes] == [
        ("look", "completed"),
        ("fix", "blocked"),
        ("look_again", "completed"),
    ]
    assert result.outcomes[1].block_reason == REASON
    assert result.outcomes[1].approval_request_id is None
    assert calls == ["get_pods", "get_pods"]
    assert not result.aborted
