"""Tier 0 MCP sandbox — spawn-time allowlist enforcement.

Sprint 17 (sandbox pillar).

Design decision (see docs/REFERENCE.md → "Tier 0 sandbox"):

    For Tier 0 sessions we wrap every MCP interaction with a spawn-time
    allowlist derived from the skill definition.  Only tools that clear
    the Tier 0 safety floor survive the wrapper: reads are allowed, but
    side-effecting writes must be both reversible and equipped with a
    compensating inverse. Two gates guard execution:

    1. ``build_allowlist()`` runs at session start against the server's
       ``list_tools()``.  Non-compliant tools are filtered out *before*
       the agent ever sees them — the LLM can't plan what it can't see.
    2. ``sandboxed_call_tool()`` is the runtime gate.  Any direct MCP
       tool invocation that bypasses ``audited_tool_call`` still trips
       this wall: an off-allowlist name raises ``Tier0SandboxViolation``.

The sandbox is tier-gate-native and composes with the existing skill +
tier enforcement in ``backend.tiers.enforcement.check``.  It adds no
infra-level containment (cgroups, containers) — that would require host
privileges AIM can't assume across Docker / binary / local installs.

Usage::

    from backend.tiers.sandbox import Tier0Sandbox

    sandbox = Tier0Sandbox.from_skill(skill_def)
    visible_tools = sandbox.filter_tools(await list_tools(session))
    # ... agent plans against visible_tools only ...
    await sandbox.call_tool(session, "cordon_node", {"node": "n1"})
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable

from mcp import ClientSession
from mcp.types import CallToolResult, Tool

from backend.mcp.client import call_tool
from backend.skills.parser import SkillDefinition


class Tier0SandboxViolation(RuntimeError):
    """Raised when a Tier 0 session attempts to call a non-allowlisted tool.

    Always fail-closed: if a tool isn't on the allowlist, we don't guess
    its reversibility — we refuse.
    """


@dataclasses.dataclass
class Tier0Sandbox:
    """A frozen, per-session allowlist derived from a skill definition.

    The allowlist is computed once at session start and never mutated
    thereafter.  A reload of the skill definition mid-session does not
    affect the running sandbox — same principle as the Sprint 12 D-017
    decision (models are per-session-stable).
    """

    allowed_tool_names: frozenset[str]

    # -- construction -------------------------------------------------------

    @classmethod
    def from_skill(
        cls,
        skill_def: SkillDefinition,
        *,
        available_tools: Iterable[Tool] | None = None,
    ) -> "Tier0Sandbox":
        """Build a sandbox from a skill definition.

        If ``available_tools`` is supplied (the result of an MCP
        ``list_tools`` call), the allowlist is the intersection of that
        set with the reversible ops in the skill.  This is the
        recommended path — it avoids carrying wildcard patterns into the
        runtime check.

        If ``available_tools`` is omitted we fall back to the literal
        tool names declared in the skill (wildcards are preserved
        verbatim — callers should prefer the tool-list form).
        """
        if available_tools is not None:
            allowed: set[str] = {
                tool.name
                for tool in available_tools
                if skill_def.is_tier0_safe(tool.name)
            }
        else:
            allowed = {
                op.tool
                for op in skill_def.operations
                if skill_def.is_tier0_safe(op.tool)
                and "*" not in op.tool
                and "?" not in op.tool
            }
        return cls(allowed_tool_names=frozenset(allowed))

    # -- runtime gates ------------------------------------------------------

    def is_allowed(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tool_names

    def filter_tools(self, tools: Iterable[Tool]) -> list[Tool]:
        """Return only the tools that survive the sandbox allowlist.

        Use this before exposing the MCP tool catalog to the agent — the
        LLM should never see a tool it can't actually invoke.
        """
        return [tool for tool in tools if self.is_allowed(tool.name)]

    async def call_tool(
        self,
        session: ClientSession,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Proxy for :func:`backend.mcp.client.call_tool` with an allowlist check.

        Raises ``Tier0SandboxViolation`` if ``tool_name`` is not on the
        allowlist.  This is the last-chance gate — ``audited_tool_call``
        already consults the tier matrix, but any caller bypassing the
        audited path still trips this wall at Tier 0.
        """
        if not self.is_allowed(tool_name):
            raise Tier0SandboxViolation(
                f"Tool '{tool_name}' is not on the Tier 0 sandbox allowlist "
                f"(rollback-safe ops only). Allowed: "
                f"{sorted(self.allowed_tool_names) or '<empty>'}"
            )
        return await call_tool(session, tool_name, arguments or {})


async def build_sandbox_for_session(
    session: ClientSession,
    skill_def: SkillDefinition,
) -> Tier0Sandbox:
    """Convenience helper — list tools on an open session and build a sandbox.

    Kept as a module-level free function so callers (CLI ``aim run``,
    API session runner) don't have to import ``list_tools`` separately.
    """
    from backend.mcp.client import list_tools

    tools = await list_tools(session)
    return Tier0Sandbox.from_skill(skill_def, available_tools=tools)
