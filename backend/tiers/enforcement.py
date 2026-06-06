"""Tier enforcement layer for OpsMender AI."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.skills.parser import SkillDefinition, loads


@dataclasses.dataclass(frozen=True)
class EnforcementResult:
    """Result of a tier enforcement check."""

    permitted: bool
    classification: str  # "safe" | "caution" | "destructive" | "unknown"
    tier: int
    reason: str
    reversible: bool = False


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
    """
    tier = normalize_tier(tier)

    classification = skill_def.classify(tool_name)
    permitted, reason = _MATRIX[classification][tier]
    reversible = skill_def.is_reversible(tool_name)

    # Tier 0 sandbox floor: only rollback-safe ops execute.
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
