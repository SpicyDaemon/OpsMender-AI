"""Tests for backend.tiers.enforcement."""

import pytest

from backend.skills.parser import SkillDefinition, OperationClassification
from backend.tiers.enforcement import check, check_and_explain, EnforcementResult


@pytest.fixture()
def skill_def():
    """Small skill definition with mixed reversibility for enforcement tests.

    ``scale_deployment`` and ``delete_pod`` are explicitly marked reversible
    and given compensating inverses so the Tier 0 matrix tests exercise
    rollback-safe writes. Non-reversible / no-inverse variants are covered in
    ``TestTier0SandboxFloor`` below.
    """
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            OperationClassification(tool="get_pods", classification="safe"),
            OperationClassification(
                tool="scale_deployment",
                classification="caution",
                reversible=True,
                compensating_inverse="restore_scale",
            ),
            OperationClassification(
                tool="delete_pod",
                classification="destructive",
                reversible=True,
                compensating_inverse="recreate_pod",
            ),
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


class TestTier0SandboxFloor:
    """Tier 0 only executes ops resolved as reversible in the skill def."""

    def test_non_reversible_caution_denied_at_tier_0(self):
        sd = SkillDefinition(
            version="1",
            environment="test",
            operations=[
                OperationClassification(
                    tool="rollout_restart", classification="caution"
                ),
            ],
        )
        r = check("rollout_restart", 0, sd)
        assert r.permitted is False
        assert r.reversible is False
        assert "sandbox floor" in r.reason

    def test_non_reversible_destructive_denied_at_tier_0(self):
        sd = SkillDefinition(
            version="1",
            environment="test",
            operations=[
                OperationClassification(tool="delete_all", classification="destructive"),
            ],
        )
        r = check("delete_all", 0, sd)
        assert r.permitted is False
        assert "sandbox floor" in r.reason

    def test_explicit_reversible_permits_at_tier_0(self):
        sd = SkillDefinition(
            version="1",
            environment="test",
            operations=[
                OperationClassification(
                    tool="cordon_node",
                    classification="caution",
                    reversible=True,
                    compensating_inverse="uncordon_node",
                ),
            ],
        )
        r = check("cordon_node", 0, sd)
        assert r.permitted is True
        assert r.reversible is True

    def test_side_effecting_tier_0_op_without_inverse_is_denied(self):
        sd = SkillDefinition(
            version="1",
            environment="test",
            operations=[
                OperationClassification(
                    tool="rollout_restart",
                    classification="caution",
                    reversible=True,
                ),
            ],
        )
        r = check("rollout_restart", 0, sd)
        assert r.permitted is False
        assert "compensating_inverse" in r.reason

    def test_safe_is_implicitly_reversible_at_tier_0(self):
        """Reads never change state, so they run at Tier 0 without annotation."""
        sd = SkillDefinition(
            version="1",
            environment="test",
            operations=[OperationClassification(tool="get_pods", classification="safe")],
        )
        r = check("get_pods", 0, sd)
        assert r.permitted is True
        assert r.reversible is True

    def test_reversibility_gate_does_not_affect_higher_tiers(self):
        """Tier 1/2 ignore reversibility (Tier 0 floor only)."""
        sd = SkillDefinition(
            version="1",
            environment="test",
            operations=[
                OperationClassification(tool="rollout_restart", classification="caution"),
            ],
        )
        assert check("rollout_restart", 1, sd).permitted is True
        assert check("rollout_restart", 2, sd).permitted is True
