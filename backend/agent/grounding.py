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
# M1-88: a word is a claim only where the summary asserts it. After a
# condition, a hedge or the future in its clause ("whether it restarted", "if a
# breaker is closed", "to be performed by a person", "suggested next checks")
# it is not.
_CONDITION = re.compile(
    r"\b(whether|if|unless|until|should|would|could|might|may|will|to be|"
    r"next (?:steps?|checks?|actions?))\b"
)
# Nor after an instruction: an imperative at the start of the clause ("Verify
# that the breakers remain closed", "Monitor the error rate") or after "to",
# "please", "then", "and", "or", "I", "we" or "you" ("then confirm it is
# restored"). A noun ("the health check confirmed ...") is still a claim.
_DIRECTIVE = re.compile(
    r"(?:^|\b(?:to|please|then|and|or|i|we|you)\s+)"
    r"(verify|check|ensure|confirm|consider|recommend(?:ed)?|suggest(?:ed)?|"
    r"proposed?|continue|try|monitor|probe)\b"
)
# Markdown and numbering in front of a clause ("- ", "* ", "1. ", "**").
_LEAD = re.compile(r"^[\s>#*_`-]*(?:\d+[.)]\s+)?[\s*_`]*")
# Things other than the incident that close or come back: "the circuit
# breakers are closed" and "the connection was restored" say nothing about the
# incident, unless the clause names the incident after them.
_OTHER_THING = re.compile(
    r"\b(circuit|breakers?|ports?|connections?|sockets?|tickets?|pull requests?|"
    r"windows?)\b"
)
_INCIDENT = re.compile(r"\b(incidents?|alerts?|outages?|issues?|problems?)\b")
# The facts line's own count, repeated by the model: it says nothing ran.
_NOTHING_EXECUTED = re.compile(r"\bactions executed in this session:\s*0\b")


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


def _clauses(text: str) -> list[str]:
    """The clauses that can carry a claim (M1-88): sentences split at
    semicolons, without their markdown lead. Questions are left out."""
    clauses: list[str] = []
    for sentence in _sentences(text.lower()):
        if sentence.rstrip("*_) ").endswith("?"):
            continue
        for part in sentence.split(";"):
            clause = _LEAD.sub("", part.strip())
            if clause and not _NOTHING_EXECUTED.search(clause):
                clauses.append(clause)
    return clauses


def _asserted(clause: str, match: re.Match[str]) -> bool:
    """Whether the clause asserts the matched word: no negation in the few
    words before it ("not resolved", "nothing was restarted"), and no
    condition, hedge, future or instruction before it in the clause. A negation
    further back ("resolved with no customer impact") does not cancel it."""
    before = clause[: match.start()]
    if _NEGATION.search(" ".join(before.split()[-4:])):
        return False
    return not (_CONDITION.search(before) or _DIRECTIVE.search(clause[: match.end()]))


def _about_something_else(before: str) -> bool:
    """Whether a closure word follows a breaker, port, connection or ticket
    with no incident named between them."""
    things = list(_OTHER_THING.finditer(before))
    return bool(things) and not _INCIDENT.search(before[things[-1].end() :])


def _claims(
    text: str, pattern: re.Pattern[str], *, about_the_incident: bool = False
) -> bool:
    """True when the summary asserts ``pattern`` somewhere. With
    ``about_the_incident``, a closure word about another thing that closes or
    comes back (a circuit breaker, a port, a connection) is not a claim."""
    for clause in _clauses(text):
        for match in pattern.finditer(clause):
            if not _asserted(clause, match):
                continue
            if about_the_incident and _about_something_else(clause[: match.start()]):
                continue
            return True
    return False


def contradictions(summary: str, facts: SessionFacts) -> list[str]:
    """What the summary claims that the record does not show."""
    found: list[str] = []
    if not facts.closed and _claims(summary, _CLOSURE, about_the_incident=True):
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
