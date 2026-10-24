"""M1-82: session summaries are grounded in the stored state and the session record.

The cases mirror the 2026-10-23 live run, where every summary invented probes
or monitoring and eight called an open incident resolved.
"""

from __future__ import annotations

from backend.agent.grounding import (
    GROUNDING_RULE,
    NO_ACTION_VERIFICATION,
    SessionFacts,
    contradictions,
    facts_block,
    facts_from_state,
    ground_summary,
    state_line,
)
from backend.agent.llm import StubLLM
from backend.agent.nodes import (
    DIAGNOSE_PROMPT,
    OBSERVE_PROMPT,
    SUMMARY_SECTIONS_WITH_ACTIONS,
    SUMMARY_SECTIONS_WITHOUT_ACTIONS,
    _build_summarize,
    _build_verify,
)

OPEN_UNOWNED = SessionFacts(incident_status="open", owner_name=None)
OPEN_HELD = SessionFacts(incident_status="open", owner_name="Sam Ops")
RESOLVED = SessionFacts(incident_status="resolved", owner_name="Sam Ops")
WITH_CALL = SessionFacts(
    incident_status="open",
    owner_name="Sam Ops",
    tool_calls=({"tool_name": "get_pods", "error": None, "permitted": True},),
)


def _state(**overrides):
    state = {
        "session_id": "s-1",
        "tier": 2,
        "incident_description": "Checkout API returns 500s. Created by hand; no probe ran.",
        "diagnosis": "Cause unknown from the description alone.",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# The facts the prompt sees
# ---------------------------------------------------------------------------


def test_state_line_names_the_holder_and_counts():
    line = state_line(OPEN_HELD)
    assert line.startswith("Stored incident state: status open; Sam Ops holds it.")
    assert "Actions executed in this session: 0" in line


def test_state_line_without_an_owner_says_so():
    assert "nobody has acknowledged it" in state_line(OPEN_UNOWNED)


def test_facts_block_lists_each_tool_call_and_its_outcome():
    facts = SessionFacts(
        incident_status="open",
        owner_name="Sam Ops",
        tool_calls=(
            {"tool_name": "get_pods", "error": None, "permitted": True},
            {"tool_name": "restart_pod", "error": "timeout", "permitted": True},
            {"tool_name": "delete_ns", "error": None, "permitted": False},
        ),
        blocked_count=1,
    )
    block = facts_block(facts)
    assert "- get_pods: ok" in block
    assert "- restart_pod: error=timeout" in block
    assert "- delete_ns: blocked by the tier policy, did not run" in block
    assert len(facts.executed) == 2


def test_facts_block_without_calls_says_nothing_ran():
    assert "No tool calls happened in this session" in facts_block(OPEN_UNOWNED)


def test_facts_from_state_prefers_the_stored_fields_over_session_start():
    state = _state(
        incident={"status": "open", "owner": None},
        tool_calls=[{"tool_name": "get_pods"}],
        blocked_actions=[{}],
        approval_requests=[{}, {}],
    )
    facts = facts_from_state(state, incident_status="resolved", owner_name="Sam Ops")
    assert facts.incident_status == "resolved" and facts.owner_name == "Sam Ops"
    assert len(facts.tool_calls) == 1
    assert facts.blocked_count == 1 and facts.approval_count == 2
    start = facts_from_state(state)
    assert start.incident_status == "open" and start.owner_name is None


# ---------------------------------------------------------------------------
# Contradictions and corrections (the 2026-10-23 cases)
# ---------------------------------------------------------------------------


def test_invented_probe_and_resolution_on_an_open_incident_are_corrected():
    text = (
        "The HTTP 200 probe succeeded with acceptable latency. "
        "The incident is resolved with no customer impact."
    )
    grounded, found = ground_summary(text, OPEN_UNOWNED)
    assert "the stored incident status is open, not resolved or closed" in found
    assert "no actions, probes or checks ran in this session" in found
    assert grounded.startswith(
        "Stored incident state: status open; nobody has acknowledged it."
    )
    assert grounded.endswith(
        "Statements above that say otherwise are not supported by the record."
    )
    assert text in grounded


def test_claimed_acknowledgement_without_an_owner_is_corrected():
    found = contradictions("The incident was acknowledged and monitored.", OPEN_UNOWNED)
    assert "nobody has acknowledged the incident" in found


def test_denied_acknowledgement_while_someone_holds_it_is_corrected():
    found = contradictions("The incident has not been acknowledged yet.", OPEN_HELD)
    assert found == ["Sam Ops holds the incident"]


def test_a_grounded_note_needs_no_correction():
    text = (
        "No actions were executed in this session. The cause is unknown. "
        "Current status: open; nobody has acknowledged it. "
        "Next checks for a person: look at the gateway logs."
    )
    grounded, found = ground_summary(text, OPEN_UNOWNED)
    assert found == []
    assert "Correction" not in grounded


def test_resolution_on_a_resolved_incident_is_not_a_contradiction():
    assert (
        contradictions("The incident is resolved; Sam Ops acknowledged it.", RESOLVED)
        == []
    )


def test_actions_that_ran_may_be_described():
    assert (
        contradictions("get_pods ran and confirmed three pods restarting.", WITH_CALL)
        == []
    )


def test_negated_sentences_do_not_count_as_claims():
    text = "Nothing was restarted and the incident is not resolved."
    assert contradictions(text, OPEN_UNOWNED) == []


def test_empty_summary_becomes_the_stored_state_alone():
    grounded, found = ground_summary(None, OPEN_HELD)
    assert grounded == state_line(OPEN_HELD) and found == []


# ---------------------------------------------------------------------------
# The verify and summarize nodes
# ---------------------------------------------------------------------------


def test_verify_without_actions_never_asks_the_model():
    llm = StubLLM(response="incident resolved")
    result = _build_verify(llm)(_state(tool_calls=[]))
    assert result["verification"] == NO_ACTION_VERIFICATION
    assert llm.calls == []


def test_verify_with_actions_still_asks_the_model():
    llm = StubLLM(response="looks fine")
    result = _build_verify(llm)(
        _state(tool_calls=[{"tool_name": "get_pods", "error": None, "permitted": True}])
    )
    assert result["verification"] == "looks fine"
    assert "get_pods" in llm.calls[0]


def test_summarize_without_actions_asks_for_what_is_known_not_a_root_cause():
    llm = StubLLM(response="note")
    _build_summarize(llm)(
        _state(incident={"status": "open", "owner": None}, tool_calls=[])
    )
    prompt = llm.calls[0]
    assert "No tool calls happened in this session" in prompt
    assert GROUNDING_RULE in prompt
    assert SUMMARY_SECTIONS_WITHOUT_ACTIONS in prompt
    assert SUMMARY_SECTIONS_WITH_ACTIONS not in prompt
    assert "status open; nobody has acknowledged it" in prompt


def test_summarize_with_actions_lists_them_and_the_holder():
    llm = StubLLM(response="summary")
    _build_summarize(llm)(
        _state(
            incident={"status": "open", "owner": "Sam Ops"},
            tool_calls=[{"tool_name": "get_pods", "error": None, "permitted": True}],
        )
    )
    prompt = llm.calls[0]
    assert "- get_pods: ok" in prompt
    assert "Sam Ops holds it" in prompt
    assert SUMMARY_SECTIONS_WITH_ACTIONS in prompt
    assert "(repeat the stored incident state)" in prompt


def test_observe_and_diagnose_prompts_refuse_invented_probes():
    assert (
        "Do not assume a" in OBSERVE_PROMPT
        and "probe, monitor or scheduled check" in OBSERVE_PROMPT
    )
    assert "say\nthe cause is unknown" in DIAGNOSE_PROMPT
    assert (
        "Do not invent probes, monitors, alerts, telemetry or\nhistory"
        in DIAGNOSE_PROMPT
    )
