"""Deterministic on-call resolution (Sprint 33).

Given a roster snapshot and a timestamp T, ``on_call_at`` returns the
``user_id`` who is responsible for paging at that moment. The function is
pure — caller materializes members + overrides from the DB and the result is
fully reproducible.

Algorithm (see ``docs/PROMPT_CONTEXT.md (D-021 — Paging Model)`` for the spec):

1. Active override wins. If any override covers T, its ``covering_user_id``
   is on call.
2. If T is outside the roster coverage window, nobody is on call.
3. Shifts hand over at the coverage start (the Roster's handoff time), on the
   Start Date and every shift-length days after it. A time before that day's
   handoff belongs to the previous day's shift. The shift index is
   ``(shift date - anchor_date) // shift length`` modulo the number of
   members. The weekly handoff weekday is the Start Date's weekday; the legacy
   ``handoff_day`` column is not used.

Time-zone math runs in the roster's configured IANA zone via ``zoneinfo``.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo


VALID_PATTERNS = ("weekly", "daily", "custom_n_days")


@dataclasses.dataclass(slots=True, frozen=True)
class OnCallMember:
    user_id: uuid.UUID
    position_index: int


@dataclasses.dataclass(slots=True, frozen=True)
class OnCallOverride:
    covering_user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime


@dataclasses.dataclass(slots=True)
class OnCallContext:
    """Materialized roster snapshot for ``on_call_at``."""

    members: Sequence[OnCallMember]
    overrides: Sequence[OnCallOverride] = ()
    time_zone: str = "UTC"
    pattern: str = "weekly"
    pattern_length: int = 7
    coverage_start_time: str = "09:00"
    coverage_end_time: str = "17:00"
    handoff_time: str = "09:00"
    anchor_date: date | None = None

    def __post_init__(self) -> None:
        if self.pattern not in VALID_PATTERNS:
            raise ValueError(f"Unknown pattern: {self.pattern}")
        if self.pattern_length <= 0:
            raise ValueError("pattern_length must be > 0")
        if self.anchor_date is None:
            raise ValueError("anchor_date is required")


def _parse_handoff(value: str) -> time:
    parts = value.split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid handoff_time: {value!r}")
    return time(int(parts[0]), int(parts[1]))


def _within_coverage(local_time: time, start: time, end: time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def _shift_length_days(ctx: OnCallContext) -> int:
    if ctx.pattern == "weekly":
        return 7
    if ctx.pattern == "daily":
        return 1
    return ctx.pattern_length


def _active_override(
    overrides: Iterable[OnCallOverride], t: datetime
) -> OnCallOverride | None:
    for ov in overrides:
        if ov.starts_at <= t < ov.ends_at:
            return ov
    return None


def on_call_at(ctx: OnCallContext, t: datetime) -> uuid.UUID | None:
    """Return the user_id on call at ``t`` for the roster snapshot.

    Returns ``None`` if the roster has no members. ``t`` may be naive or
    aware; naive timestamps are interpreted in the roster's time zone.
    """

    if not ctx.members:
        return None
    members = sorted(ctx.members, key=lambda m: m.position_index)

    tz = ZoneInfo(ctx.time_zone)
    if t.tzinfo is None:
        t = t.replace(tzinfo=tz)

    override = _active_override(ctx.overrides, t)
    if override is not None:
        return override.covering_user_id

    local = t.astimezone(tz)
    coverage_start = _parse_handoff(ctx.coverage_start_time or ctx.handoff_time)
    coverage_end = _parse_handoff(ctx.coverage_end_time or ctx.coverage_start_time)
    if not _within_coverage(
        local.timetz().replace(tzinfo=None), coverage_start, coverage_end
    ):
        return None

    # One rule for every window, including overnight and 24/7: the shift
    # that is running started at the most recent handoff (coverage start).
    shift_date = local.date()
    if local.timetz().replace(tzinfo=None) < coverage_start:
        shift_date = shift_date - timedelta(days=1)

    shift_length = _shift_length_days(ctx)
    days_elapsed = (shift_date - ctx.anchor_date).days
    if days_elapsed < 0:
        # Before the anchor date — fall back to position 0.
        shift_index = 0
    else:
        shift_index = (days_elapsed // shift_length) % len(members)

    return members[shift_index].user_id


def _utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; they were stored as UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def build_context(
    roster: Any, members: Iterable[Any], overrides: Iterable[Any]
) -> OnCallContext:
    """The one way to turn a Roster row into an ``OnCallContext``.

    ``members`` must already be the active members and ``overrides`` only those
    whose covering user can still be paged (see
    ``backend.paging.on_call_context.load_on_call_context``). Every caller —
    the paging engine, the on-call API, the calendars — goes through here, so
    they all use the Roster's real coverage window and agree on who is on call.
    """

    return OnCallContext(
        members=[
            OnCallMember(user_id=m.user_id, position_index=m.position_index)
            for m in members
        ],
        overrides=[
            OnCallOverride(
                covering_user_id=o.covering_user_id,
                starts_at=_utc(o.starts_at),
                ends_at=_utc(o.ends_at),
            )
            for o in overrides
        ],
        time_zone=roster.time_zone,
        pattern=roster.pattern,
        pattern_length=roster.pattern_length,
        coverage_start_time=roster.coverage_start_time,
        coverage_end_time=roster.coverage_end_time,
        handoff_time=roster.handoff_time,
        anchor_date=roster.anchor_date,
    )
