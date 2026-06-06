"""v1 safety hardening — generic-tool guardrail, deny-list precedence, approval
routing, and conservative defaults at the backend tier gate.

These lock in the guarantee that the AI cannot execute beyond the selected tier
and the MCP Skill policy, regardless of prompt text.
"""

from __future__ import annotations

import pytest

from backend.skills.parser import SkillDefinition, OperationClassification, loads
from backend.tiers.enforcement import check
from backend.tiers.generic_tools import is_generic_execution_tool


def _skill(*ops: OperationClassification) -> SkillDefinition:
    return SkillDefinition(version="1", environment="test", operations=list(ops))


# ---------------------------------------------------------------------------
# Generic command-execution guardrail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["shell", "bash", "run_command", "kubectl", "aws_cli", "gcloud", "az",
     "terraform", "sql", "python", "exec_anything", "do_command", "run_query"],
)
def test_generic_tools_detected(name):
    assert is_generic_execution_tool(name) is True


@pytest.mark.parametrize("name", ["get_pods", "scale_deployment", "describe_node", "list_services"])
def test_normal_tools_not_generic(name):
    assert is_generic_execution_tool(name) is False


def test_generic_tool_blocked_at_tier_0_and_2():
    sd = _skill()  # no policy
    assert check("kubectl", 0, sd).permitted is False
    assert check("kubectl", 0, sd).classification == "generic_execution"
    assert check("kubectl", 2, sd).permitted is False


def test_generic_tool_requires_approval_at_tier_1():
    sd = _skill()
    r = check("run_command", 1, sd)
    assert r.permitted is True
    assert r.requires_approval is True
    assert r.classification == "generic_execution"


def test_generic_tool_opt_out_with_allow_generic():
    # allow_generic opts a narrowly-scoped wrapper out of the guardrail.
    sd = _skill(
        OperationClassification(tool="kubectl", classification="safe", allow_generic=True)
    )
    r = check("kubectl", 0, sd)
    # Now normal rules apply: safe + reversible runs at Tier 0.
    assert r.classification == "safe"
    assert r.permitted is True


def test_generic_opt_out_does_not_apply_to_glob():
    # allow_generic only applies to the exact matched entry; a different generic
    # tool with no entry is still guarded.
    sd = _skill(
        OperationClassification(tool="kubectl", classification="safe", allow_generic=True)
    )
    assert check("bash", 0, sd).permitted is False


# ---------------------------------------------------------------------------
# Deny-list precedence (deny always wins)
# ---------------------------------------------------------------------------


def test_deny_wins_over_safe_classification_all_tiers():
    sd = _skill(OperationClassification(tool="get_secret", classification="safe", deny=True))
    for tier in (0, 1, 2):
        r = check("get_secret", tier, sd)
        assert r.permitted is False
        assert "deny-list" in r.reason


def test_deny_wins_over_allow_generic():
    # deny beats allow_generic.
    sd = _skill(
        OperationClassification(
            tool="kubectl", classification="safe", allow_generic=True, deny=True
        )
    )
    assert check("kubectl", 1, sd).permitted is False


def test_deny_glob_pattern():
    sd = _skill(OperationClassification(tool="delete_*", classification="destructive", deny=True))
    assert check("delete_database", 0, sd).permitted is False
    assert check("delete_database", 1, sd).permitted is False


def test_deny_entry_without_classification_parses():
    # A deny entry may omit classification; it defaults to destructive.
    sd = loads(
        """---
version: "1"
environment: test
operations:
  - tool: drop_table
    deny: true
---
"""
    )
    r = check("drop_table", 0, sd)
    assert r.permitted is False
    assert "deny-list" in r.reason


# ---------------------------------------------------------------------------
# Conservative defaults: no/empty skill, unknown actions
# ---------------------------------------------------------------------------


def test_empty_skill_denies_write_actions_at_every_tier():
    sd = _skill()  # no operations at all
    for tier in (0, 1, 2):
        # Unknown (unclassified) write-like action is denied — never silently run.
        assert check("delete_pod", tier, sd).permitted is False


def test_destructive_requires_approval_at_tier_1():
    sd = _skill(OperationClassification(tool="delete_pod", classification="destructive"))
    r = check("delete_pod", 1, sd)
    assert r.permitted is True
    assert r.requires_approval is True


def test_destructive_blocked_at_tier_2():
    sd = _skill(OperationClassification(tool="delete_pod", classification="destructive"))
    assert check("delete_pod", 2, sd).permitted is False
