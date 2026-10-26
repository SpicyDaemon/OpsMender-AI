"""M1-89: actions the tier gate blocks are written to the audit log (AI-07).

In the 2026-10-24 live round a Tier 2 session with five blocked actions left
session_start and session_end in the audit log and nothing else: only the
executor wrote ``tool_call_blocked``, and blocked plan actions never reach it.
The gate now writes one entry per blocked action, so the session page's Tool
Activity, the incident timeline and the session-scoped audit list show what the
agent wanted to do and why it was refused.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent

from backend.agent.graph import build_graph
from backend.agent.nodes import _build_tier_gate
from backend.api import session_runner
from backend.audit.logger import AuditLogger
from backend.db.repos import IncidentRepo, ServiceRepo, SessionRepo, TeamRepo
from backend.skills.parser import loads
from tests.test_tier2_reads import SKILL
from tests.test_tool_source_overlap import TEST_ORG_ID, app  # noqa: F401

PLAN = [
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
    {
        "tool_name": "shell",
        "tool_parameters": {"cmd": "kubectl delete pod ratings-1"},
        "justification": "fix",
    },
]


def _entries(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _blocked(path):
    return [e for e in _entries(path) if e["entry_type"] == "tool_call_blocked"]


async def test_a_tier_2_session_audits_each_blocked_action_with_the_gate_reason(
    tmp_path,
):
    class PlanningLLM:
        def invoke(self, prompt: str) -> str:
            if "Propose a list of remediation actions" in prompt:
                return json.dumps(PLAN)
            return "[]"

    async def caller(_session, tool_name, params):
        return CallToolResult(
            isError=False, content=[TextContent(type="text", text="2 pods running")]
        )

    log = tmp_path / "audit.jsonl"
    session_id = str(uuid.uuid4())
    graph = build_graph(
        tier=2,
        skill_def=loads(SKILL),
        llm=PlanningLLM(),
        mcp_session=object(),
        audit_logger=AuditLogger(log),
        tool_caller=caller,
    )
    result = await graph.ainvoke(
        {"session_id": session_id, "tier": 2, "incident_description": "500s"}
    )

    blocked = _blocked(log)
    assert [e["tool_name"] for e in blocked] == ["restart_pod", "shell"]
    assert [e["tool_parameters"] for e in blocked] == [
        {"pod": "ratings-1"},
        {"cmd": "kubectl delete pod ratings-1"},
    ]
    assert all(e["tier"] == 2 and e["session_id"] == session_id for e in blocked)
    assert all(e["permitted"] is False for e in blocked)
    # The entry carries the reason the gate gave, the same one the state holds.
    assert [e["block_reason"] for e in blocked] == [
        a["block_reason"] for a in result["blocked_actions"]
    ]
    assert all(e["block_reason"] for e in blocked)
    # The read ran and was audited once, as before; it is not blocked.
    types = [(e["entry_type"], e.get("tool_name")) for e in _entries(log)]
    assert types.count(("tool_call_start", "get_pods")) == 1
    assert ("tool_call_blocked", "get_pods") not in types


async def test_an_approval_action_without_an_approval_service_is_audited(tmp_path):
    log = tmp_path / "audit.jsonl"
    gate = _build_tier_gate(1, loads(SKILL), audit_logger=AuditLogger(log))
    update = await gate({"session_id": str(uuid.uuid4()), "plan": PLAN[1:2]})

    assert [a["tool_name"] for a in update["blocked_actions"]] == ["restart_pod"]
    [entry] = _blocked(log)
    assert entry["tool_name"] == "restart_pod" and entry["tier"] == 1
    assert "requires an approval service" in entry["block_reason"]


class _Approvals:
    """Resolves every request with a fixed status, as an operator would."""

    def __init__(self, status: str, block_reason: str | None = None):
        self.status, self.block_reason = status, block_reason

    async def request_and_wait(self, *, session_id, action, justification=None):
        request = SimpleNamespace(
            id=uuid.uuid4(),
            status=self.status,
            action=action,
            expires_at=datetime.now(timezone.utc),
        )
        return SimpleNamespace(
            request=request, block_reason=self.block_reason, guidance=None
        )


# An action no skill names is refused at every tier.
UNKNOWN = {"tool_name": "drop_database", "tool_parameters": {"name": "shop"}}


async def test_a_redirect_still_audits_what_the_gate_blocked_before_it(tmp_path):
    # The redirect drops this pass's lists from the state, but the action the
    # gate refused before the approval wait is already in the audit log.
    log = tmp_path / "audit.jsonl"
    gate = _build_tier_gate(
        1, loads(SKILL), _Approvals("redirected"), audit_logger=AuditLogger(log)
    )
    update = await gate({"session_id": str(uuid.uuid4()), "plan": [UNKNOWN, PLAN[1]]})

    assert update["redirect_requested"] is True and update["blocked_actions"] == []
    assert [e["tool_name"] for e in _blocked(log)] == ["drop_database"]


async def test_a_block_is_audited_before_a_later_approval_wait_ends(tmp_path):
    # A stop or override while the gate waits on a later approval must not
    # lose what it already refused.
    class _Stopped:
        async def request_and_wait(self, **kwargs):
            raise RuntimeError("session stopped while waiting")

    log = tmp_path / "audit.jsonl"
    gate = _build_tier_gate(1, loads(SKILL), _Stopped(), audit_logger=AuditLogger(log))
    with pytest.raises(RuntimeError):
        await gate({"session_id": str(uuid.uuid4()), "plan": [UNKNOWN, PLAN[1]]})

    assert [e["tool_name"] for e in _blocked(log)] == ["drop_database"]


@pytest.mark.parametrize("approvals", [None, "rejected"])
async def test_model_written_values_are_shaped_to_fit_the_audit_row(
    tmp_path, approvals
):
    odd = [
        {"tool_name": "x" * 300, "tool_parameters": ["a", "b"]},
        {"tool_name": 42, "tool_parameters": "restart everything"},
        {"tool_parameters": None},
    ]
    log = tmp_path / "audit.jsonl"
    service = _Approvals(approvals) if approvals else None
    gate = _build_tier_gate(1, loads(SKILL), service, audit_logger=AuditLogger(log))
    await gate({"session_id": str(uuid.uuid4()), "plan": odd})

    entries = _blocked(log)
    assert [len(e["tool_name"]) for e in entries] == [200, 2, 7]
    assert [e["tool_name"][:2] for e in entries] == ["xx", "42", "un"]
    assert [e["tool_parameters"] for e in entries] == [
        {"value": ["a", "b"]},
        {"value": "restart everything"},
        {},
    ]


async def test_a_rejected_approval_is_audited_as_blocked_with_its_reason(tmp_path):
    log = tmp_path / "audit.jsonl"
    gate = _build_tier_gate(
        1,
        loads(SKILL),
        _Approvals("rejected", "Approval rejected by human operator"),
        audit_logger=AuditLogger(log),
    )
    update = await gate({"session_id": str(uuid.uuid4()), "plan": PLAN[:2]})

    assert [a["tool_name"] for a in update["approved_actions"]] == ["get_pods"]
    [entry] = _blocked(log)
    assert entry["tool_name"] == "restart_pod"
    assert entry["block_reason"] == "Approval rejected by human operator"


async def test_an_approved_action_and_a_redirect_write_no_blocked_entry(tmp_path):
    for status in ("approved", "redirected"):
        log = tmp_path / f"{status}.jsonl"
        gate = _build_tier_gate(
            1, loads(SKILL), _Approvals(status), audit_logger=AuditLogger(log)
        )
        await gate({"session_id": str(uuid.uuid4()), "plan": PLAN[1:2]})
        assert not log.exists() or _blocked(log) == []


def test_a_gate_without_an_audit_logger_stays_a_plain_function():
    gate = _build_tier_gate(2, loads(SKILL))
    update = gate({"session_id": str(uuid.uuid4()), "plan": PLAN})
    assert [a["tool_name"] for a in update["blocked_actions"]] == [
        "restart_pod",
        "shell",
    ]


async def test_the_runner_gives_the_gate_its_audit_logger_without_a_tool_source(
    app,  # noqa: F811
    monkeypatch,
):
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name="Team b", slug="team-b")
        service = await ServiceRepo.create(
            db, TEST_ORG_ID, team_id=team.id, name="svc-b", slug="svc-b"
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Checkout 500s",
            description="d",
            service_id=service.id,
        )
        session = await SessionRepo.create(
            db, TEST_ORG_ID, tier=2, incident_id=incident.id
        )
        await db.commit()
        session_id = session.id

    async def _no_llm(factory, session):
        return SimpleNamespace()

    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state, *args, **kwargs):
            return {"status": "completed", "summary": "ok"}

    def _capture_graph(**kwargs):
        captured.update(kwargs)
        return _FakeGraph()

    monkeypatch.setattr(session_runner, "_resolve_llm", _no_llm)
    monkeypatch.setattr(session_runner, "build_graph", _capture_graph)
    app.state.workflow_start_delay_seconds = 0

    await session_runner._run_session_workflow_inner(app, session_id=session_id)

    assert "mcp_session" not in captured
    assert isinstance(captured["audit_logger"], session_runner.LiveAuditLogger)
    async with app.state.session_factory() as db:
        stored = await SessionRepo.get_by_id(db, TEST_ORG_ID, session_id)
    assert stored.status == "completed", stored.status
