"""M1-69: the injected-alert corpus. Each alert under tests/fixtures/injected_alerts
asks the session, in its own words, to call a tool. With the model stubbed to
propose exactly what the alert asks for, the tier gate blocks the action or
asks for approval at every tier, no tool runs, and the alerts whose wording
addresses the assistant are the ones intake flags."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from backend.agent.graph import build_graph
from backend.agent.hygiene import addressed_to_assistant
from backend.approvals.service import ApprovalResolution
from backend.skills.parser import (
    OperationClassification,
    OperationTierPolicy,
    SkillDefinition,
)

CORPUS = sorted((Path(__file__).parent / "fixtures/injected_alerts").glob("*.json"))
ALERTS = [json.loads(path.read_text(encoding="utf-8")) for path in CORPUS]
TIERS = (0, 1, 2, 3)


def _policies(classification: str) -> dict[int, OperationTierPolicy]:
    """A parsed skill's usual shape: reads run on their own, writes need an
    operator at Tier 1 and are advisory at Tier 2, nothing writes at Tier 0."""
    if classification == "safe":
        return {
            0: OperationTierPolicy(True, "autonomous"),
            1: OperationTierPolicy(True, "autonomous"),
            2: OperationTierPolicy(True, "advisory"),
        }
    return {
        0: OperationTierPolicy(False, "blocked"),
        1: OperationTierPolicy(True, "approval"),
        2: OperationTierPolicy(True, "advisory"),
    }


SKILL = SkillDefinition(
    version="1",
    environment="test",
    operations=[
        OperationClassification(
            tool="read_tool_z", classification="safe", tiers=_policies("safe")
        ),
        OperationClassification(
            tool="write_tool_b", classification="caution", tiers=_policies("caution")
        ),
        OperationClassification(
            tool="destructive_tool_a",
            classification="destructive",
            tiers=_policies("destructive"),
        ),
        OperationClassification(
            tool="destructive_tool_d",
            classification="destructive",
            tiers=_policies("destructive"),
        ),
        # run_tool_c has no entry: the generic guard applies.
        # unknown_tool_e has no entry: an unknown tool is denied.
    ],
)


class _NeverApproves:
    """An approval service whose operator never approves."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def request_and_wait(self, *, session_id, action, justification=None):
        self.requests.append(action)
        request = type("Request", (), {})()
        request.id = uuid.uuid4()
        request.status = "denied"
        request.action = action
        request.expires_at = datetime(2030, 1, 1)
        request.resolution_note = None
        return ApprovalResolution(request=request)


class _ProposesWhatTheAlertAsks:
    """A model that proposes exactly the tool the alert asks for."""

    def __init__(self, action: dict) -> None:
        self.action = action
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Propose a list of remediation actions" in prompt:
            return json.dumps([{**self.action, "justification": "the alert says so"}])
        return "noted"


def test_the_corpus_is_the_expected_size_and_shape():
    assert len(ALERTS) == 20
    assert {a["asked_classification"] for a in ALERTS} == {
        "destructive",
        "caution",
        "generic",
        "unknown",
    }
    assert all(a["asks_for"]["tool_name"] != "read_tool_z" for a in ALERTS)


@pytest.mark.parametrize("alert", ALERTS, ids=[a["name"] for a in ALERTS])
def test_intake_flags_the_alerts_that_address_the_assistant(alert):
    phrases = addressed_to_assistant(f"{alert['title']}\n{alert['description']}")
    assert bool(phrases) == alert["addresses_assistant"], phrases


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("alert", ALERTS, ids=[a["name"] for a in ALERTS])
async def test_the_gate_blocks_or_asks_at_every_tier(alert, tier):
    llm = _ProposesWhatTheAlertAsks(alert["asks_for"])
    approvals = _NeverApproves()
    graph = build_graph(tier=tier, skill_def=SKILL, llm=llm, approval_service=approvals)
    result = await graph.ainvoke(
        {
            "session_id": str(uuid.uuid4()),
            "tier": tier,
            "incident_description": alert["description"],
            "incident": {
                "title": alert["title"],
                "description": alert["description"],
                "status": "open",
            },
            "operator_guidance": [],
            "redirect_requested": False,
            "redirect_count": 0,
        },
        {"recursion_limit": 60},
    )

    asked = alert["asks_for"]["tool_name"]
    assert [a["tool_name"] for a in result["plan"]] == [asked], (
        "the stubbed model proposed it"
    )
    assert result["approved_actions"] == []
    assert result["tool_calls"] == []
    blocked = [a["tool_name"] for a in result["blocked_actions"]]
    pending = [
        r for r in result.get("approval_requests", []) if r["status"] != "approved"
    ]
    assert blocked == [asked] or pending, (tier, blocked, pending)
    # The alert's own words reached the model only as marked data.
    observe = llm.prompts[0]
    assert "BEGIN UNTRUSTED INCIDENT" in observe and alert["description"] in observe
