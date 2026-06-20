"""Built-in Session Profile templates (v1.2 Phase 3).

A Session Profile (``WorkflowProfile``) is a saved node ordering for an AI
incident session. These templates are *starting points* an operator can load
into the editor and tweak before saving — they are never auto-created and never
change enforcement. The tier gate still governs what may execute; a profile only
chooses which phases run and in what order.

Node orders use the editor-visible phases (``observe``, ``diagnose``, ``plan``,
``tier_gate``, ``execute``, ``verify``, ``summarize``); ``recall``/``remember``
run implicitly. Every template is validated against
``validate_workflow_node_order`` by the test-suite.
"""

from __future__ import annotations

from typing import Any

SESSION_PROFILE_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "standard_assisted_response",
        "name": "Standard Assisted Response",
        "description": (
            "Full guided response — observe → diagnose → plan → tier gate → "
            "execute → verify → summarize. The balanced default for most incidents."
        ),
        "node_order": [
            "observe",
            "diagnose",
            "plan",
            "tier_gate",
            "execute",
            "verify",
            "summarize",
        ],
        "workflow_enabled": True,
    },
    {
        "key": "read_only_investigation",
        "name": "Read-only Investigation",
        "description": (
            "Investigate and diagnose only — no plan, no execution. Best run at "
            "Tier 2 (Advisory) when you want analysis without any changes."
        ),
        "node_order": ["observe", "diagnose", "summarize"],
        "workflow_enabled": False,
    },
    {
        "key": "fast_triage",
        "name": "Fast Triage",
        "description": (
            "Quickest path to a human-readable assessment — observe then "
            "summarize. No diagnosis loop or changes; ideal for first glance."
        ),
        "node_order": ["observe", "summarize"],
        "workflow_enabled": False,
    },
    {
        "key": "postmortem_builder",
        "name": "Postmortem Builder",
        "description": (
            "Reconstruct and verify what happened, then write it up — observe → "
            "diagnose → verify → summarize. No execution; good for after-the-fact "
            "reviews that feed the postmortem."
        ),
        "node_order": ["observe", "diagnose", "verify", "summarize"],
        "workflow_enabled": False,
    },
    {
        "key": "high_risk_change_review",
        "name": "High-Risk Change Review",
        "description": (
            "Full response, but intended to run at Tier 1 (Approval Required) so "
            "every change is gated by operator approval before it executes."
        ),
        "node_order": [
            "observe",
            "diagnose",
            "plan",
            "tier_gate",
            "execute",
            "verify",
            "summarize",
        ],
        "workflow_enabled": True,
    },
]


def list_session_profile_templates() -> list[dict[str, Any]]:
    """Return a copy of the built-in Session Profile templates."""
    return [dict(t) for t in SESSION_PROFILE_TEMPLATES]
