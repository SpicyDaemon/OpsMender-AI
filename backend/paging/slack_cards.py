"""Slack Block Kit card builders for paging (Sprint 36).

A *page card* is the Slack message OpsMender sends when an escalation chain
fires a step. It carries:

* an at-a-glance header (incident title, priority pill, status badge),
* a deep-link button to the OpsMender web UI for the incident,
* optional signed actions for Acknowledge / Resolve / Escalate / Start AI Session.

The card's action ``block_id``/``action_id`` values encode the incident id and
the verb, so the interactions endpoint can route a click without parsing the
button label. Format:

    block_id  = ``opsmender:incident:{incident_id}``
    action_id = ``opsmender:{verb}``    where verb ∈ {"ack","take","resolve","view"}

D-021 #1: the "View in OpsMender" button is always present, even when chat
delivery succeeds, so operators can always escape into the web UI.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.db.models import Incident


ACTION_ACK = "opsmender:ack"
ACTION_RESOLVE = "opsmender:resolve"
ACTION_ESCALATE = "opsmender:escalate"
ACTION_START_AI_SESSION = "opsmender:start_ai_session"
ACTION_TAKE = "opsmender:take"
ACTION_VIEW = "opsmender:view"

_PRIORITY_EMOJI = {
    "P0": ":rotating_light:",
    "P1": ":red_circle:",
    "P2": ":large_orange_circle:",
    "P3": ":large_yellow_circle:",
}


def _block_id_for_incident(incident_id: uuid.UUID | str) -> str:
    return f"opsmender:incident:{incident_id}"


def build_page_card_text(incident: Incident) -> str:
    """The fallback ``text`` field — what Slack shows in notifications when
    Block Kit can't render (mobile lock screens, screen readers, IFTTT)."""

    priority = (incident.priority or "P?").upper()
    title = incident.title or "Incident page"
    return f"[{priority}] OpsMender page: {title}"


def build_page_card_blocks(
    incident: Incident,
    *,
    base_url: str | None = None,
    include_native_actions: bool = False,
) -> list[dict[str, Any]]:
    """Block Kit JSON for an actionable page card. ``base_url`` is the
    OpsMender web UI origin (e.g. ``https://opsmender.example.com``); when
    omitted, the "View in OpsMender" button is dropped (still safe — Sprint
    36 just degrades to text + the remaining action buttons)."""

    block_id = _block_id_for_incident(incident.id)
    priority = (incident.priority or "P?").upper()
    emoji = _PRIORITY_EMOJI.get(priority, ":bell:")
    title = incident.title or "Incident page"
    status_label = (incident.status or "open").replace("_", " ").title()
    severity = (incident.severity or "").strip()
    incident_id_str = str(incident.id)

    header_lines = [
        f"*{emoji} {priority} — {title}*",
        f"Status: `{status_label}`" + (f"  •  Severity: `{severity}`" if severity else ""),
    ]
    if incident.description:
        snippet = incident.description.strip().splitlines()[0][:200]
        if snippet:
            header_lines.append(snippet)

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "block_id": block_id,
            "text": {"type": "mrkdwn", "text": "\n".join(header_lines)},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Incident `{incident_id_str}`"}
            ],
        },
    ]

    elements: list[dict[str, Any]] = []
    if include_native_actions and incident.status not in {"resolved", "closed"}:
        elements.extend(
            [
                {
                    "type": "button",
                    "action_id": ACTION_ACK,
                    "text": {"type": "plain_text", "text": "Acknowledge"},
                    "style": "primary",
                    "value": incident_id_str,
                },
                {
                    "type": "button",
                    "action_id": ACTION_RESOLVE,
                    "text": {"type": "plain_text", "text": "Resolve"},
                    "style": "danger",
                    "value": incident_id_str,
                },
                {
                    "type": "button",
                    "action_id": ACTION_ESCALATE,
                    "text": {"type": "plain_text", "text": "Escalate"},
                    "value": incident_id_str,
                },
                {
                    "type": "button",
                    "action_id": ACTION_START_AI_SESSION,
                    "text": {"type": "plain_text", "text": "Start AI Session"},
                    "value": incident_id_str,
                },
            ]
        )
    if base_url:
        deep_link = f"{base_url.rstrip('/')}/dashboard/incidents/detail?id={incident_id_str}&from=slack"
        elements.append(
            {
                "type": "button",
                "action_id": ACTION_VIEW,
                "text": {"type": "plain_text", "text": "View in OpsMender"},
                "url": deep_link,
                "value": incident_id_str,
            }
        )

    if elements:
        blocks.append(
            {
                "type": "actions",
                "block_id": f"{block_id}:actions",
                "elements": elements,
            }
        )
    return blocks


def parse_incident_id_from_action(payload: dict[str, Any]) -> uuid.UUID | None:
    """Pull the incident UUID out of a Slack ``block_actions`` payload.

    Slack sends ``actions[0].value`` carrying the incident id we set when we
    built the card. Fall back to parsing ``actions[0].block_id`` if needed.
    """

    actions = payload.get("actions") or []
    if not actions:
        return None
    raw = actions[0].get("value")
    if not raw:
        block_id = actions[0].get("block_id") or ""
        prefix = "opsmender:incident:"
        if block_id.startswith(prefix):
            raw = block_id[len(prefix):].split(":", 1)[0]
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None
