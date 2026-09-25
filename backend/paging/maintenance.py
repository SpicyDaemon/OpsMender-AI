"""When a Maintenance Window is active, and what it covers.

Intake and paging both use these, so a window suppresses the same things in
both places.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from dateutil.rrule import rrulestr


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def parse_rrule(rrule: str, starts_at: datetime):
    """Parse ``rrule`` with the window's first start as DTSTART.

    Raises ``ValueError`` for a rule dateutil can't read, or one that repeats
    more often than hourly.
    """

    if re.search(r"FREQ\s*=\s*(SECONDLY|MINUTELY)", rrule, re.IGNORECASE):
        raise ValueError("A Maintenance Window can repeat at most hourly.")
    return rrulestr(rrule, dtstart=_utc(starts_at))


def window_active_at(window, at: datetime) -> bool:
    """True when ``at`` falls in the window or in one of its repeats.

    A repeat starts at each occurrence of ``rrule`` and lasts as long as the
    first window. Occurrences are in UTC, like ``starts_at``. A stored rule
    that can't be read keeps the one-off window, as before recurrence.
    """

    start, end, at = _utc(window.starts_at), _utc(window.ends_at), _utc(at)
    if start <= at < end:
        return True
    if not window.rrule:
        return False
    try:
        occurrence = parse_rrule(window.rrule, start).before(at, inc=True)
    except (ValueError, TypeError):
        return False
    return occurrence is not None and at < occurrence + (end - start)


def window_matches(
    window,
    *,
    service_id: uuid.UUID | None,
    team_id: uuid.UUID | None,
    roster_id: uuid.UUID | None = None,
) -> bool:
    """True when the window's scope covers this incident or page.

    Global covers everything; service and team cover incidents of those
    services and teams; Roster covers pages sent through that Roster's level,
    so it never matches at intake, where no Roster is known yet.
    """

    if window.scope_type == "global":
        return True
    scoped = {"service": service_id, "team": team_id, "roster": roster_id}.get(
        window.scope_type
    )
    if scoped is None:
        return False
    targets = {str(v) for v in (window.target_ids or [])}
    if window.scope_id is not None:
        targets.add(str(window.scope_id))
    return str(scoped) in targets
