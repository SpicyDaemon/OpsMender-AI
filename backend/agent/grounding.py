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

M1-110 leaves two shapes of clause out of the no-actions check, each matched
against the whole clause so that a clause which also claims something is
read as before: a negated list of what is not configured, and a suggested
command in a code span. See ``_negated_existence`` and ``_suggested_spans``.

M1-148 shows the model what the completed calls returned. See
``tool_results_block``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from backend.agent.hygiene import addressed_to_assistant

CLOSED_STATUSES = frozenset({"resolved", "closed"})

GROUNDING_RULE = (
    "Describe only what the stored incident state, the diagnosis evidence and "
    "the listed tool results show. If no actions were executed, say so plainly "
    "and do not describe probes, checks, monitors, configuration changes or "
    "their outcomes. Never say the incident is resolved, closed, fixed, "
    "mitigated or acknowledged unless the stored incident state says so. If the "
    "evidence does not establish the cause, say the cause is unknown."
)

UNREADABLE_PLAN = "The model's plan could not be read, so no action was taken."

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
    plan_unreadable: bool = False
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
        plan_unreadable=bool(state.get("plan_unreadable")),
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


def outcome_lines(facts: SessionFacts) -> list[str]:
    """One line per tool call with its outcome from the record: ran and
    returned a result, failed, or blocked."""
    lines: list[str] = []
    for call in facts.tool_calls:
        name = call.get("tool_name") or "?"
        if not call.get("permitted", True):
            outcome = "blocked by the tier policy, did not run"
        elif call.get("error"):
            outcome = f"failed, no result: {call['error']}"
        else:
            outcome = "ok"
        lines.append(f"- {name}: {outcome}")
    return lines


def facts_block(facts: SessionFacts) -> str:
    """The facts as the summarize prompt sees them."""
    lines = [state_line(facts)]
    if facts.plan_unreadable:
        lines.append(UNREADABLE_PLAN)
    if facts.tool_calls:
        lines.append("Tool calls in this session (the only actions that happened):")
        lines.extend(outcome_lines(facts))
    else:
        lines.append(
            "No tool calls happened in this session: no probe, check, monitor or "
            "change was run by it."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# What the completed calls returned (M1-148)
# ---------------------------------------------------------------------------
#
# Finding FW-09 of the 2026-11-05 connector evaluation: a completed read
# reached the verify and summarize prompts as the line "- get_pod_logs: ok"
# and nothing of what it returned, so a cause that was only in a log line or
# a ticket the session read could not inform the diagnosis or the summary.
# ``tool_results_block`` renders an excerpt of each executed call's result
# for those two prompts. The result is the protected copy the executor
# recorded (M1-150: credentials masked, at most 64 KiB), so nothing is
# unmasked here, and the caller wraps the block as untrusted text (M1-69), so
# the model is told it is data and never instructions. Two limits bound the
# prompt: at most EXCERPT_CHARS_PER_CALL characters of one result, and at
# most EXCERPT_CHARS_PER_SESSION characters of results in one prompt. A
# result longer than its limit keeps its head and its tail around a marker
# that says how many characters were left out: a log's latest lines are at
# its end and a document's identity at its start. A call past the session
# limit is listed with its result withheld, in words that do not say it
# failed. Failed and blocked calls have no entry; the outcome lines say they
# returned nothing.
#
# A session that plans again after its reads (M1-111) went back for the
# results of its later rounds, so the session limit is spent on the latest
# round's results first; within a round, and in a session with one round,
# the calls come in their order. The entries are always listed in the order
# the calls ran.

EXCERPT_CHARS_PER_CALL = 4_000
EXCERPT_CHARS_PER_SESSION = 12_000
_PARAMETER_CHARS = 200


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(value)


def result_text(result: Any) -> str:
    """The text of a tool result as the model should read it: the text items
    of an MCP result joined by newlines, a string as it is, any other value
    as compact JSON, and nothing for no result."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        content = result["content"]
        texts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type", "text") == "text"
        ]
        joined = "\n".join(text for text in texts if text)
        return joined or f"({len(content)} content items without text)"
    return _compact_json(result)


def excerpt(text: str, limit: int) -> str:
    """At most ``limit`` characters of ``text``: all of it when it fits, else
    its head and its tail around a marker that says how many characters were
    left out."""
    if len(text) <= limit:
        return text
    # The marker is longest with the whole length in it; the count it ends
    # up holding has no more digits, so the result never exceeds the limit.
    marker = f"\n[... {len(text)} characters left out ...]\n"
    if limit <= len(marker):
        return text[:limit]
    head = (limit - len(marker)) // 2
    tail = limit - len(marker) - head
    marker = f"\n[... {len(text) - head - tail} characters left out ...]\n"
    return text[:head] + marker + text[len(text) - tail :]


def tool_results_block(
    facts: SessionFacts,
    *,
    per_call: int = EXCERPT_CHARS_PER_CALL,
    per_session: int = EXCERPT_CHARS_PER_SESSION,
) -> str:
    """What each executed call returned, in order, for the verify and
    summarize prompts; empty when nothing executed."""
    executed = facts.executed
    entries: list[str] = [""] * len(executed)
    remaining = per_session
    latest_round_first = sorted(
        range(len(executed)),
        key=lambda index: (-int(executed[index].get("round") or 0), index),
    )
    for index in latest_round_first:
        call = executed[index]
        heading = f"[{call.get('tool_name') or '?'}"
        parameters = call.get("tool_parameters")
        if parameters:
            shown = _compact_json(parameters)
            if len(shown) > _PARAMETER_CHARS:
                shown = shown[:_PARAMETER_CHARS] + "..."
            heading += f" {shown}"
        heading += "]"
        text = result_text(call.get("result"))
        if not text:
            entries[index] = f"{heading} returned no text."
            continue
        if remaining <= 0:
            entries[index] = (
                f"{heading} returned {len(text)} characters, not shown: the "
                f"session's limit of {per_session} characters of results is "
                "reached."
            )
            continue
        shown = excerpt(text, min(per_call, remaining))
        remaining -= len(shown)
        if len(shown) < len(text):
            heading += f" returned {len(text)} characters; the start and the end:"
        else:
            heading += " returned:"
        entries[index] = f"{heading}\n{shown}"
    return "\n\n".join(entries)


def addressed_in_results(facts: SessionFacts) -> list[tuple[str, list[str]]]:
    """The executed calls whose result talks to the assistant ("ignore your
    instructions", "approved by the admin"), each with the phrases found, in
    order. The first lab run of M1-148 showed why the prompt names them: the
    model did not follow such a line, but repeated it as a follow-up
    suggestion."""
    found: list[tuple[str, list[str]]] = []
    for call in facts.executed:
        phrases = addressed_to_assistant(result_text(call.get("result")))
        if phrases:
            found.append((call.get("tool_name") or "?", phrases))
    return found


def addressed_in_errors(facts: SessionFacts) -> list[tuple[str, list[str]]]:
    """The failed calls whose error text talks to the assistant, each with
    the phrases found, in order (M1-168). A provider writes that text too,
    and ``outcome_lines`` shows it to the model ("failed, no result: ..."),
    so the prompts name it as they name such a result."""
    found: list[tuple[str, list[str]]] = []
    for call in facts.failed:
        phrases = addressed_to_assistant(str(call.get("error") or ""))
        if phrases:
            found.append((call.get("tool_name") or "?", phrases))
    return found


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


# M1-110: two shapes of clause the no-actions check leaves alone. Each is
# matched against the whole clause, so a clause that goes on to claim
# something (a verb after the list, a result after the command) is not one
# of them and is read by the rules above, as on main.
#
# 1. A negated list of what is not configured (the 2026-10-27 rerun's "no
#    liveness or readiness probes, monitoring, or alerts are configured for
#    the productpage deployment"): "no", "there is no", "<the deployment>
#    has no" or "no evidence of", then items made only of the nouns and
#    describing words below, then at most a closing that says they are not
#    there and a place that starts with a preposition. The item words are
#    an allowlist, so a result ("failing", "timed out") never fits; the
#    subject and the place hold no verb, pronoun, "-ed" or "-ing" word and
#    no time ("before this session"), so no claim word stands in the
#    clause outside the items' nouns and the closing ("the deployment we
#    rolled back" does not fit).
_INFRA_NOUN = frozenset(
    "probe probes probing monitoring monitor monitors alert alerts alerting "
    "dashboard dashboards check checks metrics tracing logging slo slos sli slis "
    "liveness readiness startup".split()
)
_INFRA_MODIFIER = frozenset(
    "synthetic http https tcp grpc exec external internal uptime custom automated "
    "dedicated centralized centralised prometheus prometheus-based alertmanager "
    "grafana datadog kubernetes k8s health healthcheck black-box blackbox "
    "out-of-the-box built-in application service endpoint latency error "
    "error-rate apm basic explicit separate scheduled periodic "
    "continuous kubelet pod container cluster node log any such".split()
)
_EXISTENCE_LEAD = re.compile(
    r"^(?:(?P<subject>(?:[\w-]+ ){1,4})(?:currently )?(?:has|have|had) |"
    r"(?:there|here) (?:is|are|was|were) )?"
    r"no (?:(?:evidence|record|sign|mention)s? of (?:any )?)?"
)
_EXISTENCE_CLOSE = re.compile(
    r"^(?:(?:(?:are|is|were|was|has been|have been|had been) "
    r"(?:(?:currently|yet|ever|also) )?)?"
    r"(?:configured|defined|set up|in place|present|available|attached|"
    r"installed|wired up)|exists?|existed|appears? to exist)\b ?"
)
# A closing that says the items did not run. After a list it needs "or" or
# "nor" before the last item: "no checks, alerts, dashboards, probes ran on
# all pods" and "no dashboards, and http probes were run" can be two
# clauses run together.
_EXISTENCE_RAN = re.compile(
    r"^(?:(?:were|was|have been|has been) (?:run|performed|executed|attempted)|"
    r"ran|happened|took place)\b ?"
)
_EXISTENCE_PREP = frozenset("for on in of across at within to during".split())
_EXISTENCE_END = re.compile(r"(?: (?:at all|whatsoever|yet|today|at present|so far))+$")
_LIST_JOIN = frozenset({"or", "and", "nor"})
_DETERMINER = frozenset(
    "the a an all any each every its their our your this these those some".split()
)
# A word that may stand in the subject or the place: a plain word that is
# not a pronoun, a verb form, a claim word or a list noun.
_PLACE_WORD = re.compile(r"^[a-z0-9][\w.-]*$")
_NOT_PLACE_WORD = re.compile(
    r"^(?:i|we|you|they|he|she|it|who|which|that|whose|is|are|was|were|be|been|"
    r"has|have|had|do|does|did|will|would|can|could|should|may|might|must|no|"
    r"not|and|or|but|so|then|\w+ed|\w+ing|"
    # A past tense without "-ed" ("the pods the agent took down").
    r"took|brought|got|made|went|came|put|kept|left|sent|held|built|found|saw|"
    r"ran|began|broke|drove|gave|knew|shut|spun|tore|wrote|"
    # A time ("before this session", "previously"): what was not there then
    # may be there now, because of the session.
    r"before|until|till|since|after|previously|prior|earlier|initially|"
    r"originally|formerly|anymore|longer|now|then|yet)$"
)


def _place_word(word: str) -> bool:
    return bool(
        _PLACE_WORD.match(word)
        and not _NOT_PLACE_WORD.match(word)
        and word not in _INFRA_NOUN
    )


def _negated_existence(clause: str) -> bool:
    """Whether the whole clause is a negated list of what is not configured
    (shape 1 above)."""
    text = clause.replace("`", "").strip().rstrip(".!*_ ").strip()
    text = _EXISTENCE_END.sub("", text)
    lead = _EXISTENCE_LEAD.match(text)
    if lead is None:
        return False
    subject = (lead.group("subject") or "").split()
    if not all(_place_word(w) for w in subject):
        return False
    tokens = re.findall(r"[,/]|[\w-]+|\S", text[lead.end() :])
    i, n = 0, len(tokens)
    items, join = 0, ""
    while True:
        start = i
        while i < n and (tokens[i] in _INFRA_NOUN or tokens[i] in _INFRA_MODIFIER):
            i += 1
        if i == start:
            return False
        items += 1
        if i < n and tokens[i] in (",", "/"):
            i += 1
            join = tokens[i] if i < n and tokens[i] in _LIST_JOIN else ""
            if join:
                i += 1
            continue
        if i < n and tokens[i] in _LIST_JOIN:
            join = tokens[i]
            i += 1
            continue
        break
    rest = " ".join(tokens[i:])
    closing = _EXISTENCE_CLOSE.match(rest)
    if closing is None:
        closing = _EXISTENCE_RAN.match(rest)
        if closing is not None and items > 1 and join not in ("or", "nor"):
            return False
    place = rest[closing.end() :].split() if closing else rest.split()
    if place and place[0] not in _EXISTENCE_PREP:
        return False
    # Every claim word ends in "-ed" or "-ing", is "ran" or is a list noun,
    # and none of those is a place word, so no claim word stands in the
    # subject or the place.
    return all(w in _EXISTENCE_PREP or _place_word(w) for w in place)


# 2. A suggested command (the rerun's "use `bookinfo-lab probe details
#    productpage` and `bookinfo-lab probe logs productpage` to verify the
#    health of the service"): "use" or "run" opening the clause, perhaps
#    after a lead-in, a condition or a modal ("you can use"), then the
#    commands in spans, at most a target ("on the affected pods"), and then
#    a purpose that is an instruction ("to verify ...") or a condition. A
#    condition names what the step waits for ("once tier 2 is granted",
#    "after approval"). The condition and the purpose hold no result
#    ("returned", "were healthy", "again"), no one acting ("we", "the
#    agent") and no colon, comma, bracket or dash; the spans hold no result,
#    "#" comment, colon, comma or bracket; and no result, report or someone
#    acting comes within two clauses before or after ("it passed",
#    "productpage reported 503", "2/3 pods ready", "we pinged the pods",
#    "Output: ..."), nor a heading such as "Commands run:" above it. Also
#    a span named as the suggested or recommended command. A bare span is
#    not one: a list of commands under "Commands run:" looks the same. Only
#    a probe or monitoring noun inside those spans is left alone; the words
#    around the spans are read as before, and a past tense inside a span
#    ("`pod restarted`") is still read.
_COMMAND_NOUN = frozenset({"probe", "probes", "probing", "monitor", "monitoring"})
_RESULT_WORD = frozenset(
    "returned returns showed shows reported reports passed passes passing failed "
    "fails failing succeeded succeeds gave gives printed output outputs responded "
    "responds answered answers completed finished exited ran runs executed came "
    "says said stayed remained saw seen observed noticed found again still "
    "earlier already previously".split()
)
_SPAN_RE = r"`[^`\n]*`"
_SUGGESTION = re.compile(
    r"^(?:(?:then|next|please|also|now|first|finally|afterwards|optionally)[,\s]+)*"
    r"(?:(?P<condition>(?:when|once|after|if|before|until)\s[^;:]{1,80}?),\s*)?"
    r"(?:(?:you|we|the on-call(?: engineer)?|an? (?:operator|engineer|person|"
    r"responder)|someone|on-call)\s+(?:can|could|should|may|might|will need to|"
    r"needs? to|need to)\s+)?"
    r"(?:use|run|try|execute)\s+"
    r"(?P<commands>"
    + _SPAN_RE
    + r"(?:\s*(?:,\s*and|,\s*or|,|and|or)\s*"
    + _SPAN_RE
    + r")*)"
    r"(?:\s+(?:on|against|for|in|with|from|across|inside|within)\s+"
    r"(?P<target>[^`,;:.!]*?))?"
    r"(?:\s*,?\s*(?:first|now|next|manually|instead))?"
    r"(?:\s*,?\s*(?P<purpose>(?:to|and then|then|and|so you can|so that you can|"
    r"in order to)\s+"
    r"(?:verify|check|confirm|see|inspect|validate|review|look|watch|monitor|"
    r"compare|diagnose|make sure|ensure|find out|determine|identify|get|gather|"
    r"collect|show|list|test|read|tail)\b.*"
    r"|(?:once|when|after|if|before|until|unless)\s.*))?"
    r"\s*[.!]?$"
)
# A clause right after a suggestion that reports on it ("it passed", "the
# probe returned 200") says the command ran, so the suggestion is not one.
_REPORTED_AFTER = re.compile(
    r"^(?:(?:it|its|this|that|they|their|these|both|all|each|running it|"
    r"(?:the |its )?(?:commands?|probes?|checks?|outputs?|results?|runs?|"
    r"responses?))\b.*\b(?:"
    + "|".join(sorted(_RESULT_WORD))
    + r")\b|(?:the |its )?(?:outputs?|results?|responses?)\s*:)"
)
# A clause that reports a result whatever its subject ("productpage
# responded 200 on all pods", "health was ok").
_RESULT_VERB = re.compile(
    r"\b(?:returned|responded|answered|passed|succeeded|completed|finished|"
    r"exited|printed|came back|(?:was|were) (?:ok|okay|healthy|green|ready|up|"
    r"clean|fine|good|normal|stable))\b"
)
# What a condition waits for; anything else ("after we got 503s") may be a
# report of something that happened.
_GATE = re.compile(
    r"\b(?:tier\s*\d|approval|approved|approves|granted|grants|permission|"
    r"access)\b"
)
_CONDITION_WORD = re.compile(r"^\s*(?:once|when|after|if|before|until|unless)\b")
# Someone who acts, in the condition or the purpose ("to verify the pods the
# agent restarted", "after we got 503s"): it may report what happened.
_ACTOR = re.compile(
    r"\b(?:we|i|our|us|they|the agent|the session|opsmender|the operator|"
    r"the team|someone)\b"
)
# A clause near a suggestion that opens with someone doing something ("we
# saw HTTP 200 from all three pods", "I observed 503s").
_ACTOR_LED = re.compile(
    r"^(?:we|i|the agent|the session|opsmender|the operator|the team)\s+\w+"
)
# A heading over what was done ("Commands run:", "**Actions taken:**",
# "Steps taken this session:"): every suggestion after it may be a record
# of a step that ran.
_RUN_HEADING = re.compile(
    r"^(?:(?:commands?|actions?|steps?|checks?|probes?)\s+(?:that were\s+)?"
    r"(?:run|ran|taken|executed|performed|done|completed|applied)|executed|"
    r"performed|ran|done|completed|applied)(?:\s+[\w-]+){0,3}\s*:"
)
# Other results a neighbouring clause may report: a timeout, a ratio of
# pods, a status code, "healthy again", "are reporting".
_NEARBY_RESULT = re.compile(
    r"\b(?:timed out|\d+/\d+|http \d{3}|\d{3}s?\b(?= on| from| for)|healthy again|"
    r"back to normal|reporting|failing)\b"
)
_NAMED_SUGGESTION = re.compile(
    r"^(?:the )?(?:suggested|recommended|proposed)\s+(?:next\s+)?"
    r"(?:commands?|steps?|checks?)\s*(?:is|are|would be|:)\s*"
    r"(?P<commands>"
    + _SPAN_RE
    + r"(?:\s*(?:,\s*and|,\s*or|,|and|or)\s*"
    + _SPAN_RE
    + r")*)"
    r"\s*[.!]?$"
)


def _suggested_spans(
    clause: str, nearby: tuple[str, ...] = ()
) -> list[tuple[int, int]]:
    """Where the commands of a suggestion lie in the clause (shape 2 above),
    or nothing when the clause is not one or one of the clauses near it
    reports a result."""
    for other in nearby:
        if (
            _REPORTED_AFTER.match(other)
            or _RESULT_VERB.search(other)
            or _ACTOR_LED.match(other)
            or _RESULT_WORD & set(re.findall(r"[\w-]+", other))
            or _NEARBY_RESULT.search(other)
        ):
            return []
    for pattern in (_SUGGESTION, _NAMED_SUGGESTION):
        match = pattern.match(clause)
        if match is None:
            continue
        given = match.groupdict()
        conditions = [given.get("condition") or ""]
        if _CONDITION_WORD.match(given.get("purpose") or ""):
            conditions.append(given.get("purpose") or "")
        if any(c and not _GATE.search(c) for c in conditions):
            continue
        purpose = (given.get("purpose") or "") + " " + (given.get("condition") or "")
        commands = match.group("commands")
        words = set(re.findall(r"[\w-]+", purpose + " " + commands))
        inside = " ".join(re.findall(r"`([^`\n]*)`", commands))
        if re.search(r"[:,()]| - ", purpose) or re.search(r"#|: |, |[()]", inside):
            continue
        if (
            _ACTOR.search(purpose)
            or _RESULT_VERB.search(purpose)
            or _RESULT_WORD & words
        ):
            continue
        target = (match.groupdict().get("target") or "").split()
        for k, word in enumerate(target):
            if word in _RESULT_WORD:
                break
            if word in _EXISTENCE_PREP or _place_word(word):
                continue
            after_determiner = k > 0 and target[k - 1] in _DETERMINER
            if (
                re.fullmatch(r"\w+ed", word)
                and after_determiner
                and not (
                    _ACTION.search(word)
                    or _CLOSURE.search(word)
                    or word in _RESULT_WORD
                )
            ):
                continue
            break
        else:
            return [match.span("commands")]
    return []


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
    clauses = _clauses(text)
    after_heading = False
    for index, clause in enumerate(clauses):
        after_heading = after_heading or bool(_RUN_HEADING.match(clause))
        quiet: list[tuple[int, int]] = []
        if pattern is _ACTION:
            if _negated_existence(clause):
                continue
            if not after_heading:
                nearby = (
                    clauses[max(0, index - 2) : index] + clauses[index + 1 : index + 3]
                )
                quiet = _suggested_spans(clause, tuple(nearby))
        for match in pattern.finditer(clause):
            if match.group(0) in _COMMAND_NOUN and any(
                start <= match.start() < end for start, end in quiet
            ):
                continue
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
