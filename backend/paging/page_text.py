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


async def org_name_for_page(db: AsyncSession, org_id: uuid.UUID) -> str | None:
    """Resolve the org name to show on a page (``None`` only if it can't be
    found). Every page names its org so a responder always knows which
    organization a page is for."""

    org = await OrganizationRepo.get_by_id(db, org_id)
    return org.name if org is not None else None
