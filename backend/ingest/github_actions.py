"""Native GitHub webhook deliveries on an alert intake URL (M1-142).

A repository webhook posts every event it is subscribed to at the URL it is
given: the setup ``ping``, pushes, and for each workflow run ``requested``,
``in_progress`` and ``completed``, whatever its outcome. None of those is an
alert, and the universal parser found no title in them, so each became an
untitled incident. Only a workflow run that completed as a failure is an
alert; it is turned into the canonical payload the intake already
understands, keyed by the run so a re-run or a redelivery joins the incident
the first failure opened.

A delivery is recognised by GitHub's own ``X-GitHub-Event`` header together
with GitHub's body shape (a ``repository`` or ``sender`` object, or the
ping's ``zen`` line) and no ``title`` of its own. A payload that names its
own title is an alert whatever header a proxy added, and is left to the
existing parsers.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

# The run outcomes that mean the workflow did not do its job. GitHub reports
# a run someone or something stopped as ``cancelled``; ``skipped``,
# ``neutral``, ``action_required`` and ``stale`` are not failures either.
_FAILED = frozenset({"failure", "timed_out", "startup_failure"})
# GitHub's own words for a run's step and outcome; anything else a sender
# puts there is reported as "other", never echoed.
_ACTIONS = frozenset({"requested", "in_progress", "completed"})
_CONCLUSIONS = _FAILED | frozenset(
    {"success", "cancelled", "skipped", "neutral", "action_required", "stale"}
)
_EVENT = re.compile(r"[a-z_]{1,40}")
_RUN_ID = re.compile(r"[0-9]{1,20}")


@dataclasses.dataclass(frozen=True)
class GitHubDelivery:
    """What a recognised GitHub delivery amounts to.

    ``alert`` is the canonical intake payload of a failed run; ``ignored`` is
    why a delivery is acknowledged without an incident. Exactly one is set.
    """

    alert: dict[str, Any] | None = None
    ignored: str | None = None


def _text(value: Any) -> str:
    """A sender's short text, on one line."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return " ".join(str(value).split())


def _native(payload: dict[str, Any]) -> bool:
    """GitHub's own body: its envelope objects, and no title of its own."""
    if "title" in payload:
        return False
    return (
        isinstance(payload.get("repository"), dict)
        or isinstance(payload.get("sender"), dict)
        or "zen" in payload
    )


def github_delivery(event: str | None, payload: Any) -> GitHubDelivery | None:
    """Classify a delivery, or ``None`` when it is not GitHub's to classify."""
    kind = (event or "").strip().lower()
    if not _EVENT.fullmatch(kind) or not isinstance(payload, dict):
        return None
    if not _native(payload):
        return None
    if kind != "workflow_run":
        what = "webhook setup ping" if kind == "ping" else f"{kind} event"
        return GitHubDelivery(ignored=f"GitHub {what}; not an alert")
    run = payload.get("workflow_run")
    if not isinstance(run, dict):
        return GitHubDelivery(ignored="GitHub workflow_run event without a run")
    action = _text(payload.get("action"))
    if action != "completed":
        step = action if action in _ACTIONS else "other"
        return GitHubDelivery(
            ignored=f"GitHub workflow run {step}; the run has not ended"
        )
    conclusion = _text(run.get("conclusion"))
    if conclusion not in _FAILED:
        outcome = conclusion if conclusion in _CONCLUSIONS else "other"
        return GitHubDelivery(
            ignored=f"GitHub workflow run completed as {outcome}; not a failure"
        )
    run_id = _text(run.get("id"))
    if not _RUN_ID.fullmatch(run_id):
        return GitHubDelivery(ignored="GitHub workflow run without a run id")
    repository = payload.get("repository")
    repo = _text(repository.get("full_name")) if isinstance(repository, dict) else ""
    name = _text(run.get("name")) or "workflow"
    branch = _text(run.get("head_branch"))
    where = " on ".join(part[:120] for part in (name, repo) if part)
    title = f"GitHub Actions failed: {where}" + (f" ({branch[:120]})" if branch else "")
    actor = run.get("triggering_actor") or run.get("actor")
    login = _text(actor.get("login")) if isinstance(actor, dict) else ""
    lines = [
        f"Workflow run ended with conclusion {conclusion}.",
        f"Run: {_text(run.get('html_url'))[:500]}" if run.get("html_url") else "",
        f"Commit: {_text(run.get('head_sha'))[:12]}" if run.get("head_sha") else "",
        f"Triggered by: {login[:100]}" if login else "",
        f"Attempt: {_text(run.get('run_attempt'))[:10]}"
        if run.get("run_attempt")
        else "",
    ]
    return GitHubDelivery(
        alert={
            "title": title,
            "description": "\n".join(line for line in lines if line),
            "severity": "high",
            # The run, not the attempt: a re-run of the same run and a
            # redelivered webhook join the incident the first failure opened,
            # for as long as it is open.
            "id": f"github-actions-run-{run_id}",
            "status": "firing",
            "source": "github_actions",
        }
    )
