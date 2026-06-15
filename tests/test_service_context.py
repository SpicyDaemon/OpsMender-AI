"""Unit tests for the agent service-context block (v1.2 Phase 5)."""

from __future__ import annotations

from backend.agent.service_context import format_service_context


def test_no_service_returns_empty():
    assert format_service_context(name=None) == ""
    assert format_service_context(name="") == ""
    assert format_service_context(name="   ") == ""


def test_full_block():
    out = format_service_context(
        name="checkout-api",
        priority="P1",
        description="Handles checkout and payments",
        preferred_mcp_names=["k8s-prod", "datadog"],
    )
    assert out.startswith("## Service context")
    assert "- Service: checkout-api" in out
    assert "- Priority: P1" in out
    assert "- Description: Handles checkout and payments" in out
    assert "- Preferred MCP servers: k8s-prod, datadog" in out


def test_omits_empty_optional_fields():
    out = format_service_context(name="svc", description="  ", preferred_mcp_names=[])
    assert out == "## Service context\n- Service: svc"


def test_filters_blank_mcp_names():
    out = format_service_context(
        name="svc", preferred_mcp_names=["", "  ", "real-mcp"]
    )
    assert "- Preferred MCP servers: real-mcp" in out
