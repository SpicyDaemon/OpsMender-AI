"""Teams Adaptive Card builders for paging and Notification Channels.

The Workflows builders post today: one card per incident change with
``Action.OpenUrl`` signed links (M1-100). The ``Action.Submit`` builder is
kept for the Teams bot (M1-101) and posts nothing. The Bot Framework activity
endpoint still uses the action ids and :func:`parse_incident_id_from_action`
to answer a button on a card posted earlier with the reason.

Parallels :mod:`backend.paging.slack_cards`. A *Teams page card* is an
adaptive card that was embedded in a Microsoft Graph chat message as an
``attachment`` of type ``application/vnd.microsoft.card.adaptive``.

The card carries the same affordances as the Slack equivalent:

* Header (incident title + priority pill + status badge).
* Body context (incident id + truncated description).
* Optional signed actions - Acknowledge / Resolve / Escalate / Start AI
  Session - using
  ``Action.Submit`` with a ``data.action`` field the Bot Framework activity
  endpoint routes through the common verified-action coordinator.
* An optional "View in OpsMender" ``Action.OpenUrl`` deep-link when
  ``base_url`` is provided (parallels the Slack ``ACTION_VIEW`` button).

Action data shape is intentionally identical to the Slack ``action_id``
strings - when the inbound endpoint lands in step 4 it can share the
same routing helpers.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from backend.db.models import Incident


ACTION_ACK = "opsmender:ack"
ACTION_RESOLVE = "opsmender:resolve"
ACTION_ESCALATE = "opsmender:escalate"
ACTION_START_AI_SESSION = "opsmender:start_ai_session"
ACTION_TAKE = "opsmender:take"
ACTION_VIEW = "opsmender:view"


# Adaptive card schema version we target. 1.4 is supported in Teams
# desktop, web, and mobile.
ADAPTIVE_CARD_VERSION = "1.4"

ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"

# Teams renders Markdown links and emphasis in FactSet titles and values.
_MARKDOWN_SYNTAX = re.compile(r"([\\*_\[\]()])")


def _markdown_escape(value: str) -> str:
    """Backslash-escape link and emphasis syntax, so a name or service shows
    as typed and can never become a clickable link."""
    return _MARKDOWN_SYNTAX.sub(r"\\\1", value)


def build_page_card_text(incident: Incident) -> str:
    """Fallback plain-text body. Identical shape to the Slack helper so
    notification previews and screen readers see the same thing."""

    priority = (incident.priority or "P?").upper()
    title = incident.title or "Incident page"
    return f"[{priority}] OpsMender page: {title}"


def build_page_card_adaptive(
    incident: Incident,
    *,
    base_url: str | None = None,
    include_native_actions: bool = False,
    status_update: bool = False,
    org_name: str | None = None,
    facts: list[tuple[str, str]] | None = None,
    acknowledged_by: str | None = None,
) -> dict[str, Any]:
    """Return a single adaptive card payload (the value that goes inside
    an attachment's ``content`` field). Use :func:`wrap_card_as_attachment`
    to convert it into the Graph chat-message ``attachments`` array.

    ``facts`` (from ``incident_card_facts``) join the FactSet. Once
    ``acknowledged_by`` is set the Acknowledge action goes; Graph cannot edit
    a posted card, so the Graph path posted a fresh one for each change.
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

    fact_rows: list[dict[str, str]] = []
    if org_name:
        fact_rows.append({"title": "Org", "value": org_name})
    fact_rows += [
        {"title": "Priority", "value": priority},
        {"title": "Status", "value": status_label},
    ]
    if severity:
        fact_rows.append({"title": "Severity", "value": severity})
    fact_rows += [
        {"title": _markdown_escape(label), "value": _markdown_escape(value)}
        for label, value in facts or []
    ]
    fact_rows.append({"title": "Incident", "value": incident_id_str})

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": f"{'Status update' if status_update else 'OpsMender page'}: {title}",
            "wrap": True,
        },
        {"type": "FactSet", "facts": fact_rows},
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

    actions: list[dict[str, Any]] = []
    if include_native_actions and incident.status != "resolved":
        if not acknowledged_by:
            actions.append(
                {
                    "type": "Action.Submit",
                    "title": "Acknowledge",
                    "style": "positive",
                    "data": {
                        "action": ACTION_ACK,
                        "incident_id": incident_id_str,
                    },
                }
            )
        actions.extend(
            [
                {
                    "type": "Action.Submit",
                    "title": "Resolve",
                    "style": "destructive",
                    "data": {
                        "action": ACTION_RESOLVE,
                        "incident_id": incident_id_str,
                    },
                },
                {
                    "type": "Action.Submit",
                    "title": "Escalate",
                    "data": {
                        "action": ACTION_ESCALATE,
                        "incident_id": incident_id_str,
                    },
                },
                {
                    "type": "Action.Submit",
                    "title": "Start AI Session",
                    "data": {
                        "action": ACTION_START_AI_SESSION,
                        "incident_id": incident_id_str,
                    },
                },
            ]
        )
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
    incident: Incident,
    *,
    base_url: str | None = None,
    include_native_actions: bool = False,
    status_update: bool = False,
    facts: list[tuple[str, str]] | None = None,
    acknowledged_by: str | None = None,
) -> dict[str, Any]:
    """Return a complete Graph ``chats/{id}/messages`` payload carrying
    the adaptive card. Nothing posts it today (M1-96): Teams delivery is
    being rebuilt, and the dispatcher skips Teams pages.
    """

    card = build_page_card_adaptive(
        incident,
        base_url=base_url,
        include_native_actions=include_native_actions,
        status_update=status_update,
        facts=facts,
        acknowledged_by=acknowledged_by,
    )
    attachment = wrap_card_as_attachment(card)
    fallback = build_page_card_text(incident)
    return {
        "body": {
            "contentType": "html",
            "content": (
                f'<p>{fallback}</p><attachment id="{attachment["id"]}"></attachment>'
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


# ---------------------------------------------------------------------------
# Microsoft Teams (Workflows) cards (M1-100)
# ---------------------------------------------------------------------------
#
# A Teams workflow made from the "Send webhook alerts to a channel" template
# posts the Adaptive Card it is sent. Its buttons can only open links, so each
# action is a signed link (M1-99) to OpsMender's confirm page. The caller
# mints them: this module must not import backend.bots.action_links, which
# imports the action code, which imports the notifier.

# Labels equal ACTION_LABELS in backend/api/routes/action_links.py (a test
# pins this), so the button and the confirm page name the same action.
WORKFLOWS_ACTIONS = (
    ("acknowledge", "Acknowledge", "positive"),
    ("resolve", "Resolve", None),
    ("escalate", "Escalate", None),
    ("start_ai_session", "Start AI session", None),
)
# As backend/api/routes/incidents.py: a closed incident offers no action.
CLOSED_STATUSES = frozenset({"resolved", "merged"})
FACT_VALUE_MAX = 200
SNIPPET_MAX = 300
MINIMAL_TITLE_MAX = 200
# Microsoft takes about 28 KB per post; every body stays under this.
WORKFLOWS_MAX_BODY_BYTES = 26 * 1024
TEXT_CARD_MAX_CHARS = 3000
TEXT_CARD_MAX_LINES = 40
TEXT_FALLBACK_CHARS = 1000
LINKS_NOTE = (
    "The buttons open OpsMender, where you sign in and confirm. Each button works once."
)
# Mirrors the line the escalation engine adds to an exhausted chain's text.
EXHAUSTED_NOTE = "No further responder level is configured."
VIEW_TITLE = "View in OpsMender"


def offered_actions(status: str | None, acknowledged_by: str | None) -> list[str]:
    """The actions a Workflows card offers: none once the incident is closed,
    and Acknowledge only while nobody has acknowledged it."""
    if status in CLOSED_STATUSES:
        return []
    return [
        action
        for action, _label, _style in WORKFLOWS_ACTIONS
        if not (action == "acknowledge" and acknowledged_by)
    ]


def _view_action(url: str) -> dict[str, Any]:
    return {"type": "Action.OpenUrl", "title": VIEW_TITLE, "url": url}


def _incident_view_url(incident: Incident, base_url: str) -> str:
    return (
        f"{base_url.rstrip('/')}/dashboard/incidents/detail?id={incident.id}&from=teams"
    )


def _adaptive_card(
    body: list[dict[str, Any]], actions: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": ADAPTIVE_CARD_VERSION,
        "body": body,
        "actions": actions,
    }


def _title_block(headline: str, title: str) -> dict[str, Any]:
    return {
        "type": "TextBlock",
        "size": "Large",
        "weight": "Bolder",
        "wrap": True,
        "text": f"{headline}: {_markdown_escape(title)}",
    }


def _priority_status_facts(incident: Incident) -> list[dict[str, str]]:
    return [
        {"title": "Priority", "value": (incident.priority or "P?").upper()},
        {
            "title": "Status",
            "value": (incident.status or "open").replace("_", " ").title(),
        },
    ]


def build_workflows_card(
    incident: Incident,
    *,
    headline: str,
    facts: list[tuple[str, str]] | None,
    base_url: str,
    action_links: Mapping[str, str] | None = None,
    acknowledged_by: str | None = None,
    exhausted: bool = False,
) -> dict[str, Any]:
    """The full Workflows card for one incident change.

    Alert text (title, description, fact values) is escaped, so it shows as
    typed and never becomes a link. A button appears only for an action the
    incident offers and a link the caller minted; View in OpsMender is last
    on every card. Only ``Action.OpenUrl`` is ever emitted.
    """
    body: list[dict[str, Any]] = [_title_block(headline, incident.title or "Incident")]
    if exhausted:
        body.append({"type": "TextBlock", "wrap": True, "text": EXHAUSTED_NOTE})

    fact_rows = _priority_status_facts(incident)
    severity = (incident.severity or "").strip()
    if severity:
        fact_rows.append({"title": "Severity", "value": _markdown_escape(severity)})
    fact_rows += [
        {
            "title": _markdown_escape(label),
            "value": _markdown_escape(str(value)[:FACT_VALUE_MAX]),
        }
        for label, value in facts or []
    ]
    fact_rows.append({"title": "Incident", "value": str(incident.id)})
    body.append({"type": "FactSet", "facts": fact_rows})

    description = (incident.description or "").strip()
    if description:
        body.append(
            {
                "type": "TextBlock",
                "wrap": True,
                "spacing": "Medium",
                "isSubtle": True,
                "text": _markdown_escape(description.splitlines()[0][:SNIPPET_MAX]),
            }
        )

    offered = offered_actions(incident.status, acknowledged_by)
    links = action_links or {}
    actions: list[dict[str, Any]] = []
    for action, label, style in WORKFLOWS_ACTIONS:
        if action not in offered or not links.get(action):
            continue
        button: dict[str, Any] = {
            "type": "Action.OpenUrl",
            "title": label,
            "url": links[action],
        }
        if style:
            button["style"] = style
        actions.append(button)
    if actions:
        body.append(
            {
                "type": "TextBlock",
                "wrap": True,
                "size": "Small",
                "isSubtle": True,
                "text": LINKS_NOTE,
            }
        )
    actions.append(_view_action(_incident_view_url(incident, base_url)))
    return _adaptive_card(body, actions)


def build_workflows_minimal_card(
    incident: Incident, *, headline: str, base_url: str
) -> dict[str, Any]:
    """The card for extreme input: the headline and a short title, Priority
    and Status, and View in OpsMender."""
    title = (incident.title or "Incident")[:MINIMAL_TITLE_MAX]
    body = [
        _title_block(headline, title),
        {"type": "FactSet", "facts": _priority_status_facts(incident)},
    ]
    return _adaptive_card(body, [_view_action(_incident_view_url(incident, base_url))])


def _text_card_lines(
    text: str, *, base_url: str, incident_id: uuid.UUID | None = None
) -> tuple[list[str], str | None]:
    """Keep text as text. Only the caller's incident ID can create a button."""
    lines: list[str] = []
    view_url = (
        f"{base_url.rstrip('/')}/dashboard/incidents/detail?id={incident_id}"
        if incident_id is not None
        else None
    )
    for raw in text[:TEXT_CARD_MAX_CHARS].replace("`", "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if view_url is not None and line == f"Open incident: {view_url}":
            continue
        lines.append(line)
    return lines, view_url


def _text_blocks(lines: list[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        block: dict[str, Any] = {"type": "TextBlock", "wrap": True}
        if index == 0:
            block["weight"] = "Bolder"
            line = line.strip("*").strip() or line
        block["text"] = _markdown_escape(line)
        blocks.append(block)
    return blocks


def build_workflows_text_card(
    text: str, *, base_url: str, incident_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """A card for a pre-built message (the live test, a collision notice, an
    escalation built before its incident can be read). One TextBlock per
    line, since Teams can collapse single newlines."""
    lines, view_url = _text_card_lines(text, base_url=base_url, incident_id=incident_id)
    lines = lines[:TEXT_CARD_MAX_LINES]
    body = (
        _text_blocks(lines)
        if lines
        else [{"type": "TextBlock", "wrap": True, "text": "OpsMender"}]
    )
    return _adaptive_card(body, [_view_action(view_url)] if view_url else [])


def workflows_envelope(card: dict[str, Any]) -> dict[str, Any]:
    """The message a Teams workflow trigger takes: one Adaptive Card
    attachment with exactly these keys (not the Graph attachment shape)."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
                "contentUrl": None,
                "content": card,
            }
        ],
    }


def encode_workflows_body(card: dict[str, Any]) -> bytes:
    """The exact bytes that are measured and sent."""
    return json.dumps(
        workflows_envelope(card), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def workflows_incident_body(
    incident: Incident,
    *,
    headline: str,
    facts: list[tuple[str, str]] | None,
    acknowledged_by: str | None,
    base_url: str,
    action_links: Mapping[str, str] | None,
    exhausted: bool,
) -> bytes:
    """The full card, or the minimal card when the full one is over the size
    limit. Inputs are bounded near 11 KB, so one fallback is enough, and it
    keeps the View link."""
    body = encode_workflows_body(
        build_workflows_card(
            incident,
            headline=headline,
            facts=facts,
            base_url=base_url,
            action_links=action_links,
            acknowledged_by=acknowledged_by,
            exhausted=exhausted,
        )
    )
    if len(body) <= WORKFLOWS_MAX_BODY_BYTES:
        return body
    return encode_workflows_body(
        build_workflows_minimal_card(incident, headline=headline, base_url=base_url)
    )


def workflows_text_body(
    text: str, *, base_url: str, incident_id: uuid.UUID | None = None
) -> bytes:
    """The text card, or its first line and the next 1,000 characters with
    the same View action when the full text card is over the size limit."""
    body = encode_workflows_body(
        build_workflows_text_card(text, base_url=base_url, incident_id=incident_id)
    )
    if len(body) <= WORKFLOWS_MAX_BODY_BYTES:
        return body
    lines, view_url = _text_card_lines(text, base_url=base_url, incident_id=incident_id)
    blocks = _text_blocks(lines[:1])
    rest = "\n".join(lines[1:])[:TEXT_FALLBACK_CHARS]
    if rest:
        blocks.append(
            {"type": "TextBlock", "wrap": True, "text": _markdown_escape(rest)}
        )
    return encode_workflows_body(
        _adaptive_card(blocks, [_view_action(view_url)] if view_url else [])
    )
