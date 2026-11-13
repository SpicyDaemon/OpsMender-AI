"""M1-177: the example plan in the plan prompts names no tool.

Both plan prompts show the model an example of a plan's shape. On main it
was one action of a cluster's tools, ``get_pods`` with a ``namespace``,
whatever tools the session had, so a session whose tools come from a
compute, storage or database server, or from an integration connector, was
shown a tool and an argument it does not have. The example now shows the
shape with each part in angle brackets, as JSON and nothing else. The cases
below check that it is a plan whose tool name and argument names are no
tool's or argument's name, that the prompts of sessions with tools of five
kinds of source hold no tool name and no argument name the session does not
offer, and that a model that copies the example as it is gets nothing called
and nobody asked.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

import pytest
from mcp.types import CallToolResult, TextContent

from backend.agent.graph import build_graph
from backend.agent.nodes import (
    NOT_A_TOOL_NAME_REASON,
    PLAN_EXAMPLE,
    parse_plan,
    plain_tool_name,
    shown_tool_name,
)
from backend.approvals.service import ApprovalResolution
from backend.audit.logger import AuditLogger
from backend.skills.parser import (
    OperationClassification,
    OperationTierPolicy,
    SkillDefinition,
)

READ = {tier: OperationTierPolicy(True, "autonomous") for tier in (0, 1, 2)}
ASKS = {tier: OperationTierPolicy(True, "approval") for tier in (0, 1, 2)}
WRITE = {
    0: OperationTierPolicy(False, "blocked"),
    1: OperationTierPolicy(True, "approval"),
    2: OperationTierPolicy(False, "blocked"),
}
# Tools as five kinds of source name theirs: each with its reads, its
# writes, and the arguments each lists.
SOURCES = {
    "compute": (
        {"describe_instances": ["instance_ids"], "get_console_output": ["instance_id"]},
        {"reboot_instances": ["instance_ids"]},
    ),
    "storage": (
        {"list_volumes": ["region"], "get_volume_usage": ["volume_id"]},
        {"resize_volume": ["volume_id", "size_gib"]},
    ),
    "database": (
        {"db.list-backups": ["database"], "db.get-replication-lag": ["database"]},
        {"db.failover": ["database"]},
    ),
    "connector": (
        {"tickets__issue__search": ["query"], "tickets__issue__get": ["key"]},
        {"tickets__issue__comment": ["key", "body"]},
    ),
    "cluster": (
        {"get_pods": ["namespace"], "get_pod_logs": ["namespace", "pod"]},
        {"restart_deployment": ["namespace", "deployment"]},
    ),
}
TOOL_NAME = re.compile(r'"tool_name":\s*"([^"]*)"')


def _example() -> dict:
    """The example's one action."""
    (action,) = json.loads(PLAN_EXAMPLE)
    return action


def _skill(reads, writes) -> SkillDefinition:
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            *(
                OperationClassification(tool=name, classification="safe", tiers=READ)
                for name in reads
            ),
            *(
                OperationClassification(
                    tool=name, classification="caution", tiers=WRITE
                )
                for name in writes
            ),
        ],
    )


class _Model:
    """A model that answers each plan prompt in turn from ``answers``, the last
    answer repeating, and records the plan prompts."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.plan_prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        if "Return your plan as a JSON array" not in prompt:
            return "noted"
        self.plan_prompts.append(prompt)
        return self.answers[min(len(self.plan_prompts), len(self.answers)) - 1]


class _Approver:
    """An approval service that records whom it was asked about."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def request_and_wait(
        self, *, session_id, action, justification=None, evidence=None
    ):
        self.asked.append(action["tool_name"])
        request = type("Request", (), {})()
        request.id = uuid.uuid4()
        request.status = "approved"
        request.action = action
        request.expires_at = datetime(2030, 1, 1)
        request.resolution_note = None
        return ApprovalResolution(request=request)


async def _run(
    tmp_path, model, skill, names, *, tier: int = 2, descriptions=None, approvals=None
):
    called: list[str] = []

    async def caller(_session, tool_name, params):
        called.append(tool_name)
        return CallToolResult(
            isError=False,
            content=[TextContent(type="text", text="3 items, all healthy")],
        )

    log = tmp_path / "audit.jsonl"
    graph = build_graph(
        tier=tier,
        skill_def=skill,
        llm=model,
        mcp_session=object(),
        audit_logger=AuditLogger(log),
        approval_service=approvals,
        tool_caller=caller,
        plan_tool_names=names,
        plan_tool_descriptions=descriptions,
    )
    result = await graph.ainvoke(
        {
            "session_id": str(uuid.uuid4()),
            "tier": tier,
            "incident_description": "Error rate is 4 percent.",
            "incident": {
                "title": "Error rate",
                "description": "4 percent",
                "status": "open",
            },
            "operator_guidance": [],
            "redirect_requested": False,
            "redirect_count": 0,
        },
        {"recursion_limit": 60},
    )
    # A session that called and refused nothing writes no audit entry.
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return result, called, rows


# ---------------------------------------------------------------------------
# The example itself
# ---------------------------------------------------------------------------


def test_the_example_is_a_plan_of_placeholders_and_nothing_else():
    # JSON and nothing else: no text a model might repeat after its plan.
    action = _example()
    assert set(action) == {"tool_name", "tool_parameters", "justification"}
    # Its tool name and argument names are no tool's or argument's name: each
    # holds white space, so a copy is refused before it is matched (M1-175).
    names = [action["tool_name"], *action["tool_parameters"]]
    assert len(names) == 2
    assert not any(plain_tool_name(name) for name in names)
    # Every part is in angle brackets.
    parts = [*names, *action["tool_parameters"].values(), action["justification"]]
    assert all(part.startswith("<") and part.endswith(">") for part in parts)
    # The tool placeholder points at the tool list, which both prompts head
    # "Available tools".
    assert "available tools" in action["tool_name"]


def test_the_example_holds_no_word_of_one_kind_of_tool_source():
    lowered = PLAN_EXAMPLE.lower()
    for word in ("pod", "namespace", "deployment", "kubectl", "cluster", "default"):
        assert word not in lowered, word


def test_a_verbatim_copy_is_read_as_a_plan_whose_name_is_no_tools_name():
    # A model that answers with the example as it is gives a readable plan,
    # whose tool name the record keeps as the gate's refusal will show it.
    (action,) = parse_plan(PLAN_EXAMPLE)
    assert action["tool_name"] == shown_tool_name(_example()["tool_name"])
    assert not plain_tool_name(action["tool_name"])


# ---------------------------------------------------------------------------
# The prompts of sessions with tools of five kinds of source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(SOURCES))
async def test_the_prompts_hold_no_tool_or_argument_the_session_lacks(kind, tmp_path):
    """The reproduction. On main both plan prompts of a session whose tools
    are not a cluster's held ``get_pods`` and ``namespace``."""
    reads, writes = SOURCES[kind]
    offered = {**reads, **writes}
    first = sorted(reads)[0]
    read = {
        "tool_name": first,
        "tool_parameters": {name: "x" for name in reads[first]},
        "justification": "read first",
    }
    model = _Model(json.dumps([read]), "[]")
    descriptions = {
        name: "Reads or changes a resource. Arguments: "
        + ", ".join(f"{argument} (string, required)" for argument in arguments)
        + "."
        for name, arguments in offered.items()
    }
    _, called, _ = await _run(
        tmp_path,
        model,
        _skill(reads, writes),
        sorted(offered),
        descriptions=descriptions,
    )
    assert called == [first]
    assert len(model.plan_prompts) == 2, "a first plan and a later one"
    # The example's argument names are none a tool lists.
    listed = {argument for arguments in offered.values() for argument in arguments}
    assert not set(_example()["tool_parameters"]) & listed
    for prompt in model.plan_prompts:
        assert f"Example:\n{PLAN_EXAMPLE}\n\n" in prompt
        # Every tool name the prompt writes as a plan's is one the session
        # offers, or is no tool's name at all.
        for name in TOOL_NAME.findall(prompt):
            assert name in offered or not plain_tool_name(name), name
        if kind != "cluster":
            assert "get_pods" not in prompt
            assert "namespace" not in prompt


# ---------------------------------------------------------------------------
# A model that copies the example
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", (0, 1, 2, 3))
async def test_a_copied_example_gets_nothing_called(tier, tmp_path):
    """Under a Skill whose pattern allows every name, main's example name
    would have been called if a model copied it. The example's name is
    refused as not a tool's name before it is matched, and the refusal is
    in the audit log."""
    skill = SkillDefinition(
        version="1",
        environment="test",
        operations=[
            OperationClassification(tool="*", classification="safe", tiers=READ)
        ],
    )
    model = _Model(PLAN_EXAMPLE)
    result, called, rows = await _run(
        tmp_path, model, skill, ["list_volumes"], tier=tier
    )
    assert called == []
    assert not result.get("plan_unreadable")
    (blocked,) = result["blocked_actions"]
    assert blocked["block_reason"] == NOT_A_TOOL_NAME_REASON
    (row,) = [r for r in rows if r["entry_type"] == "tool_call_blocked"]
    assert row["block_reason"] == NOT_A_TOOL_NAME_REASON


async def test_at_tier_1_nobody_is_asked_for_a_copied_example(tmp_path):
    # A Skill under which every name waits for an operator's approval: the
    # copied example is refused before anybody is asked.
    skill = SkillDefinition(
        version="1",
        environment="test",
        operations=[
            OperationClassification(tool="*", classification="caution", tiers=ASKS)
        ],
    )
    approvals = _Approver()
    model = _Model(PLAN_EXAMPLE)
    result, called, _ = await _run(
        tmp_path, model, skill, ["list_volumes"], tier=1, approvals=approvals
    )
    assert approvals.asked == [] and called == []
    assert [b["block_reason"] for b in result["blocked_actions"]] == [
        NOT_A_TOOL_NAME_REASON
    ]


async def test_a_session_without_a_tool_is_shown_an_empty_plan(tmp_path):
    model = _Model("[]")
    await _run(tmp_path, model, _skill({}, {}), [])
    (prompt,) = model.plan_prompts
    assert "Example:\n[]\n\n" in prompt
    assert _example()["tool_name"] not in prompt
