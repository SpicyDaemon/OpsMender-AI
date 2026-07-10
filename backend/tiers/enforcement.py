"""Tier enforcement layer for OpsMender AI."""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.skills.parser import SkillDefinition, loads
from backend.tiers.generic_tools import is_generic_execution_tool


@dataclasses.dataclass(frozen=True)
class EnforcementResult:
    """Result of a tier enforcement check.

    ``requires_approval`` is True when the action is permitted *only* after
    human approval under the explicit operation policy or generic-execution
    guardrail. The tier gate routes these through the approval service.
    """

    permitted: bool
    classification: (
        str  # "safe" | "caution" | "destructive" | "unknown" | "generic_execution"
    )
    tier: int
    reason: str
    reversible: bool = False
    requires_approval: bool = False

    @property
    def decision(self) -> str:
        """Stable decision value for gates, logs, and API presentation."""
        if self.requires_approval:
            return "approval"
        if self.permitted:
            return "autonomous"
        if "advisory" in self.reason.lower():
            return "advisory"
        return "deny"


# AI Autonomy Tiers (3-tier model):
#   Tier 0 — Autonomous        (explicit policy + deny lists + sandbox floor)
#   Tier 1 — Approval Required (execution mode comes from explicit policy)
#   Tier 2 — Advisory Only     (DEFAULT — no remediation actions execute)
#
# Legacy Tier 3 (advise-only) is remapped to Tier 2 (see ``check`` + migration).
def normalize_tier(tier: int) -> int:
    """Map any tier value to a valid 3-tier value.

    Legacy Tier 3 (advise-only) collapses into Tier 2 (advisory only). Any
    out-of-range value is clamped to the safest tier (2 — advisory), never to
    a more permissive tier.
    """
    if tier == 3:
        return 2
    if tier in (0, 1, 2):
        return tier
    return 2


def check(
    tool_name: str,
    tier: int,
    skill_def: SkillDefinition,
) -> EnforcementResult:
    """Check whether *tool_name* is permitted at the given *tier*.

    This is a hard programmatic check — it cannot be bypassed by agent
    reasoning. Legacy Tier 3 is normalized to Tier 2 (advisory).

    Enforcement order: deny-list (always wins) → generic-execution guardrail →
    explicit operation tier policy → Tier 0 reversible floor.
    """
    tier = normalize_tier(tier)
    classification = skill_def.classify(tool_name)
    reversible = skill_def.is_reversible(tool_name)

    # 1. Deny-list ALWAYS wins, at every tier.
    if skill_def.is_denied(tool_name):
        return EnforcementResult(
            permitted=False,
            classification=classification
            if classification != "unknown"
            else "destructive",
            tier=tier,
            reason="deny-list policy match — blocked at every tier",
            reversible=reversible,
        )

    # 2. Generic command-execution guardrail. A generic tool's name does not
    #    bound what it can do, so treat conservatively unless the skill opts it
    #    out with allow_generic. (No command-payload allowlisting yet.)
    if is_generic_execution_tool(tool_name) and not skill_def.allows_generic(tool_name):
        if tier == 1:
            return EnforcementResult(
                permitted=True,
                classification="generic_execution",
                tier=tier,
                reason="generic execution tool — requires operator approval at Tier 1",
                reversible=reversible,
                requires_approval=True,
            )
        # Tier 0 (autonomous) and Tier 2 (advisory) both block generic tools.
        reason = (
            "generic execution tool blocked at Tier 2 (advisory only)"
            if tier == 2
            else "generic execution tool blocked at Tier 0 — no command-pattern "
            "allowlisting (set allow_generic in the MCP Skill to override)"
        )
        return EnforcementResult(
            permitted=False,
            classification="generic_execution",
            tier=tier,
            reason=reason,
            reversible=reversible,
        )

    # 3. Every executable operation must declare its behavior for every tier.
    #    Programmatic or persisted definitions that bypass parser validation
    #    still fail closed here.
    policy = skill_def.tier_policy(tool_name, tier)
    if policy is None:
        return EnforcementResult(
            permitted=False,
            classification=classification,
            tier=tier,
            reason=f"operation has no explicit T{tier} policy — denied",
            reversible=reversible,
        )
    if not policy.enabled or policy.mode in {"blocked", "advisory"}:
        label = "advisory — no execution" if policy.mode == "advisory" else "blocked"
        return EnforcementResult(
            permitted=False,
            classification=classification,
            tier=tier,
            reason=f"explicit T{tier} skill policy: {label}",
            reversible=reversible,
        )
    permitted = True
    requires_approval = policy.mode == "approval"
    reason = (
        f"explicit T{tier} skill policy permits autonomous execution"
        if not requires_approval
        else f"explicit T{tier} skill policy requires operator approval"
    )

    # 4. Tier 0 reversible floor. Explicit T0 policy may opt out with
    #    ``require_reversible: false``.
    if tier == 0 and permitted:
        violation = skill_def.tier0_violation_reason(tool_name)
        if violation is not None:
            permitted = False
            reason = (
                f"{classification} operation denied at Tier 0 — {violation} "
                "(sandbox floor)"
            )

    return EnforcementResult(
        permitted=permitted,
        classification=classification,
        tier=tier,
        reason=reason,
        reversible=reversible,
        requires_approval=requires_approval,
    )


def check_and_explain(
    tool_name: str,
    tier: int,
    skill_def: SkillDefinition,
) -> str:
    """Human-readable one-liner for the enforcement decision."""
    result = check(tool_name, tier, skill_def)
    status = "PERMIT" if result.permitted else "DENY"
    return f"[{status}] {tool_name} (classification={result.classification}, tier={result.tier}): {result.reason}"


async def load_skill_for_mcp_server(
    db: AsyncSession,
    org_id: uuid.UUID,
    mcp_server_id: uuid.UUID | None,
) -> SkillDefinition | None:
    """Return the effective ``SkillDefinition`` for an MCP server."""
    from backend.db.repos import SkillRepo  # local import avoids cycles

    skill = await SkillRepo.get_for_mcp_server(db, org_id, mcp_server_id)
    if skill is None:
        return None
    return loads(skill.content_md)
