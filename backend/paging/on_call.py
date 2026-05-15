"""Deterministic on-call resolution (Sprint 33).

Given a roster snapshot and a timestamp T, ``on_call_at`` returns the
``user_id`` who is responsible for paging at that moment. The function is
pure — caller materializes members + overrides from the DB and the result is
fully reproducible.

Algorithm (see ``docs/paging-model.md`` for the spec):

1. Active override wins. If any override covers T, its ``covering_user_id``
   is on call.
2. Compute the shift index from ``(T.date() - anchor_date) // pattern_length``
   modulo the number of members.
3. Apply the ``handoff_time`` boundary: if T's local time is *before*
   ``handoff_time`` on the first day of the computed shift, the previous
   shift is still active.

Time-zone math runs in the roster's configured IANA zone via ``zoneinfo``.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, datetime, time, timedelta
from typing import Iterable, Sequence
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
    shift_length = _shift_length_days(ctx)
    days_elapsed = (local.date() - ctx.anchor_date).days
    if days_elapsed < 0:
        # Before the anchor date — fall back to position 0.
        shift_index = 0
    else:
        shift_index = (days_elapsed // shift_length) % len(members)

    # Apply handoff-time boundary: figure out when the *current* shift began
    # in local time. If T is earlier than that, the previous shift is active.
    shift_start_day = ctx.anchor_date + timedelta(
        days=shift_index * shift_length
        + ((days_elapsed // shift_length) * shift_length - shift_index * shift_length),
    )
    # The above expression is intentionally written as
    #   anchor + (days_elapsed - days_elapsed % shift_length)
    # but split for clarity. Recompute directly to avoid arithmetic mistakes:
    if days_elapsed >= 0:
        shift_start_day = ctx.anchor_date + timedelta(
            days=(days_elapsed // shift_length) * shift_length
        )
    handoff = _parse_handoff(ctx.handoff_time)
    shift_start = datetime.combine(shift_start_day, handoff, tzinfo=tz)
    if local < shift_start:
        shift_index = (shift_index - 1) % len(members)

    return members[shift_index].user_id
