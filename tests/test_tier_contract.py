"""The plan prompt states what the active tier allows (M1-90, AI-06).

In the 2026-10-24 live round the prompt said only "Current tier: 1 (determines
what actions are allowed)", and in sixteen Tier 1 sessions the model never
proposed a write, so no approval request was ever created. The prompt now
states each tier's contract; the tier gate still decides what runs.
"""

from __future__ import annotations

import pytest

from backend.agent.llm import StubLLM
from backend.agent.nodes import _build_plan, _build_plan_with_tool_names, tier_contract
from backend.skills.parser import SkillDefinition
from tests.skill_policy_helpers import explicit_operation

SKILL = SkillDefinition(
    version="1",
    environment="t",
    operations=[
        explicit_operation("get_logs", "safe"),
        explicit_operation("restart_service", "caution"),
    ],
)


def test_each_tier_states_what_runs_what_needs_approval_and_what_is_blocked():
    assert tier_contract(0).startswith("Current tier: 0 (Autonomous).")
    assert "runs at once, without a person" in tier_contract(0)
    assert "blocked and recorded" in tier_contract(0)

    assert tier_contract(1).startswith("Current tier: 1 (Approval Required).")
    assert (
        "becomes an approval request that an operator approves or rejects"
        in tier_contract(1)
    )
    assert "listing it here is the only way to ask" in tier_contract(1)

    assert tier_contract(2).startswith("Current tier: 2 (Advisory Only).")
    assert "Only the reads the skill allows at Tier 2 run" in tier_contract(2)
    assert "nothing can be approved" in tier_contract(2)

    assert tier_contract(3) == tier_contract(2)  # legacy Tier 3 is Tier 2


@pytest.mark.parametrize("tier", [0, 1, 2])
def test_both_plan_nodes_render_the_active_tiers_contract(tier):
    for build in (
        lambda llm: _build_plan_with_tool_names(
            llm, tier, SKILL, ["get_logs", "restart_service"]
        ),
        lambda llm: _build_plan(llm, tier, SKILL),
    ):
        llm = StubLLM(response="[]")
        build(llm)({"diagnosis": "productpage returns 500s"})
        prompt = llm.calls[-1]
        assert tier_contract(tier) in prompt
        assert "determines what actions are allowed" not in prompt
