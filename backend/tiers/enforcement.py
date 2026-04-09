"""Tier enforcement layer for AI Incident Manager.

Determines whether a tool call is permitted at the active tier based on the
operation's classification from the skill definition.

Tier permission matrix (from REFERENCE.md):

    | Classification | Tier 0 | Tier 1        | Tier 2 | Tier 3        |
    |----------------|--------|---------------|--------|---------------|
    | safe           | permit | permit        | permit | advise-only   |
    | caution        | permit | permit        | permit | deny          |
    | destructive    | permit | needs-approval| deny   | deny          |
    | unknown        | deny   | deny          | deny   | deny          |

``advise-only`` means the action is surfaced as a recommendation but not
executed by the agent.  For enforcement purposes it is treated as a denial
of autonomous execution.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from backend.skills.parser import SkillDefinition


@dataclasses.dataclass
class EnforcementResult:
    """Result of a tier enforcement check."""

    permitted: bool
    classification: str  # "safe" | "caution" | "destructive" | "unknown"
    tier: int
    reason: str


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

    return EnforcementResult(
        permitted=permitted,
        classification=classification,
        tier=tier,
        reason=reason,
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
