"""M1-92: a plan inside a code fence is parsed, and one that cannot be read is recorded.

In the 2026-10-24 live round a local model answered the plan prompt with its
JSON array inside a Markdown code fence. The plan step passed the text to
``json.loads``, which failed, and the plan silently became empty: the Tier 1
session completed with no tool call although the model had planned eight reads.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent

from backend.agent.graph import build_graph
from backend.agent.grounding import UNREADABLE_PLAN, facts_block, facts_from_state
from backend.agent.nodes import _build_plan, _build_plan_with_tool_names, parse_plan
from backend.api import session_runner
from backend.audit.logger import AuditLogger
from backend.db.repos import IncidentRepo, SessionMessageRepo, SessionRepo
from backend.skills.parser import loads
from tests.test_tier2_reads import SKILL
from tests.test_tool_source_overlap import TEST_ORG_ID, app  # noqa: F401

READ = {"tool_name": "get_pods", "tool_parameters": {"namespace": "shop"}}
ARRAY = json.dumps([READ])


@pytest.mark.parametrize(
    "answer",
    [
        f"```json\n{ARRAY}\n```",
        f"```\n{ARRAY}\n```",
        f"Here is the plan:\n```json\n{ARRAY}\n```\nI will wait for the results.",
        f"Here is the plan: {ARRAY}",
        f"{ARRAY}\nThese reads are safe at this tier.",
        f"Plan [JSON]: {ARRAY}",
        ARRAY,
    ],
)
def test_a_plan_in_a_fence_or_in_prose_is_read(answer):
    assert parse_plan(answer) == [READ]


@pytest.mark.parametrize("answer", ["[]", "```json\n[]\n```", "No action: []"])
def test_an_empty_array_is_a_readable_empty_plan(answer):
    assert parse_plan(answer) == []


@pytest.mark.parametrize(
    "answer",
    [
        "No actions are needed.",
        "",
        None,
        '[{"tool_name": "get_pods", "tool_parameters": {"ids": []}',
        '[{"tool_name": "get_pods", "tool_parameters": {"ids": [1, 2]}',
        "[1, 2, 3]",
    ],
)
def test_an_answer_without_a_plan_cannot_be_read(answer):
    assert parse_plan(answer) is None


class _Answers:
    """A model that answers the plan prompt with ``plan`` and records prompts."""

    def __init__(self, plan: str):
        self.plan, self.prompts = plan, []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Propose a list of remediation actions" in prompt:
            return self.plan
        return "[]"


@pytest.mark.parametrize("named_tools", [False, True])
def test_both_plan_nodes_read_a_fenced_plan_and_flag_an_unreadable_one(named_tools):
    def node(answer):
        llm = _Answers(answer)
        if named_tools:
            return _build_plan_with_tool_names(llm, 2, loads(SKILL), ["get_pods"])
        return _build_plan(llm, 2, loads(SKILL))

    fenced = node(f"```json\n{ARRAY}\n```")({"diagnosis": "d"})
    assert fenced == {"plan": [READ], "plan_unreadable": False}
    unreadable = node("I would restart the ratings pods.")({"diagnosis": "d"})
    assert unreadable == {"plan": [], "plan_unreadable": True}


async def test_a_session_runs_the_reads_of_a_fenced_plan(tmp_path):
    calls: list[str] = []

    async def caller(_session, tool_name, params):
        calls.append(tool_name)
        return CallToolResult(
            isError=False, content=[TextContent(type="text", text="2 pods running")]
        )

    graph = build_graph(
        tier=2,
        skill_def=loads(SKILL),
        llm=_Answers(f"```json\n{ARRAY}\n```"),
        mcp_session=object(),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        tool_caller=caller,
    )
    result = await graph.ainvoke(
        {"session_id": str(uuid.uuid4()), "tier": 2, "incident_description": "500s"}
    )
    assert calls == ["get_pods"]
    assert result["plan_unreadable"] is False


async def test_an_unreadable_plan_reaches_the_summary_facts(tmp_path):
    llm = _Answers("I would restart the ratings pods.")
    graph = build_graph(
        tier=2,
        skill_def=loads(SKILL),
        llm=llm,
        mcp_session=object(),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
        tool_caller=None,
    )
    result = await graph.ainvoke(
        {"session_id": str(uuid.uuid4()), "tier": 2, "incident_description": "500s"}
    )
    assert result["plan"] == [] and result["plan_unreadable"] is True
    assert UNREADABLE_PLAN in facts_block(facts_from_state(result))
    summary_prompts = [p for p in llm.prompts if "writing an incident summary" in p]
    assert summary_prompts and UNREADABLE_PLAN in summary_prompts[-1]


def test_a_readable_plan_adds_nothing_to_the_facts():
    assert UNREADABLE_PLAN not in facts_block(facts_from_state({"plan": []}))


@pytest.mark.parametrize("unreadable", [True, False])
async def test_the_runner_records_an_unreadable_plan_in_the_transcript(
    app,  # noqa: F811
    monkeypatch,
    unreadable,
):
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="Checkout 500s", description="d"
        )
        session = await SessionRepo.create(
            db, TEST_ORG_ID, tier=2, incident_id=incident.id
        )
        await db.commit()
        session_id = session.id

    async def _no_llm(factory, session):
        return SimpleNamespace()

    class _FakeGraph:
        async def ainvoke(self, state, *args, **kwargs):
            return {
                "status": "completed",
                "summary": "ok",
                "plan": [],
                "plan_unreadable": unreadable,
            }

    monkeypatch.setattr(session_runner, "_resolve_llm", _no_llm)
    monkeypatch.setattr(session_runner, "build_graph", lambda **kwargs: _FakeGraph())
    app.state.workflow_start_delay_seconds = 0

    await session_runner._run_session_workflow_inner(app, session_id=session_id)

    async with app.state.session_factory() as db:
        messages = await SessionMessageRepo.list_by_session(db, TEST_ORG_ID, session_id)
        stored = await SessionRepo.get_by_id(db, TEST_ORG_ID, session_id)
    assert stored.status == "completed", stored.status
    notes = [m for m in messages if m.node_context == "plan"]
    if unreadable:
        assert [m.content for m in notes] == [UNREADABLE_PLAN]
        assert notes[0].role == "assistant"
    else:
        assert notes == []
