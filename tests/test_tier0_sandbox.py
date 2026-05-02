"""Tests for backend.tiers.sandbox — Tier 0 allowlist enforcement."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.skills.parser import OperationClassification, SkillDefinition
from backend.tiers.sandbox import (
    Tier0Sandbox,
    Tier0SandboxViolation,
    build_sandbox_for_session,
)


def _tool(name: str):
    """Lightweight stand-in for mcp.types.Tool (only .name is read)."""
    return SimpleNamespace(name=name)


@pytest.fixture()
def mixed_skill():
    return SkillDefinition(
        version="1",
        environment="test",
        operations=[
            OperationClassification(tool="get_pods", classification="safe"),
            OperationClassification(
                tool="cordon_node",
                classification="caution",
                reversible=True,
                compensating_inverse="uncordon_node",
            ),
            OperationClassification(
                tool="uncordon_node",
                classification="caution",
                reversible=True,
                compensating_inverse="cordon_node",
            ),
            OperationClassification(tool="rollout_restart", classification="caution"),
            OperationClassification(tool="delete_pod", classification="destructive"),
        ],
    )


class TestFromSkill:
    def test_intersection_with_available_tools(self, mixed_skill):
        tools = [
            _tool(n)
            for n in ("get_pods", "cordon_node", "delete_pod", "rollout_restart")
        ]
        sandbox = Tier0Sandbox.from_skill(mixed_skill, available_tools=tools)
        assert sandbox.allowed_tool_names == frozenset({"get_pods", "cordon_node"})

    def test_unknown_server_tool_excluded(self, mixed_skill):
        """Tools the skill never mentions are treated as non-reversible."""
        tools = [_tool("get_pods"), _tool("exotic_undocumented_tool")]
        sandbox = Tier0Sandbox.from_skill(mixed_skill, available_tools=tools)
        assert "exotic_undocumented_tool" not in sandbox.allowed_tool_names

    def test_without_available_tools_uses_literal_names(self, mixed_skill):
        sandbox = Tier0Sandbox.from_skill(mixed_skill)
        assert "get_pods" in sandbox.allowed_tool_names
        assert "cordon_node" in sandbox.allowed_tool_names
        assert "rollout_restart" not in sandbox.allowed_tool_names

    def test_wildcard_literals_excluded_without_tool_list(self):
        sd = SkillDefinition(
            version="1",
            environment="test",
            operations=[
                OperationClassification(tool="describe_*", classification="safe"),
                OperationClassification(tool="get_pods", classification="safe"),
            ],
        )
        sandbox = Tier0Sandbox.from_skill(sd)
        assert sandbox.allowed_tool_names == frozenset({"get_pods"})

    def test_reversible_write_without_inverse_is_excluded(self):
        sd = SkillDefinition(
            version="1",
            environment="test",
            operations=[
                OperationClassification(
                    tool="rollout_restart",
                    classification="caution",
                    reversible=True,
                ),
                OperationClassification(tool="get_pods", classification="safe"),
            ],
        )
        sandbox = Tier0Sandbox.from_skill(
            sd,
            available_tools=[_tool("rollout_restart"), _tool("get_pods")],
        )
        assert sandbox.allowed_tool_names == frozenset({"get_pods"})


class TestFilterTools:
    def test_filters_out_non_reversible(self, mixed_skill):
        sandbox = Tier0Sandbox.from_skill(
            mixed_skill,
            available_tools=[
                _tool("get_pods"),
                _tool("cordon_node"),
                _tool("delete_pod"),
            ],
        )
        visible = sandbox.filter_tools(
            [_tool("get_pods"), _tool("delete_pod"), _tool("cordon_node")]
        )
        assert sorted(t.name for t in visible) == ["cordon_node", "get_pods"]


class TestCallToolGate:
    async def test_allowed_call_passes_through(self, mixed_skill, monkeypatch):
        sandbox = Tier0Sandbox.from_skill(
            mixed_skill, available_tools=[_tool("get_pods")]
        )
        fake_call_tool = AsyncMock(return_value="OK")
        monkeypatch.setattr("backend.tiers.sandbox.call_tool", fake_call_tool)
        session = object()

        result = await sandbox.call_tool(session, "get_pods", {"ns": "default"})
        assert result == "OK"
        fake_call_tool.assert_awaited_once_with(session, "get_pods", {"ns": "default"})

    async def test_blocked_call_raises(self, mixed_skill):
        sandbox = Tier0Sandbox.from_skill(
            mixed_skill, available_tools=[_tool("get_pods")]
        )
        with pytest.raises(Tier0SandboxViolation, match="rollout_restart"):
            await sandbox.call_tool(object(), "rollout_restart", {})

    async def test_blocked_even_if_destructive_was_marked_reversible_later(
        self, mixed_skill
    ):
        """Allowlist is frozen at construction — mutating the skill does not unlock tools."""
        sandbox = Tier0Sandbox.from_skill(
            mixed_skill, available_tools=[_tool("get_pods")]
        )
        mixed_skill.operations.append(
            OperationClassification(
                tool="delete_pod", classification="destructive", reversible=True
            )
        )
        with pytest.raises(Tier0SandboxViolation):
            await sandbox.call_tool(object(), "delete_pod", {})


class TestBuildSandboxForSession:
    async def test_uses_live_list_tools(self, mixed_skill, monkeypatch):
        fake_tools = [_tool("get_pods"), _tool("cordon_node"), _tool("delete_pod")]

        async def fake_list_tools(_session):
            return fake_tools

        monkeypatch.setattr("backend.mcp.client.list_tools", fake_list_tools)

        sandbox = await build_sandbox_for_session(object(), mixed_skill)
        assert sandbox.allowed_tool_names == frozenset({"get_pods", "cordon_node"})
