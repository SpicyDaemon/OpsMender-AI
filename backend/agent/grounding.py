"""Ground a session's model-written summary in the stored record (M1-82).

The summarize step used to see only the incident description and two earlier
model texts, and asked for a root cause, the actions taken and the current
status whatever had happened. With nothing to go on, a model filled the
sections in: the 2026-10-23 live run found all 12 summaries describing probes
or monitoring that never existed and eight calling an open incident resolved.

Two pieces fix that. ``facts_block`` renders what the record actually holds
(the stored incident status, who holds it, the tool calls that ran, what was
blocked) for the prompt, with ``GROUNDING_RULE`` beside it. ``ground_summary``
runs after the model: it puts the stored state first and appends a correction
when the text claims a resolution, an acknowledgement or actions that the
record does not show. The correction is deterministic; it never rewrites the
model's words, it answers them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CLOSED_STATUSES = frozenset({"resolved", "closed"})

GROUNDING_RULE = (
    "Describe only what the stored incident state, the diagnosis evidence and "
    "the listed tool results show. If no actions were executed, say so plainly "
    "and do not describe probes, checks, monitors, configuration changes or "
    "their outcomes. Never say the incident is resolved, closed, fixed, "
    "mitigated or acknowledged unless the stored incident state says so. If the "
    "evidence does not establish the cause, say the cause is unknown."
)

NO_ACTION_VERIFICATION = (
    "No actions were executed in this session, so there is nothing to verify. "
    "The incident state is unchanged by this session."
)

_NEGATION = re.compile(
    r"\b(no|not|none|nothing|nobody|no one|never|without|unknown|unverified|"
    r"unassigned|unowned|cannot|couldn't|could not|didn't|did not|wasn't|was not|"
    r"weren't|were not|isn't|is not|aren't|are not|unacknowledged|unresolved)\b"
)
_CLOSURE = re.compile(r"\b(resolved|closed|fixed|mitigated|remediated|restored)\b")
_ACKNOWLEDGED = re.compile(r"\backnowledged\b")
_NOT_ACKNOWLEDGED = re.compile(
    r"\b(unacknowledged|not (?:been )?acknowledged|no one has acknowledged|"
    r"nobody has acknowledged|awaiting acknowledg|remains unacknowledged)"
)
_ACTION = re.compile(
    r"\b(probe[ds]?|probing|monitor(?:ed|ing)?|ran|executed|performed|restarted|"
    r"scaled|rolled back|applied|deployed|queried|collected|checked|verified|"
    r"confirmed|reconfigured|disabled|enabled|patched|redeployed)\b"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class SessionFacts:
    """What the record holds about the incident and the session."""

    incident_status: str | None
    owner_name: str | None
    tool_calls: tuple[dict[str, Any], ...] = ()
    blocked_count: int = 0
    approval_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def acknowledged(self) -> bool:
        return bool(self.owner_name)

    @property
    def closed(self) -> bool:
        return (self.incident_status or "").lower() in CLOSED_STATUSES

    @property
    def executed(self) -> list[dict[str, Any]]:
        """Calls that ran and returned a result."""
        return [
            call
            for call in self.tool_calls
            if call.get("permitted", True) and not call.get("error")
        ]

    @property
    def failed(self) -> list[dict[str, Any]]:
        """Calls the tier allowed that failed or that the server rejected."""
        return [
            call
            for call in self.tool_calls
            if call.get("permitted", True) and call.get("error")
        ]


def facts_from_state(
    state: dict[str, Any],
    *,
    incident_status: str | None = None,
    owner_name: str | None = None,
) -> SessionFacts:
    """Build the facts from an agent state, with the stored incident fields
    supplied by the caller (the state only knows them as of session start)."""
    incident = state.get("incident") or {}
    status = incident_status if incident_status is not None else incident.get("status")
    owner = owner_name if owner_name is not None else incident.get("owner")
    return SessionFacts(
        incident_status=status,
        owner_name=owner or None,
        tool_calls=tuple(state.get("tool_calls") or ()),
        blocked_count=len(state.get("blocked_actions") or ()),
        approval_count=len(state.get("approval_requests") or ()),
    )


def state_line(facts: SessionFacts) -> str:
    """One sentence with the stored incident state, used before the summary."""
    status = facts.incident_status or "unknown"
    holder = (
        f"{facts.owner_name} holds it"
        if facts.acknowledged
        else "nobody has acknowledged it"
    )
    return (
        f"Stored incident state: status {status}; {holder}. "
        f"Actions executed in this session: {len(facts.executed)}; "
        f"failed: {len(facts.failed)}; blocked: {facts.blocked_count}; "
        f"approval requests: {facts.approval_count}."
    )


def facts_block(facts: SessionFacts) -> str:
    """The facts as the summarize prompt sees them."""
    lines = [state_line(facts)]
    if facts.tool_calls:
        lines.append("Tool calls in this session (the only actions that happened):")
        for call in facts.tool_calls:
            name = call.get("tool_name") or "?"
            if not call.get("permitted", True):
                outcome = "blocked by the tier policy, did not run"
            elif call.get("error"):
                outcome = f"failed, no result: {call['error']}"
            else:
                outcome = "ok"
            lines.append(f"- {name}: {outcome}")
    else:
        lines.append(
            "No tool calls happened in this session: no probe, check, monitor or "
            "change was run by it."
        )
    return "\n".join(lines)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text) if part.strip()]


def _claims(text: str, pattern: re.Pattern[str]) -> bool:
    """True when ``pattern`` appears as an affirmative claim: a match whose
    preceding few words carry no negation ("not resolved", "nothing was
    restarted", "nobody has acknowledged"). A negation elsewhere in the
    sentence ("resolved with no customer impact") does not cancel it."""
    for sentence in _sentences(text.lower()):
        for match in pattern.finditer(sentence):
            before = sentence[: match.start()].split()[-4:]
            if not _NEGATION.search(" ".join(before)):
                return True
    return False


def contradictions(summary: str, facts: SessionFacts) -> list[str]:
    """What the summary claims that the record does not show."""
    found: list[str] = []
    if not facts.closed and _claims(summary, _CLOSURE):
        found.append(
            f"the stored incident status is {facts.incident_status or 'unknown'}, "
            "not resolved or closed"
        )
    if not facts.acknowledged and _claims(summary, _ACKNOWLEDGED):
        found.append("nobody has acknowledged the incident")
    if facts.acknowledged and _NOT_ACKNOWLEDGED.search(summary.lower()):
        found.append(f"{facts.owner_name} holds the incident")
    if not facts.executed and _claims(summary, _ACTION):
        found.append(
            "every tool call in this session failed; none returned a result"
            if facts.failed
            else "no actions, probes or checks ran in this session"
        )
    return found


def ground_summary(summary: str | None, facts: SessionFacts) -> tuple[str, list[str]]:
    """Return the summary with the stored state first and any correction last."""
    text = (summary or "").strip()
    found = contradictions(text, facts) if text else []
    parts = [state_line(facts)]
    if text:
        parts.append(text)
    if found:
        parts.append(
            "Correction from the session record: "
            + "; ".join(found)
            + ". Statements above that say otherwise are not supported by the record."
        )
    return "\n\n".join(parts), found
