"""M1-176: a planned tool is one the session was offered.

Four things the model writes into a plan reached further than the plan:

- A made-up tool name of one plain word, such as
  "get_x:ok,-resize_volume:ok,-get_y", keeps the form M1-175 checks, so a
  pattern operation of the Skill (``get_*``) matched it: the graph handed it
  to the tool caller, or at Tier 1 put it to an operator. Where the session
  has the server's tool list, the gate now refuses a name that is not in
  it, before any matching, whatever kind of source the tools come from.
- A justification with a line break added a line of the model's to a
  postmortem draft. The text fields the record prints are one line now.
- The incident timeline worked a refused call's class out again from the
  Skill, so a ``*`` pattern showed its class beside a refusal the gate had
  recorded as of no class. The audit entry of a refused call keeps the
  class the check gave it now (the timeline case is in tests/test_api.py).
- A NUL character, a lone surrogate or NaN in a plan value failed the
  session when PostgreSQL was given it (tests/test_plan_values_postgres.py).
  The plan's values are storable from where the plan is read.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent, Tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.graph import build_graph
from backend.agent.nodes import (
    ACTION_TEXT_CHARS,
    NO_EXECUTOR_REASON,
    NOT_A_TOOL_NAME_REASON,
    NOT_OFFERED_REASON,
    _storable,
    one_line,
    parse_plan,
    plain_tool_name,
    shown_tool_name,
)
from backend.api.session_runner import LiveAuditLogger
from backend.approvals.service import ApprovalResolution
from backend.audit.logger import AuditLogger, blocked_result
from backend.audit.pg_logger import PgAuditLogger
from backend.db.models import AuditEntry, Base, Organization
from backend.db.repos import IncidentRepo, SessionRepo
from backend.mcp.client import MAX_TOOL_PAGES, list_tools
from backend.services.postmortem_draft import draft_postmortem
from backend.skills.parser import (
    OperationClassification,
    OperationTierPolicy,
    SkillDefinition,
)

READ = {tier: OperationTierPolicy(True, "autonomous") for tier in (0, 1, 2, 3)}
ASKS = {tier: OperationTierPolicy(True, "approval") for tier in (0, 1, 2, 3)}
WRITE = {
    0: OperationTierPolicy(False, "blocked"),
    1: OperationTierPolicy(True, "approval"),
    2: OperationTierPolicy(False, "blocked"),
    3: OperationTierPolicy(True, "autonomous"),
}
# Tool sources of five kinds: the server's tool list, a read pattern of the
# Skill, and a made-up name of one word that the pattern matches.
SOURCES = {
    "compute": (
        ["describe_instances", "get_console_output", "reboot_instances"],
        "describe_*",
        "describe_x:ok,-reboot_instances:ok,-describe_y",
    ),
    "storage": (
        ["get_volume_usage", "list_volumes", "resize_volume"],
        "get_*",
        "get_x:ok,-resize_volume:ok,-get_y",
    ),
    "database": (
        ["db.failover", "db.get-replication-lag", "db.list-backups"],
        "db.get-*",
        "db.get-x:ok,-db.failover:ok,-db.get-y",
    ),
    "connector": (
        ["tickets__issue__comment", "tickets__issue__get", "tickets__issue__search"],
        "tickets__issue__get*",
        "tickets__issue__getx:ok,-tickets__issue__comment:ok",
    ),
    "cluster": (
        ["get_pod_logs", "get_pods", "restart_deployment"],
        "get_*",
        "get_x:ok,-restart_deployment:ok,-get_y",
    ),
}
STORAGE = SOURCES["storage"][0]
# Names that keep the form M1-175 checks (no white space, every character
# prints) and read as another name or as punctuation.
LOOKALIKES = {
    "hangul filler": "get_volume_usage" + chr(0x3164),
    "fullwidth colon": "get_x" + chr(0xFF1A) + "ok",
    "variation selector": "get_y" + chr(0xFE0F),
}
BACKSLASH = chr(92)


def _skill(*operations: tuple[str, str, dict]) -> SkillDefinition:
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            OperationClassification(tool=tool, classification=cls, tiers=tiers)
            for tool, cls, tiers in operations
        ],
    )


def _action(name: str, **fields) -> dict:
    return {"tool_name": name, "tool_parameters": {}, "justification": "x", **fields}


class _Model:
    """Answers each plan prompt in turn from ``answers``; an empty plan once
    they run out."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.plans = 0

    def invoke(self, prompt: str) -> str:
        if "Return your plan as a JSON array" not in prompt:
            return "noted"
        self.plans += 1
        return self.answers[self.plans - 1] if self.plans <= len(self.answers) else "[]"


class _Approver:
    """An approval service that approves and records whom it was asked about."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def request_and_wait(
        self, *, session_id, action, justification=None, evidence=None
    ):
        self.asked.append(action["tool_name"])
        request = SimpleNamespace(
            id=uuid.uuid4(),
            status="approved",
            action=action,
            expires_at=datetime(2030, 1, 1),
            resolution_note=None,
        )
        return ApprovalResolution(request=request)


async def _run(
    tmp_path,
    model,
    skill,
    names,
    *,
    tier: int = 2,
    approvals=None,
    executor: bool = True,
):
    called: list[str] = []

    async def caller(_session, tool_name, params):
        called.append(tool_name)
        return CallToolResult(
            isError=False,
            content=[TextContent(type="text", text="3 items, all healthy")],
        )

    log = tmp_path / f"audit-{uuid.uuid4().hex}.jsonl"
    graph = build_graph(
        tier=tier,
        skill_def=skill,
        llm=model,
        mcp_session=object() if executor else None,
        audit_logger=AuditLogger(log),
        approval_service=approvals,
        tool_caller=caller,
        plan_tool_names=names,
    )
    result = await graph.ainvoke(
        {
            "session_id": str(uuid.uuid4()),
            "tier": tier,
            "incident_description": "Disk usage on the reports volume is 97 percent.",
            "incident": {"title": "Disk usage", "description": "97", "status": "open"},
            "operator_guidance": [],
            "redirect_requested": False,
            "redirect_count": 0,
        },
        {"recursion_limit": 60},
    )
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return (
        result,
        called,
        [row for row in rows if row["entry_type"] == "tool_call_blocked"],
    )


def _refusals(result) -> list[tuple]:
    return [
        (b["tool_name"], b["block_reason"], b["classification"])
        for b in result["blocked_actions"]
    ]


# ---------------------------------------------------------------------------
# The gate: a planned name the session was not offered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", (0, 1, 2, 3))
@pytest.mark.parametrize("kind", list(SOURCES))
async def test_a_made_up_name_is_refused_whatever_the_tool_source(kind, tier, tmp_path):
    """The reproduction. On main the graph handed the made-up name to the
    tool caller under the read pattern, at every tier and for every kind of
    source; this stub caller takes any name, where in the product a
    connector answers that it has no such tool and the Tier 0 sandbox
    refuses a name off its allowlist. The offered read beside it still
    runs."""
    names, pattern, made_up = SOURCES[kind]
    assert plain_tool_name(made_up) and made_up not in names
    offered = next(name for name in names if name.startswith(pattern.rstrip("*")))
    model = _Model(json.dumps([_action(offered), _action(made_up)]))
    result, called, rows = await _run(
        tmp_path, model, _skill((pattern, "safe", READ)), names, tier=tier
    )
    assert called == [offered]
    assert _refusals(result) == [(made_up, NOT_OFFERED_REASON, "unknown")]
    (row,) = rows
    assert (row["tool_name"], row["block_reason"]) == (made_up, NOT_OFFERED_REASON)
    assert row["result"] == {"classification": "unknown"}


@pytest.mark.parametrize("label", list(LOOKALIKES))
async def test_a_name_that_reads_as_another_is_refused(label, tmp_path):
    """These names pass the form rule of M1-175; the list refuses them."""
    name = LOOKALIKES[label]
    assert plain_tool_name(name)
    model = _Model(json.dumps([_action(name)]))
    result, called, _ = await _run(
        tmp_path, model, _skill(("get_*", "safe", READ)), STORAGE
    )
    assert called == []
    assert _refusals(result) == [(name, NOT_OFFERED_REASON, "unknown")]


async def test_nobody_is_asked_about_a_name_the_session_was_not_offered(tmp_path):
    """On main a pattern of the Skill that asks put the made-up name to an
    operator, and an approval had it called."""
    skill = _skill(("get_*", "safe", READ), ("resize_*", "caution", ASKS))
    made_up = "resize_x:ok,-delete_volume:ok"
    approvals = _Approver()
    result, called, rows = await _run(
        tmp_path,
        _Model(json.dumps([_action(made_up)])),
        skill,
        STORAGE,
        tier=1,
        approvals=approvals,
    )
    assert approvals.asked == [] and called == []
    assert _refusals(result) == [(made_up, NOT_OFFERED_REASON, "unknown")]
    assert [row["block_reason"] for row in rows] == [NOT_OFFERED_REASON]

    # A tool of the list under the same pattern is asked about as before.
    approvals = _Approver()
    _, called, _ = await _run(
        tmp_path,
        _Model(json.dumps([_action("resize_volume")])),
        skill,
        STORAGE,
        tier=1,
        approvals=approvals,
    )
    assert approvals.asked == ["resize_volume"] and called == ["resize_volume"]


async def test_a_later_round_refuses_a_name_the_session_was_not_offered(tmp_path):
    """A later round blocks a planned name that is not a read itself, with the
    class and reason of its check: the list's, whatever the Skill's pattern
    would make of the name (here a read and a write)."""
    made_up = SOURCES["storage"][2]
    writes = "resize_x:ok,-delete_volume:ok"
    model = _Model(
        json.dumps([_action("get_volume_usage")]),
        json.dumps([_action(made_up), _action(writes)]),
    )
    skill = _skill(("get_*", "safe", READ), ("resize_*", "caution", WRITE))
    result, called, rows = await _run(tmp_path, model, skill, STORAGE)
    assert model.plans == 2
    assert called == ["get_volume_usage"]
    assert sorted(_refusals(result)) == [
        (made_up, NOT_OFFERED_REASON, "unknown"),
        (writes, NOT_OFFERED_REASON, "unknown"),
    ]
    assert sorted(row["tool_name"] for row in rows) == [made_up, writes]


async def test_a_later_round_at_tier_1_asks_nobody_about_such_a_name(tmp_path):
    approvals = _Approver()
    made_up = "resize_x:ok,-delete_volume:ok"
    model = _Model(
        json.dumps([_action("get_volume_usage")]), json.dumps([_action(made_up)])
    )
    skill = _skill(("get_*", "safe", READ), ("resize_*", "caution", ASKS))
    result, called, _ = await _run(
        tmp_path, model, skill, STORAGE, tier=1, approvals=approvals
    )
    assert model.plans == 2
    assert approvals.asked == [] and called == ["get_volume_usage"]
    assert _refusals(result) == [(made_up, NOT_OFFERED_REASON, "unknown")]


async def test_at_tier_0_a_tool_off_the_list_is_not_offered(tmp_path):
    """At Tier 0 the session runner and the CLI give the gate the sandbox's
    allowlist (tests/test_tool_source_overlap.py checks the runner's); here
    a list stands in for it. A tool the server lists outside it is refused
    as not offered, where main refused it by the Tier 0 policy with the
    Skill's class; nothing runs either way."""
    skill = _skill(("get_*", "safe", READ), ("resize_volume", "destructive", WRITE))
    model = _Model(json.dumps([_action("get_volume_usage"), _action("resize_volume")]))
    allowlist = ["get_volume_usage", "list_volumes"]
    result, called, _ = await _run(tmp_path, model, skill, allowlist, tier=0)
    assert called == ["get_volume_usage"]
    assert _refusals(result) == [("resize_volume", NOT_OFFERED_REASON, "unknown")]


async def test_a_session_that_cannot_run_names_the_reason(tmp_path):
    """With nothing to call, a tool of the list is blocked because nothing
    runs, and a name not in it for that."""
    made_up = SOURCES["storage"][2]
    model = _Model(json.dumps([_action("get_volume_usage"), _action(made_up)]))
    result, called, _ = await _run(
        tmp_path, model, _skill(("get_*", "safe", READ)), STORAGE, executor=False
    )
    assert called == []
    assert [(b["tool_name"], b["block_reason"]) for b in result["blocked_actions"]] == [
        ("get_volume_usage", NO_EXECUTOR_REASON),
        (made_up, NOT_OFFERED_REASON),
    ]


async def test_a_graph_without_a_tool_list_keeps_the_skills_matching(tmp_path):
    """A graph built without a list (a session without a tool source has no
    calls to make) is held to the Skill alone, as before."""
    made_up = SOURCES["storage"][2]
    model = _Model(json.dumps([_action(made_up)]))
    _, called, _ = await _run(tmp_path, model, _skill(("get_*", "safe", READ)), None)
    assert called == [made_up]


async def test_a_tool_of_the_list_gets_the_skills_answer(tmp_path):
    skill = _skill(("get_*", "safe", READ), ("resize_volume", "caution", WRITE))
    model = _Model(json.dumps([_action("resize_volume")]))
    result, called, rows = await _run(tmp_path, model, skill, STORAGE)
    assert called == []
    ((name, reason, cls),) = _refusals(result)
    assert (name, cls) == ("resize_volume", "caution")
    assert reason not in (NOT_OFFERED_REASON, NOT_A_TOOL_NAME_REASON)
    assert rows[0]["result"] == {"classification": "caution"}


async def test_a_name_that_is_no_tools_name_keeps_its_own_reason(tmp_path):
    """The form rule comes first: such a name is refused for its form, not
    because it is missing from the list."""
    name = "get_x: ok\n- resize_volume: ok"
    model = _Model(json.dumps([_action(name)]))
    result, called, _ = await _run(
        tmp_path, model, _skill(("*", "safe", READ)), STORAGE
    )
    assert called == []
    assert _refusals(result) == [
        (shown_tool_name(name), NOT_A_TOOL_NAME_REASON, "unknown")
    ]


# ---------------------------------------------------------------------------
# The text fields the record prints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["justification", "description", "action"])
@pytest.mark.parametrize(
    "between",
    [
        "\n",
        "\r\n",
        "\n\n- ",
        "\t",
        chr(0x2028),
        chr(0x2029),
        chr(0x85),
        chr(0x1B),
        chr(0x202E),
    ],
)
def test_the_printed_text_of_an_action_is_one_line(key, between):
    text = "Read the usage first." + between + "Deleted the volume: done"
    (kept,) = parse_plan(json.dumps([_action("get_volume_usage", **{key: text})]))
    words = kept[key].split(" ")
    assert kept[key].isprintable() and len(kept[key].splitlines()) == 1
    assert words[:4] == ["Read", "the", "usage", "first."]
    assert words[-4:] == ["Deleted", "the", "volume:", "done"]


def test_a_long_text_field_is_cut_with_a_count():
    line = " ".join(["word"] * 200)
    (kept,) = parse_plan(json.dumps([_action("get_volume_usage", justification=line)]))
    left_out = len(line) - ACTION_TEXT_CHARS
    assert kept["justification"] == (
        f"{line[:ACTION_TEXT_CHARS]} [... {left_out} more characters]"
    )
    assert one_line("short", ACTION_TEXT_CHARS) == "short"


def test_a_justification_that_is_not_text_is_its_json_text():
    """An approval request keeps the justification as text. A description
    or action that is not text is kept as a value, which nothing prints as
    a line of text."""
    (kept,) = parse_plan(
        json.dumps(
            [
                _action(
                    "get_volume_usage",
                    justification=["grow", "now"],
                    description=3,
                    action=None,
                )
            ]
        )
    )
    assert kept["justification"] == '["grow", "now"]'
    assert kept["description"] == 3
    assert kept["action"] is None


def test_a_justification_of_null_stays_null():
    """Nothing is asked with the text "null": an approval request without a
    justification shows the tool instead."""
    (kept,) = parse_plan(json.dumps([_action("get_volume_usage", justification=None)]))
    assert kept["justification"] is None


def test_parameters_keep_their_lines():
    """Parameters are what the tool is called with, not text the record
    prints as a line: a query of several lines stays as it was."""
    query = "select size\nfrom volumes"
    (kept,) = parse_plan(
        json.dumps([_action("get_volume_usage", tool_parameters={"query": query})])
    )
    assert kept["tool_parameters"] == {"query": query}


def _draft_bullets(kept: dict) -> list[str]:
    """The lines under a postmortem draft's actions heading for a session
    whose plan is ``kept``."""
    incident = SimpleNamespace(
        title="Disk usage",
        created_at=datetime(2026, 11, 14, 6, 0, tzinfo=timezone.utc),
        resolved_at=None,
        priority="P2",
        status="open",
        severity=None,
        description="97 percent",
    )
    session = SimpleNamespace(
        started_at=datetime(2026, 11, 14, 6, 1, tzinfo=timezone.utc),
        summary="Read the usage.",
        progress={"plan": [kept], "diagnosis": "The volume is full."},
    )
    lines = draft_postmortem(incident, [session]).splitlines()
    head = next(i for i, line in enumerate(lines) if "Proposed/taken actions" in line)
    bullets = []
    for line in lines[head + 1 :]:
        if line.startswith("#"):
            break
        if line.strip():
            bullets.append(line)
    return bullets


def test_a_postmortem_draft_gets_one_line_per_action():
    """The reproduction. On main the justification's second line stood in
    the draft as an action of its own."""
    text = "Read the usage first.\n- Deleted the reports volume: done"
    (kept,) = parse_plan(json.dumps([_action("get_volume_usage", justification=text)]))
    assert _draft_bullets(kept) == [
        "- Read the usage first. - Deleted the reports volume: done (`get_volume_usage`)"
    ]


def test_a_description_that_is_not_text_leaves_the_draft_to_the_justification():
    (kept,) = parse_plan(
        json.dumps(
            [_action("get_volume_usage", description=[], justification="Read it.")]
        )
    )
    assert _draft_bullets(kept) == ["- Read it. (`get_volume_usage`)"]


# ---------------------------------------------------------------------------
# Values PostgreSQL refuses
# ---------------------------------------------------------------------------


def test_storable_replaces_a_lone_surrogate_and_drops_nul():
    value = {
        "k" + chr(0): ["a" + chr(0xD800) + "b", float("nan"), {"x" + chr(0xDFFF): 1}],
        "smile": "ok" + chr(0x1F600),
    }
    kept = _storable(value)
    assert kept == {
        "k": ["a" + chr(0xFFFD) + "b", "nan", {"x" + chr(0xFFFD): 1}],
        "smile": "ok" + chr(0x1F600),
    }
    json.dumps(kept, ensure_ascii=False, allow_nan=False).encode("utf-8")


def test_plan_values_are_storable_from_where_the_plan_is_read():
    """A JSON text holds a lone surrogate or a NUL as an escape, and NaN as a
    bare word: the parser keeps none of them for the record or the call."""
    answer = (
        '[{"tool_name": "get_volume_usage", "tool_parameters": {"volume_id": "a'
        + BACKSLASH
        + 'ud800", "count": NaN, "k'
        + BACKSLASH
        + 'u0000": "b'
        + BACKSLASH
        + 'u0000"}, "justification": "x'
        + BACKSLASH
        + 'udfff"}]'
    )
    (kept,) = parse_plan(answer)
    assert kept["tool_parameters"] == {
        "volume_id": "a" + chr(0xFFFD),
        "count": "nan",
        "k": "b",
    }
    assert kept["justification"] == "x" + chr(0xFFFD)
    json.dumps(kept, ensure_ascii=False, allow_nan=False).encode("utf-8")


DEEP = "[" * 1500 + "]" * 1500


def test_a_plan_nested_too_deep_to_clean_is_read_as_no_plan():
    """The decoder reads a value nested this deep; cleaning it would go
    past the interpreter's recursion limit."""
    answer = (
        '[{"tool_name": "get_volume_usage", "tool_parameters": {"v": ' + DEEP + "}}]"
    )
    assert json.loads(answer)
    assert parse_plan(answer) is None


async def test_a_session_whose_plan_nests_too_deep_goes_on(tmp_path):
    answer = (
        '[{"tool_name": "get_volume_usage", "tool_parameters": {"v": ' + DEEP + "}}]"
    )
    result, called, _ = await _run(
        tmp_path, _Model(answer), _skill(("get_*", "safe", READ)), STORAGE
    )
    assert result["plan_unreadable"] and called == []


@pytest.mark.parametrize("hidden", [BACKSLASH + "u0000", BACKSLASH + "ud800"])
def test_a_tool_name_is_checked_before_its_values_are_cleaned(hidden):
    """Taking a NUL out of a name would make it a tool's name: the name's
    form is checked as the model wrote it, and the record keeps its escape."""
    answer = '[{"tool_name": "get_volume' + hidden + '_usage", "tool_parameters": {}}]'
    (kept,) = parse_plan(answer)
    written = json.loads(answer)[0]["tool_name"]
    assert kept["tool_name"] == shown_tool_name(written)
    assert kept["tool_name"].isascii() and not plain_tool_name(kept["tool_name"])


# ---------------------------------------------------------------------------
# The class the audit entry of a refused call keeps
# ---------------------------------------------------------------------------


def test_a_file_audit_entry_keeps_the_class_of_a_refused_call(tmp_path):
    log = AuditLogger(tmp_path / "audit.jsonl")
    log.log_tool_call_blocked("s", 2, "get_x", classification="unknown")
    log.log_tool_call_blocked("s", 2, "get_y")
    rows = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert [row["result"] for row in rows] == [{"classification": "unknown"}, None]
    assert blocked_result(None) is None


async def test_a_database_audit_entry_keeps_the_class_of_a_refused_call():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:8]}"))
        incident = await IncidentRepo.create(db, org_id, title="Disk", description="97")
        session = await SessionRepo.create(db, org_id, tier=2, incident_id=incident.id)
        await db.commit()

    async def publish(_session_id, _message):
        return None

    live = LiveAuditLogger(
        factory, org_id=org_id, session_id=session.id, publisher=publish
    )
    await live.log_tool_call_blocked(
        str(session.id), 2, "get_x", block_reason="r", classification="unknown"
    )
    async with factory() as db:
        await PgAuditLogger(db, org_id).log_tool_call_blocked(
            str(session.id), 2, "resize_volume", classification="caution"
        )
        await db.commit()
        rows = (
            (await db.execute(select(AuditEntry).order_by(AuditEntry.timestamp)))
            .scalars()
            .all()
        )
    assert [(row.tool_name, row.result) for row in rows] == [
        ("get_x", {"classification": "unknown"}),
        ("resize_volume", {"classification": "caution"}),
    ]
    await engine.dispose()


# ---------------------------------------------------------------------------
# Every page of a server's tool list
# ---------------------------------------------------------------------------


class _PagedServer:
    """A tool source that answers ``tools/list`` in pages: ``answers`` maps
    the cursor asked with (None for the first page) to the page's tool names
    and the cursor of the next page."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.asked: list = []

    async def list_tools(self, cursor=None, *, params=None):
        key = None if params is None else params.cursor
        self.asked.append(key)
        if isinstance(self.answers[key], Exception):
            raise self.answers[key]
        names, after = self.answers[key]
        tools = [Tool(name=name, inputSchema={"type": "object"}) for name in names]
        return SimpleNamespace(tools=tools, nextCursor=after)


async def test_every_page_of_a_servers_tool_list_is_read():
    server = _PagedServer(
        {
            None: (["list_volumes"], "p2"),
            "p2": (["get_volume_usage"], "p3"),
            "p3": (["resize_volume"], None),
        }
    )
    tools = await list_tools(server)
    assert [tool.name for tool in tools] == [
        "list_volumes",
        "get_volume_usage",
        "resize_volume",
    ]
    assert server.asked == [None, "p2", "p3"]


async def test_a_cursor_given_again_ends_the_reading():
    server = _PagedServer(
        {None: (["list_volumes"], "p2"), "p2": (["get_volume_usage"], "p2")}
    )
    tools = await list_tools(server)
    assert [tool.name for tool in tools] == ["list_volumes", "get_volume_usage"]
    assert server.asked == [None, "p2"]


async def test_a_tool_listed_again_is_kept_once():
    """A server that answers every cursor with its first page is read twice
    and lists each tool once."""
    server = _PagedServer(
        {
            None: (["list_volumes", "get_volume_usage"], "p2"),
            "p2": (["list_volumes", "get_volume_usage"], "p2"),
        }
    )
    tools = await list_tools(server)
    assert [tool.name for tool in tools] == ["list_volumes", "get_volume_usage"]
    assert server.asked == [None, "p2"]


async def test_a_later_page_that_fails_keeps_the_pages_read():
    server = _PagedServer(
        {
            None: (["list_volumes"], "p2"),
            "p2": (["get_volume_usage"], "p3"),
            "p3": ConnectionError("the connection was reset"),
        }
    )
    tools = await list_tools(server)
    assert [tool.name for tool in tools] == ["list_volumes", "get_volume_usage"]
    assert server.asked == [None, "p2", "p3"]


async def test_a_first_page_that_fails_fails_the_listing():
    server = _PagedServer({None: ConnectionError("the connection was refused")})
    with pytest.raises(ConnectionError):
        await list_tools(server)


async def test_a_server_that_never_stops_paging_is_cut_off():
    class Endless:
        def __init__(self) -> None:
            self.pages = 0

        async def list_tools(self, cursor=None, *, params=None):
            self.pages += 1
            tool = Tool(name=f"tool_{self.pages}", inputSchema={"type": "object"})
            return SimpleNamespace(tools=[tool], nextCursor=f"c{self.pages}")

    server = Endless()
    tools = await list_tools(server)
    assert len(tools) == server.pages == MAX_TOOL_PAGES


async def test_a_cursor_that_is_not_text_is_not_followed():
    class OnePage:
        def __init__(self) -> None:
            self.pages = 0

        async def list_tools(self, cursor=None, *, params=None):
            self.pages += 1
            tool = Tool(name="list_volumes", inputSchema={"type": "object"})
            return SimpleNamespace(tools=[tool], nextCursor=5)

    server = OnePage()
    assert [tool.name for tool in await list_tools(server)] == ["list_volumes"]
    assert server.pages == 1
