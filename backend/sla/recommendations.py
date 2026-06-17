"""SLO-breach recommendations (v1.2 Phase 6).

Pure, deterministic advisory logic — **no LLM, no auto-incident, no auto-page**.
Given an SLO's computed compliance status (objective/actual/error-budget/burn
rate), the current target status, and the owning service/team (when the SLA
target is linked), produce a human-readable recommendation for a breaching or
at-risk SLO, or ``None`` when the SLO is healthy.

Per ROADMAP, SLO breaches stay **warnings/recommendations** in v1 — an operator
decides whether to act. These recommendations exist to make that decision
faster and route it to the right team.
"""

from __future__ import annotations

import dataclasses

# An SLO is "at risk" (warning, even while still compliant) when this much or
# less of its error budget remains.
_AT_RISK_BUDGET_PCT = 20.0
# Burn rate at/above which a breach is escalated to "critical".
_CRITICAL_BURN_RATE = 2.0


@dataclasses.dataclass(frozen=True)
class RecommendationVerdict:
    severity: str  # "critical" | "warning"
    headline: str
    actions: list[str]


def evaluate_slo_recommendation(
    *,
    slo_name: str,
    target_name: str,
    objective_pct: float,
    actual_pct: float,
    error_budget_remaining_pct: float,
    burn_rate: float,
    compliant: bool,
    target_status: str,
    service_name: str | None,
    team_name: str | None,
) -> RecommendationVerdict | None:
    """Return a recommendation for a breaching/at-risk SLO, or ``None`` if healthy.

    Severity:
      - **critical** — breaching (actual < objective) and either the error
        budget is exhausted or it's burning fast (burn rate ≥ 2×).
      - **warning** — breaching but not yet critical, *or* still compliant but
        with ≤ 20% of the error budget remaining (at risk).
      - healthy SLOs return ``None``.
    """
    breaching = not compliant
    at_risk = compliant and error_budget_remaining_pct <= _AT_RISK_BUDGET_PCT

    if not breaching and not at_risk:
        return None

    if breaching and (error_budget_remaining_pct <= 0.0 or burn_rate >= _CRITICAL_BURN_RATE):
        severity = "critical"
    else:
        severity = "warning"

    if breaching:
        headline = (
            f"SLO '{slo_name}' is breaching its {objective_pct:g}% objective "
            f"(actual {actual_pct:g}%)."
        )
    else:
        headline = (
            f"SLO '{slo_name}' is at risk — only "
            f"{error_budget_remaining_pct:g}% of its error budget remains."
        )

    actions: list[str] = []
    if target_status == "down":
        actions.append(
            f"Target '{target_name}' is currently DOWN — investigate the "
            "monitored endpoint first."
        )
    if service_name:
        team_suffix = f" (team {team_name})" if team_name else ""
        actions.append(
            f"Owning service: {service_name}{team_suffix}. Check its on-call "
            "and acknowledge an open incident, or start one if needed."
        )
    else:
        actions.append(
            "This SLA target isn't linked to a Service — link it so future "
            "recommendations route to the owning team."
        )
    actions.append(
        "Review recent deploys and configuration changes in the affected window."
    )
    if burn_rate >= _CRITICAL_BURN_RATE:
        actions.append(
            f"Error budget is burning fast (burn rate {burn_rate:g}×) — consider "
            "opening an incident to coordinate the response."
        )
    actions.append(
        "Advisory only — OpsMender will not auto-create an incident or page "
        "anyone on your behalf."
    )

    return RecommendationVerdict(severity=severity, headline=headline, actions=actions)
