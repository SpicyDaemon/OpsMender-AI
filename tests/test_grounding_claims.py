"""M1-88: the grounding correction answers claims about the incident, not words.

On 2026-10-24 eight sentences in correct live summaries drew "Correction from
the session record": circuit breakers that are "closed", a repeated facts line,
a question and a heading for checks a person should run. The fabrications the
correction exists for (2026-10-23) must still be answered.
"""

from __future__ import annotations

import pytest

from backend.agent.grounding import (
    SessionFacts,
    contradictions,
    ground_summary,
    state_line,
)

# The live round: open, held by admin, nothing ran, five calls blocked.
LIVE = SessionFacts(incident_status="open", owner_name="admin", blocked_count=5)
OPEN_UNOWNED = SessionFacts(incident_status="open", owner_name=None)

CLOSURE = "the stored incident status is open, not resolved or closed"
NO_ACTIONS = "no actions, probes or checks ran in this session"
NOT_ACKNOWLEDGED = "nobody has acknowledged the incident"

# The eight sentences from the 2026-10-24 live round, as the model wrote them.
LIVE_SENTENCES = [
    "- Verify that circuit‑breaker states for all dependencies remain closed.",
    "* Circuit breakers are currently closed",
    "- Health of each downstream service and whether their circuit breakers are open or closed",
    "- Verify circuit breaker states for each dependency; if a breaker is closed on a flaky "
    "service, consider opening it temporarily while troubleshooting",
    "- Verify that circuit breakers remain closed and latency stays within normal limits",
    "Stored incident state: status open; admin holds it. Actions executed in this session: 0; "
    "failed: 0; blocked: 5; approval requests: 0.",
    "- Whether the productpage pod itself is running normally or has crashed/restarted.",
    "**Suggested next checks (to be performed by a person):**",
]


@pytest.mark.parametrize("sentence", LIVE_SENTENCES)
def test_the_live_sentences_draw_no_correction(sentence):
    assert contradictions(sentence, LIVE) == []


def test_a_summary_made_of_the_live_sentences_is_left_alone():
    grounded, found = ground_summary("\n".join(LIVE_SENTENCES), LIVE)
    assert found == []
    assert "Correction from the session record" not in grounded


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        # The 2026-10-23 fabrications.
        (
            "A scheduled synthetic probe ran at 09:00 and returned HTTP 200.",
            [NO_ACTIONS],
        ),
        ("The alert was resolved as a false positive.", [CLOSURE]),
        ("The incident is closed.", [CLOSURE]),
        ("The incident was acknowledged by the on-call engineer.", [NOT_ACKNOWLEDGED]),
        (
            "We restarted the ratings deployment and the service was restored.",
            [CLOSURE, NO_ACTIONS],
        ),
        # A noun is not an instruction.
        (
            "The health check confirmed the service is restored.",
            [CLOSURE, NO_ACTIONS],
        ),
        ("The next probe ran at 10:00 and returned HTTP 200.", [NO_ACTIONS]),
        # Another thing named first does not cover the incident named after it.
        ("After the connection pool recovered, the incident was resolved.", [CLOSURE]),
        ("The incident was resolved after connections recovered.", [CLOSURE]),
        # A clause after a semicolon stands on its own.
        ("Verify the ratings pods; the incident is resolved.", [CLOSURE]),
        # A facts line the model changed is checked like any other text.
        (
            "Stored incident state: status resolved; nobody has acknowledged it.",
            [CLOSURE],
        ),
        ("Actions executed in this session: 2; failed: 0.", [NO_ACTIONS]),
    ],
)
def test_claims_the_record_does_not_show_are_still_corrected(summary, expected):
    assert contradictions(summary, OPEN_UNOWNED) == expected


@pytest.mark.parametrize(
    "summary",
    [
        # Instructions.
        "Monitor the error rate for 30 minutes.",
        "Continue monitoring the ratings service.",
        "- Confirm that the productpage is restored after the restart.",
        "Restart the ratings pods and then confirm the productpage is restored.",
        "I recommend checking that the ratings pods restarted cleanly.",
        "Check whether anyone has acknowledged the incident.",
        # Conditions, hedges and the future.
        "If the restart is approved, the service should be restored within minutes.",
        "The ratings pods may have restarted overnight.",
        "The restart will be performed by a person after approval.",
        "Keep paging until the incident is acknowledged.",
        "Recommended next steps: restart the ratings deployment once approved.",
        # Questions.
        "Was the incident resolved before the session started?",
        "**Has the ratings pod restarted?**",
        # Other things that close or come back.
        "The maintenance window closed at 10:00.",
        "The connection to the ratings service was restored.",
    ],
)
def test_instructions_conditions_questions_and_other_things_are_not_claims(summary):
    assert contradictions(summary, OPEN_UNOWNED) == []


@pytest.mark.parametrize("facts", [LIVE, OPEN_UNOWNED])
def test_the_stored_state_line_repeated_by_the_model_is_not_a_claim(facts):
    assert contradictions(state_line(facts), facts) == []
