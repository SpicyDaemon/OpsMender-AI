"""Tier enforcement layer for AI Incident Manager.

Determines whether a tool call is permitted at the active tier based on the
operation's classification from the skill definition.

Tier permission matrix (from REFERENCE.md):

    | Classification | Tier 0 *           | Tier 1        | Tier 2 | Tier 3        |
    |----------------|--------------------|---------------|--------|---------------|
    | safe           | permit             | permit        | permit | advise-only   |
    | caution        | permit if reversible| permit        | permit | deny          |
    | destructive    | permit if reversible| needs-approval| deny   | deny          |
    | unknown        | deny               | deny          | deny   | deny          |

``advise-only`` means the action is surfaced as a recommendation but not
executed by the agent.  For enforcement purposes it is treated as a denial
of autonomous execution.

(*) Tier 0 has a second hard gate — the operation must resolve to
``reversible=True`` in the skill definition.  This is the Tier 0 sandbox
floor: an autonomous run may only execute operations whose effects can be
undone by the rollback engine (or are read-only).  Non-reversible ops are
denied even at Tier 0.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.skills.parser import SkillDefinition, loads


@dataclasses.dataclass
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

    Tier 0 adds a second gate: the operation must clear the Tier 0
    safety floor in the skill definition.  That means it must be
    reversible and, if it is side-effecting, it must declare a
    compensating inverse. Non-compliant ops are denied at Tier 0 even
    if the classification matrix would otherwise permit them.
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
    mcp_server_id: uuid.UUID | None,
) -> SkillDefinition | None:
    """Return the effective ``SkillDefinition`` for an MCP server.

    Lookup order:

    1. A skill bound to ``mcp_server_id`` (the most recently created wins).
    2. A global skill with ``mcp_server_id IS NULL`` as the fallback.

    Returns ``None`` if neither is present — callers decide whether to
    treat that as fail-closed (deny all) or fall back to a file-based
    skill definition.
    """
    from backend.db.repos import SkillRepo  # local import avoids cycles

    skill = await SkillRepo.get_for_mcp_server(db, mcp_server_id)
    if skill is None:
        return None
    return loads(skill.content_md)
