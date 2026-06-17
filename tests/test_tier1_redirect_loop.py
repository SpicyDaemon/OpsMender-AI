"""Tier 1 interactive redirect loop (graph-level).

When an operator resolves an approval with the ``redirected`` decision, the
tier_gate abandons the current plan pass and the graph loops back to the plan
node with the operator's free-text guidance threaded into state, so the agent
re-plans. After a redirect, an approve lets the action execute.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from backend.agent.graph import build_graph
from backend.agent.llm import StubLLM
from backend.approvals.service import ApprovalResolution
from backend.skills.parser import SkillDefinition, OperationClassification


@dataclass
class _FakeReq:
    id: uuid.UUID
    status: str
    action: dict[str, Any]
    expires_at: datetime
    resolution_note: str | None = None


class _RedirectThenApprove:
    """Fake approval service: first call redirects with guidance, then approves."""

    def __init__(self) -> None:
        self.calls = 0

    async def request_and_wait(self, *, session_id, action, justification=None):
        self.calls += 1
        if self.calls == 1:
            req = _FakeReq(
                id=uuid.uuid4(),
                status="redirected",
                action=action,
                expires_at=datetime(2030, 1, 1),
                resolution_note="use the canary deployment first",
            )
            return ApprovalResolution(
                request=req, guidance="use the canary deployment first"
            )
        req = _FakeReq(
            id=uuid.uuid4(),
            status="approved",
            action=action,
            expires_at=datetime(2030, 1, 1),
        )
        return ApprovalResolution(request=req)


@pytest.fixture()
def skill_def() -> SkillDefinition:
    # Classification-only (legacy) skill → the enforcement legacy branch makes
    # every write require approval at Tier 1.
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            OperationClassification(tool="restart_service", classification="caution"),
        ],
    )


def _plan_json() -> str:
    return (
        '[{"tool_name": "restart_service", "tool_parameters": {}, '
        '"justification": "restart the failing service"}]'
    )


async def test_redirect_loops_back_to_plan_then_approves(skill_def):
    approval = _RedirectThenApprove()
    # StubLLM returns the same plan JSON for every node call; the plan node
    # parses it into a single caution action that requires approval at Tier 1.
    llm = StubLLM(response=_plan_json())

    graph = build_graph(
        tier=1,
        skill_def=skill_def,
        llm=llm,
        approval_service=approval,
    )

    initial_state = {
        "session_id": str(uuid.uuid4()),
        "tier": 1,
        "incident_description": "service is down",
        "incident": {"title": "down", "description": "service is down", "status": "open"},
        "operator_guidance": [],
        "redirect_requested": False,
        "redirect_count": 0,
    }

    result = await graph.ainvoke(initial_state, {"recursion_limit": 60})

    # The approval gate was hit twice: once for the redirect, once for the
    # approve after re-planning. That proves the loop ran.
    assert approval.calls == 2
    # The operator's guidance was threaded into state for the re-plan.
    assert "use the canary deployment first" in result.get("operator_guidance", [])
    # The final pass approved and queued the action for execution.
    assert any(
        a.get("tool_name") == "restart_service"
        for a in result.get("approved_actions", [])
    )
    # The loop terminated cleanly (no lingering redirect flag).
    assert result.get("redirect_requested") is False
    assert result.get("redirect_count") == 1
