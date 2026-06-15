"""Explicit MCP Skill tier policy and session-tier resolution coverage."""

import pytest

from backend.skills.parser import loads
from backend.tiers.enforcement import check
from backend.tiers.resolution import resolve_session_tier
from backend.tiers.sandbox import Tier0Sandbox


EXPLICIT_SKILL = """---
version: "1"
environment: production
default_tier: T1
operations:
  - tool: delete_stuck_pod
    classification: destructive
    reversible: false
    tiers:
      T0:
        enabled: true
        mode: autonomous
        require_reversible: false
      T1:
        enabled: true
        mode: approval
      T2:
        enabled: false
        mode: blocked
  - tool: restart_deployment
    classification: caution
    reversible: true
    tiers:
      T0:
        enabled: true
        mode: autonomous
        require_reversible: true
      T1:
        enabled: true
        mode: approval
      T2:
        enabled: true
        mode: advisory
  - tool: kubectl
    classification: caution
    tiers:
      T0:
        enabled: true
        mode: autonomous
        require_reversible: false
      T1:
        enabled: true
        mode: approval
      T2:
        enabled: false
        mode: blocked
  - tool: delete_database
    deny: true
---
"""


def test_parser_reads_explicit_tiers_and_skill_default():
    skill = loads(EXPLICIT_SKILL)
    assert skill.default_tier == 1
    assert skill.tier_policy("delete_stuck_pod", 0).mode == "autonomous"
    assert skill.tier_policy("delete_stuck_pod", 0).require_reversible is False


def test_parser_rejects_string_boolean_in_tier_policy():
    with pytest.raises(ValueError, match="must be true or false"):
        loads(EXPLICIT_SKILL.replace("enabled: true", 'enabled: "false"', 1))


def test_explicit_t0_destructive_can_override_reversible_floor():
    result = check("delete_stuck_pod", 0, loads(EXPLICIT_SKILL))
    assert result.permitted is True
    assert result.requires_approval is False
    assert result.decision == "autonomous"
    sandbox = Tier0Sandbox.from_skill(loads(EXPLICIT_SKILL))
    assert "delete_stuck_pod" in sandbox.allowed_tool_names


def test_explicit_t0_reversible_requirement_blocks_missing_inverse():
    result = check("restart_deployment", 0, loads(EXPLICIT_SKILL))
    assert result.permitted is False
    assert "compensating_inverse" in result.reason


def test_explicit_t1_destructive_requires_approval():
    result = check("delete_stuck_pod", 1, loads(EXPLICIT_SKILL))
    assert result.permitted is True
    assert result.requires_approval is True
    assert result.decision == "approval"


def test_explicit_t2_write_is_blocked_or_advisory():
    blocked = check("delete_stuck_pod", 2, loads(EXPLICIT_SKILL))
    advisory = check("restart_deployment", 2, loads(EXPLICIT_SKILL))
    assert blocked.permitted is False
    assert blocked.decision == "deny"
    assert advisory.permitted is False
    assert advisory.decision == "advisory"


def test_deny_and_unknown_still_win_at_every_tier():
    skill = loads(EXPLICIT_SKILL)
    for tier in (0, 1, 2):
        assert check("delete_database", tier, skill).permitted is False
        assert check("not_declared", tier, skill).permitted is False


def test_generic_guard_still_requires_allow_generic():
    skill = loads(EXPLICIT_SKILL)
    assert check("kubectl", 0, skill).permitted is False
    assert check("kubectl", 1, skill).requires_approval is True


def test_allow_generic_exposes_explicit_policy():
    skill = loads(
        EXPLICIT_SKILL.replace(
            "classification: caution\n    tiers:\n      T0:",
            "classification: caution\n    allow_generic: true\n    tiers:\n      T0:",
            1,
        )
    )
    result = check("kubectl", 1, skill)
    assert result.permitted is True
    assert result.requires_approval is True


def test_session_tier_resolution_priority_and_fallback():
    assert resolve_session_tier(
        requested_tier=0,
        service_tier=1,
        skill_default_tier=2,
        org_default_tier=2,
    ) == 0
    assert resolve_session_tier(
        service_tier=1,
        skill_default_tier=0,
        org_default_tier=2,
    ) == 1
    assert resolve_session_tier(skill_default_tier=0, org_default_tier=1) == 0
    assert resolve_session_tier(org_default_tier=1) == 1
    assert resolve_session_tier() == 2
