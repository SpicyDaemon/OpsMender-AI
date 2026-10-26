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
spec mandates for platforms without verified interactive callbacks. Slack and
Discord can replace that fallback hint with native actions when the channel
opts in and its platform verifier is configured. Microsoft Teams receives
nothing while its delivery is being rebuilt (M1-96).

``build_incident_message`` is deliberately platform-neutral: it returns a
single markdown-ish string that every adapter's ``send_message`` already knows
how to deliver. Whether a platform *renders* it as a rich card or a plain line
is governed by :mod:`backend.bots.capabilities`; the content is the same useful
information either way. ``incident_card_facts`` is the one list of facts the
text and the native Slack and Discord cards all show.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_PUBLIC_URL = "http://localhost:3000"

# The last line of a card without verified action buttons. A card that has the
# buttons must not carry it.
SIGN_IN_TO_ACT_HINT = (
    "Sign in to OpsMender to acknowledge, resolve, escalate, or start an AI session."
)

# The Team fact on the last card a team-scoped channel gets once the incident
# has left its teams. That card names no service, team or responder.
MOVED_TO_ANOTHER_TEAM = "Moved to another team"

# Maps the raw lifecycle event key to the headline shown at the top of the card.
EVENT_HEADLINES: dict[str, str] = {
    "incident.created": "🚨 New incident",
    "incident.acknowledged": "✅ Incident acknowledged",
    "incident.resolved": "🟢 Incident resolved",
    "incident.escalated": "⏫ Incident escalated",
    "incident.escalation_exhausted": "Escalation exhausted",
    "incident.updated": "✏️ Incident updated",
}


def public_base_url(base_url: str | None = None) -> str:
    """The OpsMender web origin chat links point at: the caller's value, else
    ``OPSMENDER_PUBLIC_URL``, else the local default."""
    candidate = base_url or os.environ.get("OPSMENDER_PUBLIC_URL") or DEFAULT_PUBLIC_URL
    return candidate.rstrip("/")


def incident_link(incident_id: Any, *, base_url: str | None = None) -> str:
    """Authenticated deep link to the incident detail page.

    Opening it requires an OpsMender session; it is safe to post into a chat
    channel because viewing/acting still enforces login + RBAC.
    """
    return f"{public_base_url(base_url)}/dashboard/incidents/detail?id={incident_id}"


def _headline(event_type: str | None) -> str:
    if event_type and event_type in EVENT_HEADLINES:
        return EVENT_HEADLINES[event_type]
    return "🚨 Incident update"


def incident_card_facts(
    incident,
    *,
    responder: dict | None,
    service_name: str | None,
    team_name: str | None,
    org_name: str | None,
) -> list[tuple[str, str]]:
    """The ordered facts every incident card shows, as ``(label, value)``.

    One source for the text message and the Slack and Discord cards,
    so they cannot drift: Service, Team and Org (when set), then who holds
    the incident (Responder, Acknowledged by when set, Escalation level when
    above 0). ``responder`` is the snapshot the notifier resolves; without it
    the card says nothing about who holds the incident.
    """
    source = service_name or getattr(incident, "external_source", None) or "manual"
    facts: list[tuple[str, str]] = [("Service", source)]
    if team_name:
        facts.append(("Team", team_name))
    if org_name:
        facts.append(("Org", org_name))
    if responder:
        state = responder.get("responder_state")
        name = responder.get("responder_display_name")
        if state == "assigned" and name:
            facts.append(("Responder", f"Assigned to {name}"))
        elif state == "awaiting" and name:
            facts.append(("Responder", f"Awaiting {name}"))
        elif state == "escalated" and name:
            facts.append(("Responder", f"Escalated to {name}"))
        else:
            facts.append(("Responder", "Unassigned"))
        ack_name = responder.get("acknowledged_by_display_name")
        if ack_name:
            facts.append(("Acknowledged by", ack_name))
        level = responder.get("escalation_level")
        if level:
            facts.append(("Escalation level", str(level)))
    return facts


def build_incident_message(
    incident,
    *,
    event_type: str | None = None,
    base_url: str | None = None,
    responder: dict | None = None,
    service_name: str | None = None,
    team_name: str | None = None,
    org_name: str | None = None,
    ai_summary: str | None = None,
    supports_actions: bool = False,
) -> str:
    """Build the incident message text for a Notification Channel.

    ``responder`` is the dict produced by the incidents route's
    ``_resolve_responder`` (keys: ``responder_state``,
    ``responder_display_name``, ``acknowledged_by_display_name``, …) or
    ``None`` when unavailable.

    ``supports_actions`` reflects channel-level readiness, not just the broad
    platform capability. Slack and Discord set it only when native actions are
    enabled and callback verification is configured; other platforms keep the
    OpsMender link fallback.
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

    card_facts = dict(
        incident_card_facts(
            incident,
            responder=responder,
            service_name=service_name,
            team_name=team_name,
            org_name=org_name,
        )
    )
    context_bits = [f"Service/source: {card_facts['Service']}"]
    context_bits += [
        f"{label}: {card_facts[label]}"
        for label in ("Team", "Org")
        if label in card_facts
    ]
    lines.append(" · ".join(context_bits))

    lines += [
        f"{label}: {card_facts[label]}"
        for label in ("Responder", "Escalation level")
        if label in card_facts
    ]
    # Escalation cards also name who was paged before the current responder.
    prev_name = (responder or {}).get("previous_responder_display_name")
    if prev_name:
        lines.append(f"Previous responder: {prev_name}")
    if "Acknowledged by" in card_facts:
        lines.append(f"Acknowledged by: {card_facts['Acknowledged by']}")

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
        lines.append(SIGN_IN_TO_ACT_HINT)

    return "\n".join(lines)
