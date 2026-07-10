"""Shared formatting for the plain-text body of an incident page.

Used by both the immediate fan-out (``dispatch``) and the staged
notification-escalation path so SMS / voice / email / text-fallback pages read
identically. Every page names its organization so a responder always knows which
org a page is for.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Incident
from backend.db.repos import OrganizationRepo


def format_page_subject_body(
    incident: Incident, *, org_name: str | None = None
) -> tuple[str, str]:
    """Build the (subject, body) for a page. ``org_name`` adds an ``Org:`` line
    at the top of the body when provided."""

    subject = f"OpsMender: {incident.title or 'Incident page'}"
    lines: list[str] = []
    if org_name:
        lines.append(f"Org: {org_name}")
    lines += [
        f"Priority: {incident.priority or 'P?'}",
        f"Status: {incident.status}",
        f"Incident: {incident.id}",
    ]
    if incident.description:
        lines.append("")
        lines.append(incident.description)
    return subject, "\n".join(lines)


# Spoken severity words keyed by priority. Reading "P1" aloud is unclear; a
# severity word is the telephony convention.
_PRIORITY_WORDS = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}


def _voice_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def format_voice_summary(
    incident: Incident,
    *,
    org_name: str | None = None,
    service_name: str | None = None,
) -> str:
    """A concise, speakable one-line summary of a page.

    Unlike the text body, this omits the incident UUID and status (which read
    badly aloud) and leads with severity + the affected service, the way an
    on-call voice page conventionally does.
    """
    parts: list[str] = []
    if org_name:
        parts.append(f"{org_name}.")
    severity = _PRIORITY_WORDS.get((incident.priority or "").upper())
    parts.append(
        f"{severity.capitalize()} severity incident" if severity else "Incident"
    )
    if service_name:
        parts.append(f"on {service_name}")
    title = (incident.title or "untitled").strip().rstrip(".")
    return f"{' '.join(parts)}: {title}."


def format_voice_menu_twiml(summary: str, action_url: str) -> str:
    """TwiML that speaks the page then gathers one keypad digit.

    1 = acknowledge (take ownership), 2 = escalate to the next responder,
    3 = resolve (e.g. a false alarm), * = repeat. No input falls through and
    hangs up — the escalation chain's timer re-pages as usual.
    """
    say = (
        f"This is OpsMender. {summary} "
        "Press 1 to acknowledge and take ownership. "
        "Press 2 to escalate to the next responder. "
        "Press 3 to resolve this incident, for example if it is a false alarm. "
        "Press star to repeat this message."
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather numDigits="1" timeout="12" method="POST" action="{_voice_escape(action_url)}">'
        f"<Say>{_voice_escape(say)}</Say>"
        "</Gather>"
        "<Say>No input received. Goodbye.</Say>"
        "<Hangup/>"
        "</Response>"
    )


async def org_name_for_page(db: AsyncSession, org_id: uuid.UUID) -> str | None:
    """Resolve the org name to show on a page (``None`` only if it can't be
    found). Every page names its org so a responder always knows which
    organization a page is for."""

    org = await OrganizationRepo.get_by_id(db, org_id)
    return org.name if org is not None else None
