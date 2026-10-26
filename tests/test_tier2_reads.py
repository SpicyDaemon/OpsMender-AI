"""Tier 2 runs the reads a skill allows (M1-94, owner decision O-28, AI-04).

In the 2026-10-24 live round a Tier 2 session ran no tool at all, reads
included, so its advice rested on the alert text only. A skill may now mark an
operation it classifies safe as autonomous at T2; writes stay blocked, approval
never applies at Tier 2, and the frozen tier check is unchanged: it already
runs an enabled autonomous policy at any tier and still blocks generic command
tools at Tier 2.
"""

from __future__ import annotations

import json
import uuid

from mcp.types import CallToolResult, TextContent

from backend.agent.graph import build_graph
from backend.audit.logger import AuditLogger
from backend.integrations.base import IntegrationCapability
from backend.integrations.tools import IntegrationToolDescriptor, _operation_for
from backend.skills.parser import loads
from backend.skills.template import build_skill_from_tools
from backend.tiers.enforcement import check

SKILL = """---
version: "1"
environment: t
operations:
  - tool: get_pods
    classification: safe
    tiers:
      T0: {enabled: true, mode: autonomous}
      T1: {enabled: true, mode: autonomous}
      T2: {enabled: true, mode: autonomous}
  - tool: restart_pod
    classification: caution
    reversible: true
    compensating_inverse: restart_pod
    tiers:
      T0: {enabled: true, mode: autonomous, require_reversible: true}
      T1: {enabled: true, mode: approval}
      T2: {enabled: false, mode: blocked}
  - tool: shell
    classification: safe
    tiers:
      T0: {enabled: true, mode: autonomous}
      T1: {enabled: true, mode: autonomous}
      T2: {enabled: true, mode: autonomous}
---
"""


def test_the_tier_check_runs_a_safe_read_at_tier_2_and_blocks_the_rest():
    skill = loads(SKILL)
    assert check("get_pods", 2, skill).permitted is True
    assert check("restart_pod", 2, skill).permitted is False
    # A generic command tool stays blocked at Tier 2 whatever the skill says.
    assert check("shell", 2, skill).permitted is False


async def test_a_tier_2_session_reads_and_blocks_the_write(tmp_path):
    plan = json.dumps(
        [
            {
                "tool_name": "get_pods",
                "tool_parameters": {"namespace": "shop"},
                "justification": "read",
            },
            {
                "tool_name": "restart_pod",
                "tool_parameters": {"pod": "ratings-1"},
                "justification": "fix",
            },
        ]
    )

    class PlanningLLM:
        def invoke(self, prompt: str) -> str:
            return plan if "Propose a list of remediation actions" in prompt else "[]"

    calls: list[str] = []

    async def caller(_session, tool_name, params):
        calls.append(tool_name)
        return CallToolResult(
            isError=False, content=[TextContent(type="text", text="2 pods running")]
        )

    graph = build_graph(
        tier=2,
        skill_def=loads(SKILL),
        llm=PlanningLLM(),
        mcp_session=object(),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        tool_caller=caller,
    )
    result = await graph.ainvoke(
        {
            "session_id": str(uuid.uuid4()),
            "tier": 2,
            "incident_description": "productpage 500s",
        }
    )

    assert calls == ["get_pods"]
    assert [c["tool_name"] for c in result["tool_calls"] if c["permitted"]] == [
        "get_pods"
    ]
    assert [a["tool_name"] for a in result["blocked_actions"]] == ["restart_pod"]


def test_a_generated_skill_lets_safe_operations_read_at_tier_2():
    content = build_skill_from_tools(
        name="lab",
        operations=[
            {"tool": "get_logs", "classification": "safe"},
            {"tool": "restart_service", "classification": "caution"},
        ],
    )
    skill = loads(content)
    read, write = (
        skill.tier_policy("get_logs", 2),
        skill.tier_policy("restart_service", 2),
    )
    assert read.enabled and read.mode == "autonomous"
    assert not write.enabled and write.mode == "blocked"


def test_an_integration_read_may_run_at_tier_2_and_a_write_may_not():
    def operation(capability):
        return _operation_for(
            IntegrationToolDescriptor(
                name=f"integration__jira__{capability.action}__x",
                description=capability.description,
                connector_id=uuid.uuid4(),
                capability=capability,
            )
        )

    read = operation(IntegrationCapability("get_issue", "Read an issue."))
    write = operation(
        IntegrationCapability(
            "create_issue", "Create an issue.", classification="caution", mutating=True
        )
    )
    assert (
        read.policy_for_tier(2).enabled and read.policy_for_tier(2).mode == "autonomous"
    )
    assert (
        not write.policy_for_tier(2).enabled
        and write.policy_for_tier(2).mode == "advisory"
    )
