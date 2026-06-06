"""Tier enforcement layer for OpsMender AI."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.skills.parser import SkillDefinition, loads
from backend.tiers.generic_tools import is_generic_execution_tool


@dataclasses.dataclass(frozen=True)
class EnforcementResult:
    """Result of a tier enforcement check.

    ``requires_approval`` is True when the action is permitted *only* after
    human approval (Tier 1 destructive, or a generic-execution tool at Tier 1).
    The tier gate routes these through the approval service before execution.
    """

    permitted: bool
    classification: str  # "safe" | "caution" | "destructive" | "unknown" | "generic_execution"
    tier: int
    reason: str
    reversible: bool = False
    requires_approval: bool = False


# AI Autonomy Tiers (3-tier model):
#   Tier 0 — Autonomous       (may execute remediation, incl. destructive,
#                              within skill policy / deny lists / sandbox floor)
#   Tier 1 — Approval Required (safe/caution auto; destructive routed to the
#                              approval gate; unknown denied)
#   Tier 2 — Advisory Only     (DEFAULT — no write/remediation actions execute;
#                              read-only observation still happens in the
#                              observe node, which runs before this gate)
#
# Legacy Tier 3 (advise-only) is remapped to Tier 2 (see ``check`` + migration).
_ADVISORY_REASON = (
    "Tier 2 is advisory only — no remediation actions execute "
    "(read-only analysis and recommendations only)"
)

# Rows: classification → {tier: (permitted, reason)}
_MATRIX: dict[str, dict[int, tuple[bool, str]]] = {
    "safe": {
        0: (True, "safe operation — permitted at Tier 0 (autonomous)"),
        1: (True, "safe operation — permitted at Tier 1 (approval required)"),
        2: (False, _ADVISORY_REASON),
    },
    "caution": {
        0: (True, "caution operation — permitted at Tier 0 (autonomous)"),
        1: (True, "caution operation — permitted at Tier 1 (approval required)"),
        2: (False, _ADVISORY_REASON),
    },
    "destructive": {
        0: (True, "destructive operation — permitted at Tier 0 (autonomous, sandbox floor)"),
        1: (True, "destructive operation — permitted at Tier 1 (requires approval)"),
        2: (False, _ADVISORY_REASON),
    },
    "unknown": {
        0: (False, "unknown operation — denied (not in skill definition)"),
        1: (False, "unknown operation — denied (not in skill definition)"),
        2: (False, _ADVISORY_REASON),
    },
}


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
    tier/classification matrix → Tier 0 sandbox floor.
    """
    tier = normalize_tier(tier)
    classification = skill_def.classify(tool_name)
    reversible = skill_def.is_reversible(tool_name)

    # 1. Deny-list ALWAYS wins, at every tier.
    if skill_def.is_denied(tool_name):
        return EnforcementResult(
            permitted=False,
            classification=classification if classification != "unknown" else "destructive",
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

    # 3. Tier/classification matrix.
    permitted, reason = _MATRIX[classification][tier]

    # 4. Tier 0 sandbox floor: only rollback-safe ops execute.
    if tier == 0 and permitted:
        violation = skill_def.tier0_violation_reason(tool_name)
        if violation is not None:
            permitted = False
            reason = (
                f"{classification} operation denied at Tier 0 — {violation} "
                "(sandbox floor)"
            )

    # Destructive at Tier 1 is permitted only via the approval gate.
    requires_approval = tier == 1 and classification == "destructive" and permitted

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
