"""Unit tests for built-in Session Profile templates (v1.2 Phase 3)."""

from __future__ import annotations

from backend.agent.graph import validate_workflow_node_order
from backend.workflow.templates import (
    SESSION_PROFILE_TEMPLATES,
    list_session_profile_templates,
)


def test_all_templates_have_required_fields():
    for t in SESSION_PROFILE_TEMPLATES:
        assert t["key"]
        assert t["name"]
        assert t["description"]
        assert isinstance(t["node_order"], list) and t["node_order"]


def test_template_keys_are_unique():
    keys = [t["key"] for t in SESSION_PROFILE_TEMPLATES]
    assert len(keys) == len(set(keys))


def test_every_template_node_order_is_valid():
    # Each template must pass the same validator the create route uses, so a
    # loaded template always saves cleanly.
    for t in SESSION_PROFILE_TEMPLATES:
        assert validate_workflow_node_order(t["node_order"]) == t["node_order"]


def test_expected_templates_present():
    keys = {t["key"] for t in list_session_profile_templates()}
    assert keys == {
        "standard_assisted_response",
        "read_only_investigation",
        "fast_triage",
        "postmortem_builder",
        "high_risk_change_review",
    }


def test_list_returns_copies():
    a = list_session_profile_templates()
    a[0]["name"] = "mutated"
    b = list_session_profile_templates()
    assert b[0]["name"] != "mutated"
