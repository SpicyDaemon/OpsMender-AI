"""Paging surface (Sprint 33+).

Pure-function algorithms (``on_call.on_call_at``, ``priority.assign_priority``)
live here so the API + workflow layers can call them deterministically and
unit-test them without a live DB.
"""

from backend.paging.channel_factory import (
    build_channel_factory,
    null_channel_factory,
)
from backend.paging.escalation import (
    HARD_INACTIVITY_TIMEOUT_SECONDS,
    SOFT_TAKEOVER_WINDOW_SECONDS,
    StepFireResult,
    cancel_chain,
    handle_ack,
    handle_force_takeover,
    handle_takeover_confirm,
    handle_takeover_request,
    select_chain_for_incident,
    start_chain,
    tick,
    tick_all_due,
)
from backend.paging.on_call import (
    OnCallContext,
    OnCallMember,
    OnCallOverride,
    on_call_at,
)
from backend.paging.priority import (
    DEFAULT_MODE_FOR,
    PRIORITY_RANK,
    PriorityAssignment,
    PriorityRuleLike,
    assign_priority,
    rule_matches,
)

__all__ = [
    "DEFAULT_MODE_FOR",
    "HARD_INACTIVITY_TIMEOUT_SECONDS",
    "OnCallContext",
    "OnCallMember",
    "OnCallOverride",
    "PRIORITY_RANK",
    "PriorityAssignment",
    "PriorityRuleLike",
    "SOFT_TAKEOVER_WINDOW_SECONDS",
    "StepFireResult",
    "assign_priority",
    "build_channel_factory",
    "cancel_chain",
    "handle_ack",
    "handle_force_takeover",
    "handle_takeover_confirm",
    "handle_takeover_request",
    "null_channel_factory",
    "on_call_at",
    "rule_matches",
    "select_chain_for_incident",
    "start_chain",
    "tick",
    "tick_all_due",
]
