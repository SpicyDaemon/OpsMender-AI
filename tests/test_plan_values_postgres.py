"""M1-176: a plan value PostgreSQL refuses does not fail the session.

Run with PART4_PG_URL pointed at a disposable database. The URL is not
logged. On main every case below failed the session: the audit log or an
approval request was given a value PostgreSQL text or JSONB refuses (a lone
surrogate, a NUL character, NaN) or a justification that was not text. The
plan's parser now keeps every value storable and each text field the
record prints one line, so the session goes on and the record holds what
was called.
"""

from __future__ import annotations

import os
import uuid

import pytest
from mcp.types import CallToolResult, TextContent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.agent.graph import build_graph
from backend.api.session_runner import LiveAuditLogger
from backend.approvals.service import ApprovalService
from backend.db.models import ApprovalRequest, AuditEntry, Base, Organization
from backend.db.repos import IncidentRepo, SessionRepo
from backend.skills.parser import (
    OperationClassification,
    OperationTierPolicy,
    SkillDefinition,
)

pytestmark = pytest.mark.integration

BACKSLASH = chr(92)
LONE = BACKSLASH + "ud800"
NUL = BACKSLASH + "u0000"
READ = {tier: OperationTierPolicy(True, "autonomous") for tier in (0, 1, 2, 3)}
WRITE = {
    0: OperationTierPolicy(False, "blocked"),
    1: OperationTierPolicy(True, "approval"),
    2: OperationTierPolicy(False, "blocked"),
    3: OperationTierPolicy(True, "autonomous"),
}
SKILL = SkillDefinition(
    version="1",
    environment="test",
    operations=[
        OperationClassification(tool="get_*", classification="safe", tiers=READ),
        OperationClassification(
            tool="resize_volume", classification="caution", tiers=WRITE
        ),
    ],
)
NAMES = ["get_volume_usage", "list_volumes", "resize_volume"]
REPLACED = chr(0xFFFD)

# label: (tier, the model's plan answer, what the record keeps)
CASES = {
    "a lone surrogate in a parameter of a read": (
        2,
        '[{"tool_name": "get_volume_usage", "tool_parameters": {"volume_id": "a'
        + LONE
        + '"}, "justification": "read"}]',
        {"called": ["get_volume_usage"], "parameters": {"volume_id": "a" + REPLACED}},
    ),
    "a lone surrogate in a parameter of a refused action": (
        2,
        '[{"tool_name": "resize_volume", "tool_parameters": {"size_gib": "2'
        + LONE
        + '"}, "justification": "grow"}]',
        {"called": [], "parameters": {"size_gib": "2" + REPLACED}},
    ),
    "a NUL character in a parameter of a read": (
        2,
        '[{"tool_name": "get_volume_usage", "tool_parameters": {"volume_id": "a'
        + NUL
        + 'b"}, "justification": "read"}]',
        {"called": ["get_volume_usage"], "parameters": {"volume_id": "ab"}},
    ),
    "NaN as a parameter of a read": (
        2,
        '[{"tool_name": "get_volume_usage", "tool_parameters": {"volume_id": NaN},'
        ' "justification": "read"}]',
        {"called": ["get_volume_usage"], "parameters": {"volume_id": "nan"}},
    ),
    "a request whose justification is not text": (
        1,
        '[{"tool_name": "resize_volume", "tool_parameters": {"size_gib": "2"},'
        ' "justification": ["grow", "now"]}]',
        {"called": [], "parameters": {"size_gib": "2"}, "asked": '["grow", "now"]'},
    ),
    "a request whose justification holds a lone surrogate": (
        1,
        '[{"tool_name": "resize_volume", "tool_parameters": {"size_gib": "2"},'
        ' "justification": "grow' + LONE + '"}]',
        {"called": [], "parameters": {"size_gib": "2"}, "asked": "grow" + REPLACED},
    ),
}


class _Model:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.plans = 0

    def invoke(self, prompt: str) -> str:
        if "Return your plan as a JSON array" not in prompt:
            return "noted"
        self.plans += 1
        return self.answer if self.plans == 1 else "[]"


@pytest.fixture
async def pg():
    url = os.environ.get("PART4_PG_URL")
    if not url:
        pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            Organization(
                id=org_id, name="Plan values", slug=f"plan-values-{org_id.hex[:8]}"
            )
        )
        await db.commit()
    yield factory, org_id
    # Leave the database as the test found it: a later PostgreSQL test file
    # counts rows of its own (M1-176's first gate run).
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.parametrize("label", list(CASES))
async def test_a_plan_value_postgresql_refuses_does_not_fail_the_session(pg, label):
    factory, org_id = pg
    tier, answer, expected = CASES[label]
    async with factory() as db:
        incident = await IncidentRepo.create(
            db, org_id, title="Disk usage", description="97 percent"
        )
        session = await SessionRepo.create(
            db, org_id, tier=tier, incident_id=incident.id
        )
        await db.commit()
        session_id = session.id

    async def publish(_session_id, _message):
        return None

    called: list[str] = []

    async def caller(_session, tool_name, params):
        called.append(tool_name)
        return CallToolResult(
            isError=False,
            content=[TextContent(type="text", text="3 items, all healthy")],
        )

    approvals = (
        ApprovalService(
            factory,
            org_id=org_id,
            timeout_seconds=1,
            poll_interval_seconds=0.05,
            publisher=publish,
        )
        if tier == 1
        else None
    )
    graph = build_graph(
        tier=tier,
        skill_def=SKILL,
        llm=_Model(answer),
        mcp_session=object(),
        audit_logger=LiveAuditLogger(
            factory, org_id=org_id, session_id=session_id, publisher=publish
        ),
        approval_service=approvals,
        tool_caller=caller,
        plan_tool_names=NAMES,
    )
    result = await graph.ainvoke(
        {
            "session_id": str(session_id),
            "tier": tier,
            "incident_description": "Disk usage on the reports volume is 97 percent.",
            "incident": {"title": "Disk usage", "description": "97", "status": "open"},
            "operator_guidance": [],
            "redirect_requested": False,
            "redirect_count": 0,
        },
        {"recursion_limit": 60},
    )

    assert called == expected["called"]
    (action,) = result["plan"]
    assert action["tool_parameters"] == expected["parameters"]
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEntry).where(AuditEntry.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
        requests = (
            (
                await db.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.session_id == session_id
                    )
                )
            )
            .scalars()
            .all()
        )
    kept = [
        row.tool_parameters
        for row in rows
        if row.entry_type in ("tool_call_start", "tool_call_blocked")
    ]
    assert kept == [expected["parameters"]]
    if tier == 1:
        (request,) = requests
        assert request.justification == expected["asked"]
        assert request.status == "expired"
    else:
        assert requests == []
