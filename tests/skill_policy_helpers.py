"""Shared explicit skill policies for tests that construct definitions directly."""

from __future__ import annotations

from typing import Any

from backend.skills.parser import OperationClassification, OperationTierPolicy


def explicit_operation(
    tool: str,
    classification: str,
    **kwargs: Any,
) -> OperationClassification:
    """Build an operation with the conservative compatibility policy."""
    return OperationClassification(
        tool=tool,
        classification=classification,
        tiers={
            0: OperationTierPolicy(True, "autonomous", True),
            1: OperationTierPolicy(True, "approval"),
            2: OperationTierPolicy(False, "advisory"),
        },
        **kwargs,
    )
