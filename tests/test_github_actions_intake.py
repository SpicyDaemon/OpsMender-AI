"""M1-142 (I-06): a GitHub webhook on an intake URL pages only for a failed
workflow run.

In the 2026-11-05 live round one private repository's webhook, subscribed to
workflow runs, was pointed at a service's Alert Intake URL. One failed run
produced three accepted lifecycle deliveries and three untitled incidents,
and GitHub's setup ping added a fourth named after the repository. The
workflow's own failure-only step, posting the canonical payload, paged once
as intended.
"""

# The app, client and auth_headers fixtures come from the paging tests.
# ruff: noqa: F811

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models import Incident, IngestLog
from backend.ingest.github_actions import github_delivery
from tests.test_paging import TEST_ORG_ID, app, auth_headers, client  # noqa: F401

REPO = {"full_name": "acme/opsmender-intg1-lab"}
SENDER = {"login": "octo"}


def _run(action: str, conclusion: str | None, *, run_id=4101, attempt: int = 1):
    return {
        "action": action,
        "workflow_run": {
            "id": run_id,
            "name": "ci",
            "head_branch": "break-ci",
            "head_sha": "0123456789abcdef0123456789abcdef01234567",
            "status": "completed" if action == "completed" else action,
            "conclusion": conclusion,
            "html_url": f"https://github.com/acme/opsmender-intg1-lab/actions/runs/{run_id}",
            "run_attempt": attempt,
            "triggering_actor": SENDER,
        },
        "repository": REPO,
        "sender": SENDER,
    }


PING = {"zen": "Keep it logically awesome.", "hook_id": 1, "repository": REPO}
PUSH = {"ref": "refs/heads/main", "repository": REPO, "sender": SENDER}
SKIPPED = {
    "success": True,
    "incident_id": None,
    "dedup_action": "skipped",
    "error": None,
}


async def _intake_url(client, auth_headers) -> str:
    team = await client.post(
        "/teams",
        json={"name": "Alpha", "slug": f"alpha-{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    assert team.status_code == 201, team.text
    service = await client.post(
        "/services",
        json={"team_id": team.json()["id"], "name": "Lab", "slug": "lab"},
        headers=auth_headers,
    )
    assert service.status_code == 201, service.text
    return service.json()["intake_url"]


async def _incidents(app) -> list[Incident]:
    async with app.state.session_factory() as db:
        rows = await db.execute(select(Incident).where(Incident.org_id == TEST_ORG_ID))
        return list(rows.scalars().all())


async def _log_rows(app) -> int:
    async with app.state.session_factory() as db:
        return len((await db.execute(select(IngestLog))).scalars().all())


async def test_one_failed_run_pages_once_and_its_lifecycle_pages_nothing(
    app, client, auth_headers
):
    url = await _intake_url(client, auth_headers)

    async def deliver(event: str, body: dict):
        response = await client.post(url, json=body, headers={"X-GitHub-Event": event})
        assert response.status_code == 200, response.text
        return response.json()

    # The live round's deliveries, in order: the setup ping and the three
    # steps of one failed run. A push, which a webhook is subscribed to
    # unless it is unticked, pages nothing either.
    assert await deliver("ping", PING) == SKIPPED
    assert await deliver("push", PUSH) == SKIPPED
    assert await deliver("workflow_run", _run("requested", None)) == SKIPPED
    assert await deliver("workflow_run", _run("in_progress", None)) == SKIPPED
    assert await _incidents(app) == []
    # Nothing was written to the delivery log either: its rows are counted
    # as inbound alerts by the noise report.
    assert await _log_rows(app) == 0

    failed = await deliver("workflow_run", _run("completed", "failure"))
    assert failed["dedup_action"] == "created"
    incidents = await _incidents(app)
    assert len(incidents) == 1
    assert incidents[0].title == (
        "GitHub Actions failed: ci on acme/opsmender-intg1-lab (break-ci)"
    )
    assert incidents[0].severity == "high"
    assert incidents[0].description == (
        "Workflow run ended with conclusion failure.\n"
        "Run: https://github.com/acme/opsmender-intg1-lab/actions/runs/4101\n"
        "Commit: 0123456789ab\n"
        "Triggered by: octo\n"
        "Attempt: 1"
    )

    # A redelivery and a failed re-run of the same run join that incident.
    again = await deliver("workflow_run", _run("completed", "failure"))
    rerun = await deliver("workflow_run", _run("completed", "failure", attempt=2))
    assert {again["incident_id"], rerun["incident_id"]} == {failed["incident_id"]}
    assert len(await _incidents(app)) == 1

    # Another run is another incident; a run that passed or was cancelled is
    # none.
    await deliver("workflow_run", _run("completed", "timed_out", run_id=4102))
    passed = await deliver("workflow_run", _run("completed", "success", run_id=4103))
    stopped = await deliver("workflow_run", _run("completed", "cancelled", run_id=4104))
    assert passed == SKIPPED and stopped == SKIPPED
    assert len(await _incidents(app)) == 2


async def test_the_workflows_own_step_and_the_webhook_can_share_an_id(
    app, client, auth_headers
):
    """A workflow that posts its own failure payload and a webhook on the
    same URL page once when the step uses the run's id."""
    url = await _intake_url(client, auth_headers)
    step = await client.post(
        url,
        json={
            "title": "intg1 ci failed: ci #7",
            "description": "https://github.com/acme/opsmender-intg1-lab/actions/runs/7",
            "severity": "high",
            "id": "github-actions-run-7",
            "status": "firing",
        },
    )
    assert step.json()["dedup_action"] == "created"
    webhook = await client.post(
        url,
        json=_run("completed", "failure", run_id=7),
        headers={"X-GitHub-Event": "workflow_run"},
    )
    assert webhook.json()["incident_id"] == step.json()["incident_id"]
    assert [i.title for i in await _incidents(app)] == ["intg1 ci failed: ci #7"]


async def test_other_senders_and_titled_payloads_are_untouched(
    app, client, auth_headers
):
    url = await _intake_url(client, auth_headers)
    # A payload that names its own title is an alert whatever header a proxy
    # or a sender added.
    titled = await client.post(
        url,
        json={"title": "DB down", "id": "db-1", "repository": REPO},
        headers={"X-GitHub-Event": "ping"},
    )
    assert titled.json()["dedup_action"] == "created"
    # The run's shape from a sender that is not a GitHub webhook is parsed
    # as before.
    lookalike = await client.post(
        url, json={"name": "deploy failed", "workflow_run": {"id": 1}, "id": "x-1"}
    )
    assert lookalike.json()["dedup_action"] == "created"
    titles = sorted(incident.title for incident in await _incidents(app))
    assert titles == ["DB down", "deploy failed"]


async def test_a_token_for_another_providers_format_is_left_as_it_was(
    app, client, auth_headers
):
    """Only a token whose alerts the universal parser reads is classified:
    one tied to another provider's format would refuse the canonical
    payload, so answering its ping with 200 would hide that."""
    created = await client.post(
        "/ingest-tokens",
        json={"name": "loki", "provider": "loki"},
        headers=auth_headers,
    )
    assert created.status_code in (200, 201), created.text
    token = created.json()["token"]
    for event, body in (("ping", PING), ("workflow_run", _run("completed", "failure"))):
        response = await client.post(
            "/incidents/ingest",
            json=body,
            headers={"X-GitHub-Event": event, "X-OpsMender-Token": token},
        )
        assert response.status_code == 422, (event, response.text)
    assert await _incidents(app) == []

    auto = await client.post(
        "/ingest-tokens",
        json={"name": "auto", "provider": "auto"},
        headers=auth_headers,
    )
    token = auto.json()["token"]
    ping = await client.post(
        "/incidents/ingest",
        json=PING,
        headers={"X-GitHub-Event": "ping", "X-OpsMender-Token": token},
    )
    assert ping.json() == SKIPPED
    failed = await client.post(
        "/incidents/ingest",
        json=_run("completed", "failure"),
        headers={"X-GitHub-Event": "workflow_run", "X-OpsMender-Token": token},
    )
    assert failed.json()["dedup_action"] == "created"


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        # No GitHub header, or not GitHub's body shape.
        (None, _run("completed", "failure")),
        ("workflow_run", ["not", "an", "object"]),
        ("workflow_run", {"action": "completed", "workflow_run": {"id": 1}}),
        ("ping", {"title": "DB down", "repository": REPO}),
        ("not a github event name!", PING),
    ],
)
def test_what_is_not_githubs_is_left_to_the_existing_parsers(event, payload):
    assert github_delivery(event, payload) is None


@pytest.mark.parametrize(
    ("event", "payload", "reason"),
    [
        ("ping", PING, "GitHub webhook setup ping; not an alert"),
        ("push", PUSH, "GitHub push event; not an alert"),
        (
            "workflow_job",
            {"action": "completed", "workflow_job": {"id": 1}, "repository": REPO},
            "GitHub workflow_job event; not an alert",
        ),
        (
            "Workflow_Run",
            _run("in_progress", None),
            "GitHub workflow run in_progress; the run has not ended",
        ),
        (
            "workflow_run",
            _run("completed", "cancelled"),
            "GitHub workflow run completed as cancelled; not a failure",
        ),
        # A sender's own words are never repeated: an unknown step or
        # outcome is "other", however long it is.
        (
            "workflow_run",
            _run("A" * 500000, None),
            "GitHub workflow run other; the run has not ended",
        ),
        (
            "workflow_run",
            _run("completed", "x\nFAKE LOG LINE"),
            "GitHub workflow run completed as other; not a failure",
        ),
        # A run id that is not GitHub's (text, a flag) opens nothing.
        (
            "workflow_run",
            _run("completed", "failure", run_id="x" * 2000),
            "GitHub workflow run without a run id",
        ),
        (
            "workflow_run",
            _run("completed", "failure", run_id=True),
            "GitHub workflow run without a run id",
        ),
        (
            "workflow_run",
            {"action": "completed", "workflow_run": "nope", "repository": REPO},
            "GitHub workflow_run event without a run",
        ),
    ],
)
def test_a_github_delivery_that_is_not_a_failed_run_is_acknowledged(
    event, payload, reason
):
    delivery = github_delivery(event, payload)
    assert delivery is not None and delivery.alert is None
    assert delivery.ignored == reason


def test_a_failed_run_becomes_the_canonical_alert():
    delivery = github_delivery("Workflow_Run", _run("completed", "startup_failure"))
    assert delivery is not None and delivery.ignored is None
    assert delivery.alert == {
        "title": "GitHub Actions failed: ci on acme/opsmender-intg1-lab (break-ci)",
        "description": (
            "Workflow run ended with conclusion startup_failure.\n"
            "Run: https://github.com/acme/opsmender-intg1-lab/actions/runs/4101\n"
            "Commit: 0123456789ab\n"
            "Triggered by: octo\n"
            "Attempt: 1"
        ),
        "severity": "high",
        "id": "github-actions-run-4101",
        "status": "firing",
        "source": "github_actions",
    }


def test_a_failed_runs_text_fields_are_bounded():
    body = _run("completed", "failure")
    body["workflow_run"]["name"] = "n" * 5000
    body["workflow_run"]["head_branch"] = "b\n" * 5000
    body["workflow_run"]["html_url"] = "https://example.test/" + "u" * 5000
    body["workflow_run"]["triggering_actor"] = {"login": "l" * 5000}
    alert = github_delivery("workflow_run", body).alert
    assert len(alert["title"]) < 400
    assert "\n" not in alert["title"]
    assert len(alert["description"]) < 800
