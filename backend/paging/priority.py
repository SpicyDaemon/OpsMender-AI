"""Priority + response-mode assignment for new incidents (Sprint 33).

``assign_priority`` runs the org's rule list, picks the first match, and
returns ``(priority, response_mode)``. An optional one-way LLM escalation
pass can bump priority up — never down. False positives are tolerable;
missed P0s are not.

Pure with respect to the inputs: caller is responsible for materializing
the rules list and (if enabled) invoking the LLM via the injected callback.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Awaitable, Callable, Iterable


# Higher rank = more urgent. Used to ensure LLM escalation is one-way.
PRIORITY_RANK: dict[str, int] = {
    "P0": 4,
    "P1": 3,
    "P2": 2,
    "P3": 1,
}

DEFAULT_MODE_FOR: dict[str, str] = {
    "P0": "page",
    "P1": "page",
    "P2": "notify",
    "P3": "auto_resolve",
}


@dataclasses.dataclass(slots=True)
class PriorityRuleLike:
    """Minimal duck-typed rule shape — ORM rows match by attribute names."""

    id: Any
    name: str
    rule_index: int
    condition: dict
    priority: str
    response_mode: str | None
    is_active: bool


@dataclasses.dataclass(slots=True)
class PriorityAssignment:
    priority: str
    response_mode: str
    matched_rule_id: Any | None
    llm_escalated: bool = False
    llm_reason: str | None = None


def _normalize_terms(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip().lower() for v in value if v is not None]
    return [str(value).strip().lower()]


def rule_matches(condition: dict, payload: dict) -> bool:
    """Return True if every key in ``condition`` matches ``payload``.

    Condition values may be a scalar or a list — list semantics is OR.
    Missing payload keys never match. Comparison is case-insensitive.
    """

    if not isinstance(condition, dict):
        return False
    for key, expected in condition.items():
        wanted = _normalize_terms(expected)
        if not wanted:
            # Empty list / None means "any value present".
            if key not in payload or payload.get(key) in (None, ""):
                return False
            continue
        actual = _normalize_terms(payload.get(key))
        if not any(v in wanted for v in actual):
            return False
    return True


def _normalize_priority(value: str | None) -> str | None:
    if not value:
        return None
    value = value.upper().strip()
    return value if value in PRIORITY_RANK else None


async def assign_priority(
    payload: dict,
    rules: Iterable[PriorityRuleLike],
    *,
    llm_escalation_enabled: bool = False,
    llm_callback: Callable[[dict, str], Awaitable[tuple[str | None, str | None]]]
    | None = None,
    fallback_priority: str = "P3",
) -> PriorityAssignment:
    """Compute priority + response_mode for an incoming incident.

    ``rules`` should already be filtered to ``is_active = True`` and sorted
    by ``rule_index`` ascending — first match wins. ``llm_callback`` is an
    async function ``(payload, current_priority) -> (new_priority, reason)``.
    The callback is only invoked when ``llm_escalation_enabled`` is True.
    """

    matched_rule_id = None
    priority = fallback_priority
    response_mode: str | None = None

    for rule in rules:
        if not rule.is_active:
            continue
        if rule_matches(rule.condition or {}, payload):
            matched_rule_id = rule.id
            priority = rule.priority
            response_mode = rule.response_mode
            break

    priority = _normalize_priority(priority) or fallback_priority
    if not response_mode:
        response_mode = DEFAULT_MODE_FOR.get(priority, "notify")

    llm_escalated = False
    llm_reason: str | None = None
    if llm_escalation_enabled and llm_callback is not None:
        try:
            llm_pri, reason = await llm_callback(payload, priority)
        except Exception:
            llm_pri, reason = None, None
        new_pri = _normalize_priority(llm_pri)
        if new_pri is not None and PRIORITY_RANK[new_pri] > PRIORITY_RANK[priority]:
            llm_escalated = True
            llm_reason = reason
            priority = new_pri
            response_mode = DEFAULT_MODE_FOR.get(priority, response_mode)

    return PriorityAssignment(
        priority=priority,
        response_mode=response_mode,
        matched_rule_id=matched_rule_id,
        llm_escalated=llm_escalated,
        llm_reason=llm_reason,
    )
