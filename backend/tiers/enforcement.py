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


# Rows: classification → {tier: (permitted, reason)}
_MATRIX: dict[str, dict[int, tuple[bool, str]]] = {
    "safe": {
        0: (True, "safe operation — permitted at Tier 0"),
        1: (True, "safe operation — permitted at Tier 1"),
        2: (True, "safe operation — permitted at Tier 2"),
        3: (False, "Tier 3 is advise-only — agent cannot execute"),
    },
    "caution": {
        0: (True, "caution operation — permitted at Tier 0"),
        1: (True, "caution operation — permitted at Tier 1"),
        2: (True, "caution operation — permitted at Tier 2"),
        3: (False, "Tier 3 denies caution operations"),
    },
    "destructive": {
        0: (True, "destructive operation — permitted at Tier 0 (sandbox)"),
        1: (True, "destructive operation — permitted at Tier 1 (requires approval)"),
        2: (False, "Tier 2 denies destructive operations"),
        3: (False, "Tier 3 denies destructive operations"),
    },
    "unknown": {
        0: (False, "unknown operation — denied (not in skill definition)"),
        1: (False, "unknown operation — denied (not in skill definition)"),
        2: (False, "unknown operation — denied (not in skill definition)"),
        3: (False, "unknown operation — denied (not in skill definition)"),
    },
}


def check(
    tool_name: str,
    tier: int,
    skill_def: SkillDefinition,
) -> EnforcementResult:
    """Check whether *tool_name* is permitted at the given *tier*.

    This is a hard programmatic check — it cannot be bypassed by agent
    reasoning.
    """
    if tier not in (0, 1, 2, 3):
        raise ValueError(f"Invalid tier: {tier} (must be 0-3)")

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
