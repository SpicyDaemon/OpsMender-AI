"""Shared formatting for the plain-text body of an incident page.

Used by both the immediate fan-out (``dispatch``) and the staged
notification-escalation path so SMS / voice / email / text-fallback pages read
identically. The org name is included only when the deployment actually has more
than one organization — in a single-org install the org is unambiguous, so we
keep the page clean.
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
    """Resolve the org name to show on a page, or ``None`` for single-org.

    Returns the org's name only when the deployment has more than one
    organization (so an operator on-call across orgs can tell them apart);
    otherwise returns ``None`` so the page stays clean."""

    orgs = await OrganizationRepo.list_all(db)
    if len(orgs) <= 1:
        return None
    match = next((o for o in orgs if o.id == org_id), None)
    return match.name if match is not None else None
