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


# M1-110 (F1 of the 2026-10-27 rerun): a correct P0 Tier 2 summary drew the
# no-actions correction from a negated list ("monitoring" more than four words
# after "no") and from "probe" inside a suggested command. Two shapes of
# clause are now left out of the no-actions check, each matched against the
# whole clause. These are the sentences of the reviews' corpus that main
# corrects and the branch does not; every one is a correct thing to say about
# a session in which nothing ran.
NOTHING_RAN = [
    "- No liveness or readiness probes, monitoring, or alerts are configured for the productpage deployment.",
    "No alerts, dashboards, or uptime monitoring are configured for productpage.",
    "No alerts, dashboards, probes or monitoring at all.",
    "No alerts, dashboards, probes, or monitoring on the ratings deployment.",
    "No automated liveness or readiness probes, monitoring, or alerts are configured.",
    "No centralized monitoring, alerting, or uptime probes are configured.",
    "No dedicated liveness or readiness probes, monitoring, or alerts are configured for productpage.",
    "No liveness or readiness probes, alerts, or monitoring for productpage.",
    "No liveness or readiness probes, alerts, or monitoring of any kind.",
    "No liveness or readiness probes, alerts, or monitoring on productpage.",
    "No liveness or readiness probes, external uptime monitoring, or alerts are configured.",
    "No liveness or readiness probes, monitoring, or alerts appear to exist.",
    "no liveness or readiness probes, monitoring, or alerts are configured",
    "No liveness or readiness probes, monitoring, or alerts are configured for the productpage deployment.",
    "No liveness or readiness probes, monitoring, or alerts are configured for this service.",
    "No liveness or readiness probes, monitoring, or alerts are configured on either deployment.",
    "No liveness or readiness probes, monitoring, or alerts are configured.",
    "No liveness or readiness probes, monitoring, or alerts configured.",
    "No liveness or readiness probes, monitoring, or alerts today.",
    "No liveness or readiness probes, monitoring, or alerts whatsoever.",
    "No liveness or readiness probes, monitoring, or alerts yet.",
    "No liveness or readiness probes, monitoring, or alerts!",
    "No liveness or readiness probes, nor monitoring, nor alerts are configured.",
    "No liveness or readiness probes, Prometheus monitoring, or Alertmanager alerts are configured.",
    "No liveness or readiness probes, Prometheus-based monitoring, or alerts are configured.",
    "No liveness or readiness probes, synthetic monitoring, uptime checks or alerts are configured.",
    "No liveness or readiness probes/monitoring/alerts exist for ratings.",
    "No liveness probes, readiness probes, or uptime monitoring were run in this session.",
    "No metrics, tracing, logging, dashboards or monitoring are configured.",
    "No out-of-the-box liveness or readiness probes, monitoring, or alerts are configured.",
    "No probes or monitoring were performed.",
    "No startup, liveness or readiness probes, monitoring, or alerts are configured.",
    "No synthetic checks, alerts, dashboards, or monitoring are present.",
    "No synthetic checks, alerts, dashboards, or monitoring exist for this service.",
    "No synthetic checks, alerts, dashboards, or monitoring for this service.",
    "No synthetic checks, alerts, dashboards, or monitoring have been set up.",
    "No synthetic checks, alerts, dashboards, or monitoring were ever set up.",
    "No synthetic checks, alerts, dashboards, or monitoring were run in this session.",
    "No synthetic checks, alerts, dashboards, or monitoring were set up.",
    "No synthetic checks, alerts, dashboards, or probes were run on all pods.",
    "No synthetic checks, alerts, dashboards, or probing exist for this service.",
    "No synthetic checks, alerts, dashboards, probes are configured on all pods.",
    "No synthetic external uptime http monitoring exists for the service.",
    "No synthetic http probes or custom uptime monitoring were run.",
    "The on-call engineer can use `bookinfo-lab probe details productpage` to verify the health of the service.",
    "The productpage deployment currently has no liveness or readiness probes, monitoring, or alerts configured.",
    "The productpage deployment has no liveness or readiness probes, alerts, or monitoring at present.",
    "The recommended command is `bookinfo-lab probe details productpage`.",
    "The recommended command is `bookinfo-lab probe details`.",
    "The service has no liveness or readiness probes, alerts, or monitoring.",
    "The suggested command is `bookinfo-lab probe details productpage`.",
    "The suggested command is `bookinfo-lab probe details`.",
    "There are no Grafana dashboards, and HTTP liveness probes are configured on all pods.",
    "There is no evidence of any liveness or readiness probes, monitoring, or alerts.",
    "There is no evidence of probes, checks, or monitoring in this session.",
    "There is no liveness or readiness probe, monitoring, or alert configured.",
    "There were no liveness or readiness probes or monitoring",
    "There were no liveness or readiness probes or monitoring.",
    "There were no liveness or readiness probes or monitoring; the pods are healthy.",
    "1. Run `bookinfo-lab probe details productpage` to verify the health.",
    "1. Run `bookinfo-lab probe details` to verify the pods restarted.",
    "Afterwards run `bookinfo-lab probe details` to verify the pods restarted.",
    "An operator can run `bookinfo-lab probe details ratings` after approval.",
    "Next, run `bookinfo-lab probe details productpage` to see the pod health.",
    "Once Tier 2 is granted, run `bookinfo-lab probe details productpage`.",
    "Run `bookinfo-lab monitor productpage` to verify.",
    "Run `bookinfo-lab probe details productpage` and `bookinfo-lab probe logs productpage` to verify.",
    "Run `bookinfo-lab probe details productpage` first, then confirm the pods restarted.",
    "Run `bookinfo-lab probe details productpage` in the affected namespace to verify the rollout.",
    "Run `bookinfo-lab probe details productpage` once Tier 2 is granted.",
    "Run `bookinfo-lab probe details` and then confirm the pods restarted.",
    "Run `bookinfo-lab probe details` first, then confirm the pods restarted.",
    "Run `bookinfo-lab probe details` to verify.",
    "use `bookinfo-lab probe details productpage` and `bookinfo-lab probe logs productpage` to verify the health of the service",
    "Use `bookinfo-lab probe details productpage` and `bookinfo-lab probe logs productpage` to verify the health of the service.",
    "Use `bookinfo-lab probe details productpage` and `bookinfo-lab probe logs productpage` to verify the pod health.",
    "Use `bookinfo-lab probe details productpage` on the affected pods to verify the fix.",
    "Use `bookinfo-lab probe details productpage` to make sure the pods are healthy.",
    "Use `bookinfo-lab probe details productpage`, `bookinfo-lab probe logs productpage` and `kubectl get events -n bookinfo` to verify.",
    "Use `bookinfo-lab probe details` on the affected pods to verify.",
    "Use `bookinfo-lab probe details` to verify the pods restarted.",
    "Use `bookinfo-lab probe details` to verify.",
    "When Tier 2 is approved, run `bookinfo-lab probe details productpage` to verify the pods.",
    "You can use `bookinfo-lab probe details productpage` to verify the fix.",
    "You can use `bookinfo-lab probe details` to verify.",
]


@pytest.mark.parametrize("sentence", NOTHING_RAN)
def test_a_negated_list_or_a_suggested_command_draws_no_correction(sentence):
    assert contradictions(sentence, LIVE) == []
    assert contradictions(sentence, OPEN_UNOWNED) == []


# Claims shaped like those two clauses: a verb or a result after the list, a
# result after the command, a command named under "Commands run:", a result
# in the next clause. Each is corrected exactly as main corrects it.
@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (
            "No synthetic checks, alerts or dashboards, probes passed on all pods.",
            [NO_ACTIONS],
        ),
        (
            "No probes or monitoring are configured, so we restarted the pods.",
            [NO_ACTIONS],
        ),
        ("No probes or monitoring for the pods we restarted.", [NO_ACTIONS]),
        ("Zero probes failed.", [NO_ACTIONS]),
        ("Zero of three probes returned errors.", [NO_ACTIONS]),
        (
            "No alerts or monitoring dashboards were configured, probes returned 200.",
            [NO_ACTIONS],
        ),
        ("The agent restarted the pod and has no probes.", [NO_ACTIONS]),
        (
            "Monitoring shows no probes configured, and the pods restarted.",
            [NO_ACTIONS],
        ),
        ("Resolved, no probes or monitoring configured.", [CLOSURE]),
        (
            "No liveness or readiness probes, monitoring, or alerts are configured, and the pods were restarted.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring shows p99 under 200 ms.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring dashboards show p99 under 200 ms.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts are configured for the pods that restarted.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts are configured by the agent, which restarted the pods.",
            [NO_ACTIONS],
        ),
        (
            "There is no liveness or readiness probe, but monitoring caught the error.",
            [NO_ACTIONS],
        ),
        (
            "There were no liveness or readiness probes or monitoring, so the agent probed the endpoints.",
            [NO_ACTIONS],
        ),
        (
            "The deployment has no alerts and the readiness probe returned 200.",
            [NO_ACTIONS],
        ),
        (
            "No pager alerts or customer tickets, monitoring stayed green all night.",
            [NO_ACTIONS],
        ),
        (
            "No pager alerts or customer tickets, probes succeeded on all pods.",
            [NO_ACTIONS],
        ),
        ("No errors, the readiness probes were executed at 10:00.", [NO_ACTIONS]),
        (
            "No liveness or readiness probes, monitoring, or alerts were configured before the agent deployed them.",
            [NO_ACTIONS],
        ),
        (
            "No synthetic checks, alerts, dashboards or monitoring were missing after the agent patched the deployment.",
            [NO_ACTIONS],
        ),
        ("Run `bookinfo-lab probe details productpage` returned 200.", [NO_ACTIONS]),
        (
            "Run `bookinfo-lab probe details productpage` on all pods returned 200.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` on the pods and it returned 200.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify: it returned 200.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify, it returned 200 on all pods.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify the health; it passed.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` once more: 3/3 ready.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` after the restart, it returned 200.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` and `kubectl get pods`, which returned 200.",
            [NO_ACTIONS],
        ),
        (
            "Then run `bookinfo-lab probe details productpage` (returned 200).",
            [NO_ACTIONS],
        ),
        (
            "After `bookinfo-lab probe details productpage` returned 200, run `kubectl get pods`.",
            [NO_ACTIONS],
        ),
        (
            "We ran `bookinfo-lab probe details productpage` to verify the health.",
            [NO_ACTIONS],
        ),
        (
            "I used `bookinfo-lab probe details productpage` to verify the health.",
            [NO_ACTIONS],
        ),
        (
            "Ran `bookinfo-lab probe details productpage` to verify the health.",
            [NO_ACTIONS],
        ),
        (
            "Use of `bookinfo-lab probe details productpage` showed 3/3 ready.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify the health, and it showed 3/3 ready.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` and then confirm the result: 3/3 ready.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify the health, which succeeded.",
            [NO_ACTIONS],
        ),
        ("Run `bookinfo-lab probe details productpage` until it passed.", [NO_ACTIONS]),
        (
            "Run `bookinfo-lab probe details productpage` to check the pods, which came back healthy.",
            [NO_ACTIONS],
        ),
        (
            "The suggested command `bookinfo-lab probe details productpage` returned 200.",
            [NO_ACTIONS],
        ),
        (
            "The suggested command is `bookinfo-lab probe details productpage`: 3/3 ready.",
            [NO_ACTIONS],
        ),
        (
            "The recommended check `bookinfo-lab probe details productpage` passed.",
            [NO_ACTIONS],
        ),
        (
            "Commands run:\n- `bookinfo-lab probe details productpage`\n- `bookinfo-lab probe logs productpage`",
            [NO_ACTIONS],
        ),
        ("Executed:\n`bookinfo-lab probe details productpage`", [NO_ACTIONS]),
        ("- `bookinfo-lab probe details productpage` (returned 200)", [NO_ACTIONS]),
        ("- `bookinfo-lab probe details productpage`: 3/3 ready", [NO_ACTIONS]),
        ("`bookinfo-lab probe details productpage` returned 200.", [NO_ACTIONS]),
        (
            "`bookinfo-lab probe details productpage` was run and returned no errors.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` on the restarted pods to verify, which passed.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` on pods restarted by the agent.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` on the pods the agent restarted.",
            [NO_ACTIONS],
        ),
        (
            "Then run `bookinfo-lab probe details productpage`, done, the pods restarted.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage`; the probe confirmed all pods ready.",
            [NO_ACTIONS],
        ),
        (
            "You can use `bookinfo-lab probe details productpage` to verify, as the probe returned 200 earlier.",
            [NO_ACTIONS],
        ),
        (
            "When the pods restarted, run `bookinfo-lab probe details productpage`.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` on the pods responded with 200.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` on the pods responded.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` on all pods returns 200.",
            [NO_ACTIONS],
        ),
        ("We restarted the pods and have no probes.", [NO_ACTIONS]),
        ("No customer impact, the agent ran the probe.", [NO_ACTIONS]),
        ("No alerts, the agent ran probes on all pods.", [NO_ACTIONS]),
        (
            "No synthetic checks, alerts, dashboards, probes ran on all pods.",
            [NO_ACTIONS],
        ),
        (
            "No synthetic checks, alerts, dashboards, monitoring happened to catch it.",
            [NO_ACTIONS],
        ),
        ("Run commands: `bookinfo-lab probe details productpage`.", [NO_ACTIONS]),
        (
            "When monitoring caught the error, run `kubectl get pods` to verify.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts are configured for ratings and productpage answered 200.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts are configured for productpage answered with 200.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts are configured for the job the agent ran.",
            [NO_ACTIONS],
        ),
        ("The job we ran has no probes.", [NO_ACTIONS]),
        (
            "Use `bookinfo-lab probe details productpage` to confirm the state we restored.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` on productpage while it is answering 200.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to confirm the fix we shipped.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage # all green` to verify.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts are configured for the pods we watch.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to confirm it still works.",
            [NO_ACTIONS],
        ),
        (
            "After the rollback, run `bookinfo-lab probe details productpage` to confirm the fix.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` after the rollback.",
            [NO_ACTIONS],
        ),
        (
            "Once Tier 2 was granted and productpage answered 200, run `bookinfo-lab probe details productpage` to verify.",
            [NO_ACTIONS],
        ),
        (
            "Once Tier 2 approval covers monitoring, run `kubectl get pods` to verify.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the health of the service. We saw HTTP 200 from all three pods.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the health of the service. I observed 503s on two of the three pods.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts were configured for productpage before this session.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts existed for the productpage deployment until this session.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts were in place for productpage before the change.",
            [NO_ACTIONS],
        ),
        (
            "Previously the productpage deployment had no liveness or readiness probes, monitoring, or alerts.",
            [NO_ACTIONS],
        ),
        (
            "No other liveness or readiness probes, monitoring, or alerts were run on productpage.",
            [NO_ACTIONS],
        ),
        (
            "No additional liveness or readiness probes or monitoring were run on productpage.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the health of the service. We pinged all three pods.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the health. Ask the on-call first. It returned 200 on all pods.",
            [NO_ACTIONS],
        ),
        (
            "The pods we deleted have no liveness or readiness probes, monitoring, or alerts.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts exist on the pods the agent took down.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts exist on the pods opsmender brought back.",
            [NO_ACTIONS],
        ),
        (
            "There is no Grafana dashboard, and HTTP probes were run on all three pods during this session.",
            [NO_ACTIONS],
        ),
        (
            "There are no alerts or dashboards, and HTTP probes were executed on all three productpage pods.",
            [NO_ACTIONS],
        ),
        (
            "The deployment has no Grafana dashboards, and synthetic HTTP probes were performed on every pod.",
            [NO_ACTIONS],
        ),
        (
            "There are no Grafana dashboards, and Prometheus monitoring ran on all pods.",
            [NO_ACTIONS],
        ),
        ("No grafana alerts and liveness probes ran on every pod.", [NO_ACTIONS]),
        (
            "Use `bookinfo-lab probe details productpage` to check the endpoints and all three were healthy.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to check health and health was ok.",
            [NO_ACTIONS],
        ),
        (
            "You can use `bookinfo-lab probe details productpage` to confirm productpage and every pod was ready.",
            [NO_ACTIONS],
        ),
        (
            "After we got 503s from productpage, run `bookinfo-lab probe details productpage` to verify the health.",
            [NO_ACTIONS],
        ),
        (
            "After the agent pinged productpage, use `bookinfo-lab probe details productpage` to check the health.",
            [NO_ACTIONS],
        ),
        (
            "After OpsMender curled productpage, run `bookinfo-lab probe details productpage` to see the pod health.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to confirm the 200 the agent got from each pod.",
            [NO_ACTIONS],
        ),
        (
            "Productpage responded 200 on all three pods. Use `bookinfo-lab probe details productpage` to see the details.",
            [NO_ACTIONS],
        ),
        (
            "It passed on all three pods; run `bookinfo-lab probe details productpage` to see the details.",
            [NO_ACTIONS],
        ),
        (
            "Commands run:\n1. Use `bookinfo-lab probe details productpage` to verify the health.",
            [NO_ACTIONS],
        ),
        (
            "**Actions taken:**\n- Run `bookinfo-lab probe details productpage` to verify the health.",
            [NO_ACTIONS],
        ),
        (
            "Re-run `bookinfo-lab probe details productpage` to verify the health.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage (3/3 ready)` to verify.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage: 3/3 ready, 0 restarts` to verify.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the pods the agent restarted.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify. Health was ok.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify (all pods healthy).",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify - all pods healthy.",
            [NO_ACTIONS],
        ),
        (
            "No synthetic checks, alerts, dashboards, probes found errors on all pods.",
            [NO_ACTIONS],
        ),
        (
            "No pager alerts, uptime dashboards, monitoring found the memory leak.",
            [NO_ACTIONS],
        ),
        ("No alerts were found during probing of all pods.", [NO_ACTIONS]),
        ("No alerts were found from probing the pods.", [NO_ACTIONS]),
        (
            "Run `bookinfo-lab probe details productpage` to verify. Everything passed.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify. Returned 200 on all pods.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify. 3/3 pods passed.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify. Result: 3/3 ready.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify, the agent restarted the pods.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to check the rollout, which the agent applied.",
            [NO_ACTIONS],
        ),
        ("No alerts or dashboards are configured: probing saw 3 errors.", [NO_ACTIONS]),
        (
            "No pager alerts or dashboards, the readiness probes are green on all pods.",
            [NO_ACTIONS],
        ),
        (
            "No alerts or dashboards, the agent saw 3 green readiness probes.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` and the pods recovered.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify, it failed on two pods.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify, which reported 3/3 ready.",
            [NO_ACTIONS],
        ),
        ("Executed commands: `bookinfo-lab probe details productpage`.", [NO_ACTIONS]),
        ("Run `kubectl rollout status` and `echo restarted` to verify.", [NO_ACTIONS]),
        (
            "Run `bookinfo-lab probe details productpage` to verify the health. It passed.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the pods.\nThe probe returned 200 on all pods.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify the health; the output showed 3/3 ready.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` to verify the health. Both commands succeeded.",
            [NO_ACTIONS],
        ),
        ("Zero readiness probes timed out across all productpage pods.", [NO_ACTIONS]),
        ("Zero liveness probes errored during the incident window.", [NO_ACTIONS]),
        ("Zero probe timeouts in the last hour.", [NO_ACTIONS]),
        ("Zero probe failures occurred on productpage.", [NO_ACTIONS]),
        ("The probe output contains no failing checks.", [NO_ACTIONS]),
        ("The probe sweep had zero failing checks.", [NO_ACTIONS]),
        ("The liveness probe logs contain no errored readiness checks.", [NO_ACTIONS]),
        (
            "We found no failing liveness or readiness probes on productpage.",
            [NO_ACTIONS],
        ),
        (
            "This session found no unhealthy liveness or readiness probes on any productpage pod.",
            [NO_ACTIONS],
        ),
        (
            "No failing liveness or readiness probes were found on productpage.",
            [NO_ACTIONS],
        ),
        (
            "No http liveness or readiness probe failures occurred during the rollout.",
            [NO_ACTIONS],
        ),
        ("No http liveness or readiness probes timed out.", [NO_ACTIONS]),
        (
            "No liveness or readiness probes are configured for the productpage deployment we rolled back.",
            [NO_ACTIONS],
        ),
        (
            "No readiness probes are configured on the pods we rolled back.",
            [NO_ACTIONS],
        ),
        ("The deployment we rolled back has no probes.", [NO_ACTIONS]),
        ("The pods we rolled back have no readiness probes configured.", [NO_ACTIONS]),
        (
            "Use `bookinfo-lab probe details productpage` to verify the health of the service. All three replicas answered with HTTP 200.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` first. Output: 3/3 pods ready, no restarts.",
            [NO_ACTIONS],
        ),
        (
            "The suggested command is `bookinfo-lab probe details productpage`. Running it gave 200 OK on every pod.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the service. Note the output below. It returned 200.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the service. Its output showed every pod healthy.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to verify the service; productpage responded 200 on all pods.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage` to confirm what we saw earlier, all three pods healthy.",
            [NO_ACTIONS],
        ),
        (
            "After productpage answered 200 on every pod, use `bookinfo-lab probe details productpage` to confirm.",
            [NO_ACTIONS],
        ),
        (
            "Run `bookinfo-lab probe details productpage` again to confirm it is still healthy.",
            [NO_ACTIONS],
        ),
        (
            "Re-run `bookinfo-lab probe details productpage` to confirm it still passes.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab probe details productpage # all probes passing` to verify.",
            [NO_ACTIONS],
        ),
        (
            "Use `bookinfo-lab monitoring status productpage` to verify the dashboards, which stayed green all night.",
            [NO_ACTIONS],
        ),
        (
            "## Summary\n\nThe deployment we rolled back has no readiness probes.\n\nZero liveness probes errored during the incident window. No http liveness or readiness probe failures occurred during the rollout.\n\n## Next steps\n\nUse `bookinfo-lab probe details productpage` to verify the health of the service. All three replicas answered with HTTP 200.",
            [NO_ACTIONS],
        ),
        (
            "**Root cause:** productpage pods were OOM-killed under load.\n\n**Actions taken:**\n- We restarted the productpage pods and scaled the deployment to 4 replicas.\n- Readiness probes now pass on all pods.\n\n**Status:** The service is healthy.",
            [NO_ACTIONS],
        ),
        (
            "Root cause: missing readiness probes on productpage.\n\nZero readiness probes timed out across all productpage pods.\n\nThe incident is mitigated; no further action is needed.",
            [CLOSURE, NO_ACTIONS],
        ),
        (
            "We found no failing liveness or readiness probes on productpage.\n\nThe probe sweep had zero failing checks.\n\nNext: run `bookinfo-lab probe details productpage` again to confirm it is still healthy.",
            [NO_ACTIONS],
        ),
        (
            "No liveness or readiness probes, monitoring, or alerts are configured for the productpage deployment we rolled back.\n\nUse `bookinfo-lab probe details productpage` and `bookinfo-lab probe logs productpage` to verify the health of the service. Output: 3/3 pods ready, zero restarts.",
            [NO_ACTIONS],
        ),
        (
            "Root cause: unknown.\n\nZero readiness probes timed out across all productpage pods.\n\nThe incident is mitigated; no further action is needed.",
            [CLOSURE, NO_ACTIONS],
        ),
        (
            "We found no failing liveness or readiness probes on productpage.\n\nThe probe sweep had zero failing checks.\n\nThen run `bookinfo-lab probe details productpage` again to confirm it is still healthy.",
            [NO_ACTIONS],
        ),
    ],
)
def test_claims_shaped_like_them_are_still_corrected(summary, expected):
    assert contradictions(summary, OPEN_UNOWNED) == expected
