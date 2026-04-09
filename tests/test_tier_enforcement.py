"""Tests for backend.tiers.enforcement."""

import pytest

from backend.skills.parser import SkillDefinition, OperationClassification
from backend.tiers.enforcement import check, check_and_explain, EnforcementResult


@pytest.fixture()
def skill_def():
    """A small skill definition for testing enforcement."""
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            OperationClassification(tool="get_pods", classification="safe"),
            OperationClassification(tool="scale_deployment", classification="caution"),
            OperationClassification(tool="delete_pod", classification="destructive"),
        ],
    )


class TestCheck:
    # --- Safe operations ---
    def test_safe_permitted_tier_0(self, skill_def):
        r = check("get_pods", 0, skill_def)
        assert r.permitted is True
        assert r.classification == "safe"

    def test_safe_permitted_tier_1(self, skill_def):
        assert check("get_pods", 1, skill_def).permitted is True

    def test_safe_permitted_tier_2(self, skill_def):
        assert check("get_pods", 2, skill_def).permitted is True

    def test_safe_denied_tier_3(self, skill_def):
        r = check("get_pods", 3, skill_def)
        assert r.permitted is False
        assert "advise-only" in r.reason

    # --- Caution operations ---
    def test_caution_permitted_tier_0(self, skill_def):
        assert check("scale_deployment", 0, skill_def).permitted is True

    def test_caution_permitted_tier_1(self, skill_def):
        assert check("scale_deployment", 1, skill_def).permitted is True

    def test_caution_permitted_tier_2(self, skill_def):
        assert check("scale_deployment", 2, skill_def).permitted is True

    def test_caution_denied_tier_3(self, skill_def):
        r = check("scale_deployment", 3, skill_def)
        assert r.permitted is False

    # --- Destructive operations ---
    def test_destructive_permitted_tier_0(self, skill_def):
        assert check("delete_pod", 0, skill_def).permitted is True

    def test_destructive_permitted_tier_1(self, skill_def):
        r = check("delete_pod", 1, skill_def)
        assert r.permitted is True
        assert "approval" in r.reason

    def test_destructive_denied_tier_2(self, skill_def):
        r = check("delete_pod", 2, skill_def)
        assert r.permitted is False

    def test_destructive_denied_tier_3(self, skill_def):
        assert check("delete_pod", 3, skill_def).permitted is False

    # --- Unknown operations ---
    def test_unknown_denied_all_tiers(self, skill_def):
        for tier in (0, 1, 2, 3):
            r = check("exec_shell", tier, skill_def)
            assert r.permitted is False
            assert r.classification == "unknown"

    # --- Invalid tier ---
    def test_invalid_tier_raises(self, skill_def):
        with pytest.raises(ValueError, match="Invalid tier"):
            check("get_pods", 5, skill_def)


class TestCheckAndExplain:
    def test_permit_message(self, skill_def):
        msg = check_and_explain("get_pods", 2, skill_def)
        assert msg.startswith("[PERMIT]")
        assert "get_pods" in msg

    def test_deny_message(self, skill_def):
        msg = check_and_explain("delete_pod", 2, skill_def)
        assert msg.startswith("[DENY]")
        assert "delete_pod" in msg


class TestEnforcementResult:
    def test_fields(self):
        r = EnforcementResult(
            permitted=True, classification="safe", tier=2, reason="ok"
        )
        assert r.permitted is True
        assert r.tier == 2
