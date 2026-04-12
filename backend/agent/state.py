"""Shared state schema for the incident response workflow.

The ``IncidentState`` TypedDict is the single state object that flows
through every node in the LangGraph workflow.  Each node reads what it
needs and returns a partial update dict.

Design notes
------------
* Fields that *accumulate* (like ``tool_calls``) use the ``Annotated``
  + ``operator.add`` reducer so that each node's returned list is
  appended to the existing list rather than replacing it.
* Fields that are *set once* (like ``session_id``, ``tier``) have no
  reducer — last write wins (but in practice they are set at the start
  and never overwritten).
"""

from __future__ import annotations

import operator
from typing import Any, Annotated, TypedDict


class ToolCallRecord(TypedDict, total=False):
    """A single tool-call record stored in state."""

    tool_name: str
    tool_parameters: dict[str, Any]
    classification: str          # safe | caution | destructive | unknown
    permitted: bool
    result: dict[str, Any] | None
    error: str | None
    duration_ms: int | None
    block_reason: str | None


class IncidentState(TypedDict, total=False):
    """State schema for the incident response workflow.

    Every key is optional (``total=False``) because each node only
    returns the fields it updates.
    """

    # -- identity (set once at session start) --------------------------------
    session_id: str
    tier: int
    skill_definition_path: str

    # -- incident context (set by user / observe node) -----------------------
    incident_description: str

    # -- node outputs --------------------------------------------------------
    observations: str              # output of the observe node
    diagnosis: str                 # output of the diagnose node
    plan: list[dict[str, Any]]     # proposed actions from the plan node

    # -- tier gate -----------------------------------------------------------
    approved_actions: list[dict[str, Any]]   # actions that passed the gate
    blocked_actions: list[dict[str, Any]]    # actions that were blocked
    approval_requests: list[dict[str, Any]]  # approval records created at Tier 1

    # -- execution -----------------------------------------------------------
    tool_calls: Annotated[list[ToolCallRecord], operator.add]

    # -- verification & summary ---------------------------------------------
    verification: str              # output of the verify node
    summary: str                   # output of the summarize node

    # -- control flow --------------------------------------------------------
    status: str                    # active | awaiting_approval | completed | failed | timed_out
    error: str | None
