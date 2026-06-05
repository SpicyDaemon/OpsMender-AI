"""Incident card / message content for Notification Channels.

Produces the human-readable incident message OpsMender posts to a configured
Notification Channel when an incident is created or changes state.

Honesty / security guardrail
----------------------------
The message never contains a public, unauthenticated action URL. Responder
actions (Acknowledge / Resolve / Escalate / Start AI Session) happen *inside*
OpsMender behind login + RBAC. The card therefore carries an **authenticated
incident link** that opens the incident detail page; the recipient signs in
and acts there. This is the secure, universally-supported fallback the product
spec mandates for platforms without verified interactive callbacks — which in
v1 is every platform.

``build_incident_message`` is deliberately platform-neutral: it returns a
single markdown-ish string that every adapter's ``send_message`` already knows
how to deliver. Whether a platform *renders* it as a rich card or a plain line
is governed by :mod:`backend.bots.capabilities`; the content is the same useful
information either way.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_PUBLIC_URL = "http://localhost:3000"

# Maps the raw lifecycle event key to the headline shown at the top of the card.
EVENT_HEADLINES: dict[str, str] = {
    "incident.created": "🚨 New incident",
    "incident.acknowledged": "✅ Incident acknowledged",
    "incident.resolved": "🟢 Incident resolved",
    "incident.escalated": "⏫ Incident escalated",
    "incident.updated": "✏️ Incident updated",
}


def _base_url(base_url: str | None) -> str:
    candidate = (
        base_url
        or os.environ.get("OPSMENDER_PUBLIC_URL")
        or DEFAULT_PUBLIC_URL
    )
    return candidate.rstrip("/")


def incident_link(incident_id: Any, *, base_url: str | None = None) -> str:
    """Authenticated deep link to the incident detail page.

    Opening it requires an OpsMender session; it is safe to post into a chat
    channel because viewing/acting still enforces login + RBAC.
    """
    return f"{_base_url(base_url)}/dashboard/incidents/detail?id={incident_id}"


def _headline(event_type: str | None) -> str:
    if event_type and event_type in EVENT_HEADLINES:
        return EVENT_HEADLINES[event_type]
    return "🚨 Incident update"


def build_incident_message(
    incident,
    *,
    event_type: str | None = None,
    base_url: str | None = None,
    responder: dict | None = None,
    service_name: str | None = None,
    team_name: str | None = None,
    ai_summary: str | None = None,
    supports_actions: bool = False,
) -> str:
    """Build the incident message text for a Notification Channel.

    ``responder`` is the dict produced by the incidents route's
    ``_resolve_responder`` (keys: ``responder_state``,
    ``responder_display_name``, ``acknowledged_by_display_name``, …) or
    ``None`` when unavailable.

    ``supports_actions`` reflects the *platform* capability. In v1 it is False
    for every platform, so the action hint always points the recipient into
    OpsMender; the parameter exists so a future adapter with verified
    interactive callbacks can render real controls instead.
    """
    lines: list[str] = [f"*{_headline(event_type)}: {incident.title}*"]

    facts: list[str] = []
    if incident.severity:
        facts.append(f"Severity: `{incident.severity}`")
    if incident.status:
        facts.append(f"Status: `{incident.status}`")
    if incident.priority:
        facts.append(f"Priority: `{incident.priority}`")
    if facts:
        lines.append(" · ".join(facts))

    source = service_name or incident.external_source or "manual"
    context_bits = [f"Service/source: {source}"]
    if team_name:
        context_bits.append(f"Team: {team_name}")
    lines.append(" · ".join(context_bits))

    if responder:
        state = responder.get("responder_state")
        name = responder.get("responder_display_name")
        if state == "assigned" and name:
            lines.append(f"Responder: Assigned to {name}")
        elif state == "awaiting" and name:
            lines.append(f"Responder: Awaiting {name}")
        elif state == "escalated" and name:
            lines.append(f"Responder: Escalated to {name}")
        else:
            lines.append("Responder: Unassigned")
        ack_name = responder.get("acknowledged_by_display_name")
        if ack_name:
            lines.append(f"Acknowledged by: {ack_name}")

    if incident.created_at:
        lines.append(f"Created: {incident.created_at.isoformat()}")

    description = (incident.description or "").strip()
    if description:
        snippet = description if len(description) <= 280 else description[:277] + "…"
        lines.append("")
        lines.append(snippet)

    if ai_summary:
        lines.append("")
        lines.append(f"AI summary: {ai_summary}")

    link = incident_link(incident.id, base_url=base_url)
    lines.append("")
    lines.append(f"Open incident: {link}")

    if not supports_actions:
        # Honest, secure fallback: no public action buttons. The operator acts
        # inside OpsMender under login + RBAC.
        lines.append(
            "Sign in to OpsMender to acknowledge, resolve, escalate, or start an AI session."
        )

    return "\n".join(lines)
