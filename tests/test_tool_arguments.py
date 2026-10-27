"""The agent sees each tool's arguments, and a call the server rejects is recorded as failed (M1-91).

In the 2026-10-24 live round 62 of 127 tool calls were rejected because the
plan prompt never showed the tools' arguments and the model guessed their
names; a rejected call (`isError: true`) was then recorded as executed, so the
facts line counted it and the summarize prompt listed it as "ok".
"""

from __future__ import annotations

from mcp.types import CallToolResult, TextContent, Tool

from backend.agent.grounding import (
    SessionFacts,
    contradictions,
    facts_block,
    state_line,
)
from backend.agent.llm import StubLLM
from backend.agent.nodes import _build_plan_with_tool_names
from backend.agent.workflow_executor import WorkflowExecutor
from backend.audit.executor import audited_tool_call
from backend.audit.logger import AuditLogger
from backend.mcp.client import tool_plan_description
from backend.skills.parser import SkillDefinition
from tests.skill_policy_helpers import explicit_operation
from tests.test_workflow_executor import SESSION_ID, _skill

GET_LOGS = Tool(
    name="get_logs",
    description="Read a service's recent log lines.",
    inputSchema={
        "type": "object",
        "properties": {"service": {"type": "string"}, "lines": {"type": "integer"}},
        "required": ["service"],
    },
)


def test_the_plan_description_lists_the_arguments():
    assert tool_plan_description(GET_LOGS) == (
        "Read a service's recent log lines. "
        "Arguments: service (string, required), lines (integer)."
    )
    bare = Tool(name="list_services", description=None, inputSchema={"type": "object"})
    assert tool_plan_description(bare) == "Arguments: none."


def test_the_plan_prompt_shows_the_arguments_and_asks_for_those_names():
    llm = StubLLM(response="[]")
    skill = SkillDefinition(
        version="1",
        environment="t",
        operations=[explicit_operation("get_logs", "safe")],
    )
    plan = _build_plan_with_tool_names(
        llm,
        1,
        skill,
        ["get_logs"],
        tool_descriptions={"get_logs": tool_plan_description(GET_LOGS)},
    )
    plan({"diagnosis": "productpage returns 500s"})
    prompt = llm.calls[-1]
    assert (
        "- get_logs (safe, waits for operator approval): Read a service's recent log "
        "lines. Arguments: service (string, required)" in prompt
    )
    assert "pass only the argument names" in prompt


async def test_a_call_the_server_rejects_is_an_error(tmp_path):
    async def caller(_session, tool_name, params):
        return CallToolResult(
            isError=True,
            content=[
                TextContent(type="text", text="missing required argument: service")
            ],
        )

    skill = SkillDefinition(
        version="1",
        environment="t",
        operations=[explicit_operation("get_logs", "safe")],
    )
    result = await audited_tool_call(
        session=object(),
        tool_name="get_logs",
        tool_parameters={"service_name": "ratings"},
        session_id=SESSION_ID,
        tier=1,
        skill_def=skill,
        logger=AuditLogger(tmp_path / "audit.jsonl"),
        tool_caller=caller,
    )
    assert result.permitted is True
    assert (
        result.error == "the tool reported an error: missing required argument: service"
    )
    assert result.result["isError"] is True


def test_the_facts_count_a_rejected_call_as_failed_not_executed():
    facts = SessionFacts(
        incident_status="open",
        owner_name="admin",
        tool_calls=(
            {
                "tool_name": "get_logs",
                "permitted": True,
                "error": "the tool reported an error: no service",
            },
            {"tool_name": "get_metrics", "permitted": True, "error": None},
        ),
        blocked_count=0,
        approval_count=0,
    )
    assert "Actions executed in this session: 1; failed: 1; blocked: 0;" in state_line(
        facts
    )
    block = facts_block(facts)
    assert (
        "- get_logs: failed, no result: the tool reported an error: no service" in block
    )
    assert "- get_metrics: ok" in block


def test_a_summary_claiming_checks_when_every_call_failed_is_corrected():
    facts = SessionFacts(
        incident_status="open",
        owner_name="admin",
        tool_calls=({"tool_name": "get_logs", "permitted": True, "error": "rejected"},),
        blocked_count=0,
        approval_count=0,
    )
    found = contradictions("We checked the logs of every service.", facts)
    assert found == ["every tool call in this session failed; none returned a result"]


async def test_a_workflow_step_whose_tool_reports_an_error_fails(tmp_path):
    calls: list[str] = []

    async def caller(_session, tool_name, params):
        calls.append(tool_name)
        return CallToolResult(
            isError=True, content=[TextContent(type="text", text="pod lookup refused")]
        )

    executor = WorkflowExecutor(
        mcp_session=object(),
        skill_def=_skill(first_failure="abort"),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        tier=0,
        tool_caller=caller,
    )
    result = await executor.execute(
        session_id=SESSION_ID, incident={"id": "inc-1", "title": "API down"}
    )
    assert calls == ["find_pod"]
    assert result.aborted is True and result.abort_step_id == "find"
    assert [item.status for item in result.outcomes] == ["failed"]
    assert "pod lookup refused" in result.outcomes[0].error
