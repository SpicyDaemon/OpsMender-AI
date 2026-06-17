"""Pure unit tests for the SLO-breach recommendation engine (v1.2 Phase 6)."""

from __future__ import annotations

from backend.sla.recommendations import evaluate_slo_recommendation


def _eval(**overrides):
    base = dict(
        slo_name="api-availability",
        target_name="api",
        objective_pct=99.9,
        actual_pct=100.0,
        error_budget_remaining_pct=100.0,
        burn_rate=0.0,
        compliant=True,
        target_status="up",
        service_name=None,
        team_name=None,
    )
    base.update(overrides)
    return evaluate_slo_recommendation(**base)


def test_healthy_slo_returns_none():
    assert _eval() is None


def test_compliant_but_low_budget_is_warning():
    v = _eval(compliant=True, error_budget_remaining_pct=10.0, actual_pct=99.91)
    assert v is not None
    assert v.severity == "warning"
    assert "at risk" in v.headline


def test_breaching_low_burn_is_warning():
    v = _eval(compliant=False, actual_pct=99.85, burn_rate=1.5,
              error_budget_remaining_pct=15.0)
    assert v is not None
    assert v.severity == "warning"
    assert "breaching" in v.headline


def test_breaching_fast_burn_is_critical():
    v = _eval(compliant=False, actual_pct=98.0, burn_rate=20.0,
              error_budget_remaining_pct=0.0)
    assert v is not None
    assert v.severity == "critical"


def test_unlinked_target_recommends_linking_service():
    v = _eval(compliant=False, actual_pct=99.0, burn_rate=10.0, service_name=None)
    assert any("isn't linked to a Service" in a for a in v.actions)


def test_linked_target_routes_to_service_and_team():
    v = _eval(
        compliant=False, actual_pct=99.0, burn_rate=10.0,
        service_name="Checkout", team_name="Payments",
    )
    assert any("Checkout" in a and "Payments" in a for a in v.actions)


def test_down_target_surfaces_investigation_action_first():
    v = _eval(compliant=False, actual_pct=90.0, burn_rate=50.0, target_status="down")
    assert "DOWN" in v.actions[0]


def test_always_advisory_only():
    v = _eval(compliant=False, actual_pct=99.0, burn_rate=10.0)
    assert any("Advisory only" in a for a in v.actions)
