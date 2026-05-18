"""Teams Adaptive Card builder for paging (Sprint 37 step 3).

Parallels :mod:`backend.paging.slack_cards`. A *Teams page card* is an
adaptive card the dispatcher embeds in a Microsoft Graph chat message
as an ``attachment`` of type ``application/vnd.microsoft.card.adaptive``.

The card carries the same affordances as the Slack equivalent:

* Header (incident title + priority pill + status badge).
* Body context (incident id + truncated description).
* Three action buttons — Acknowledge / Take Over / Resolve — using
  ``Action.Submit`` with a ``data.action`` field the Sprint 37 step 4
  bot-activity endpoint will route on.
* An optional "View in OpsMender" ``Action.OpenUrl`` deep-link when
  ``base_url`` is provided (parallels the Slack ``ACTION_VIEW`` button).

Action data shape is intentionally identical to the Slack ``action_id``
strings — when the inbound endpoint lands in step 4 it can share the
same routing helpers.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.db.models import Incident


ACTION_ACK = "opsmender:ack"
ACTION_TAKE = "opsmender:take"
ACTION_RESOLVE = "opsmender:resolve"
ACTION_VIEW = "opsmender:view"


# Adaptive card schema version we target. 1.4 is supported in Teams
# desktop, web, and mobile.
ADAPTIVE_CARD_VERSION = "1.4"

ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"


def build_page_card_text(incident: Incident) -> str:
    """Fallback plain-text body. Identical shape to the Slack helper so
    notification previews and screen readers see the same thing."""

    priority = (incident.priority or "P?").upper()
    title = incident.title or "Incident page"
    return f"[{priority}] OpsMender page: {title}"


def build_page_card_adaptive(
    incident: Incident, *, base_url: str | None = None
) -> dict[str, Any]:
    """Return a single adaptive card payload (the value that goes inside
    an attachment's ``content`` field). Use :func:`wrap_card_as_attachment`
    to convert it into the Graph chat-message ``attachments`` array.
    """

    priority = (incident.priority or "P?").upper()
    title = incident.title or "Incident page"
    status_label = (incident.status or "open").replace("_", " ").title()
    severity = (incident.severity or "").strip()
    incident_id_str = str(incident.id)
    snippet = ""
    if incident.description:
        first_line = incident.description.strip().splitlines()[0]
        snippet = first_line[:300]

    facts: list[dict[str, str]] = [
        {"title": "Priority", "value": priority},
        {"title": "Status", "value": status_label},
    ]
    if severity:
        facts.append({"title": "Severity", "value": severity})
    facts.append({"title": "Incident", "value": incident_id_str})

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": f"OpsMender page: {title}",
            "wrap": True,
        },
        {"type": "FactSet", "facts": facts},
    ]
    if snippet:
        body.append(
            {
                "type": "TextBlock",
                "wrap": True,
                "spacing": "Medium",
                "text": snippet,
                "isSubtle": True,
            }
        )

    actions: list[dict[str, Any]] = [
        {
            "type": "Action.Submit",
            "title": "Acknowledge",
            "style": "positive",
            "data": {
                "action": ACTION_ACK,
                "incident_id": incident_id_str,
            },
        },
        {
            "type": "Action.Submit",
            "title": "Take Over",
            "data": {
                "action": ACTION_TAKE,
                "incident_id": incident_id_str,
            },
        },
        {
            "type": "Action.Submit",
            "title": "Resolve",
            "style": "destructive",
            "data": {
                "action": ACTION_RESOLVE,
                "incident_id": incident_id_str,
            },
        },
    ]
    if base_url:
        deep_link = (
            f"{base_url.rstrip('/')}/dashboard/incidents/detail"
            f"?id={incident_id_str}&from=teams"
        )
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "View in OpsMender",
                "url": deep_link,
            }
        )

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": ADAPTIVE_CARD_VERSION,
        "body": body,
        "actions": actions,
    }


def wrap_card_as_attachment(card: dict[str, Any]) -> dict[str, Any]:
    """Wrap an adaptive card payload in the Graph chat ``attachment`` shape.

    Caller is responsible for picking the ``id`` and using it inside the
    message ``body.content`` as ``<attachment id="..."></attachment>``.
    """

    attachment_id = uuid.uuid4().hex
    return {
        "id": attachment_id,
        "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
        "contentUrl": None,
        "content": card,
        "name": None,
        "thumbnailUrl": None,
    }


def build_graph_chat_message(
    incident: Incident, *, base_url: str | None = None
) -> dict[str, Any]:
    """Return a complete Graph ``chats/{id}/messages`` payload carrying
    the adaptive card. This is the value the dispatcher posts to Graph.
    """

    card = build_page_card_adaptive(incident, base_url=base_url)
    attachment = wrap_card_as_attachment(card)
    fallback = build_page_card_text(incident)
    return {
        "body": {
            "contentType": "html",
            "content": (
                f"<p>{fallback}</p>"
                f"<attachment id=\"{attachment['id']}\"></attachment>"
            ),
        },
        "attachments": [attachment],
    }


def parse_incident_id_from_action(payload: dict[str, Any]) -> uuid.UUID | None:
    """Pull the incident UUID out of an Adaptive Card ``Action.Submit``
    payload as Teams sends it through the bot-activity endpoint.

    Teams posts the action ``data`` as ``value`` on the activity. We accept
    either the raw ``data`` dict or a wrapped ``{"value": {...}}`` shape.
    """

    if not isinstance(payload, dict):
        return None
    data = payload.get("value") if isinstance(payload.get("value"), dict) else payload
    raw = (data or {}).get("incident_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None
