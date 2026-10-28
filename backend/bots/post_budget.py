"""Post limit for Notification Channels whose provider caps posts (M1-100).

A Microsoft Teams (Workflows) channel takes at most ``posts_per_window`` posts
in ``post_window`` (the adapter says how many). The count is read from
committed audit rows, so it holds across every api, worker and scheduler
process without a table or stored state of its own: every outbound attempt
that reached the provider path counts, a held or limited post does not.

A transient failure pauses the channel for a growing wait derived from the
latest counted rows. A permanent failure stops the catch-up of held cards
until a post goes through (an incident card or Send live test).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.bots.capabilities import display_name
from backend.bots.connectors import get_adapter, list_platforms
from backend.db.models import BotConnector
from backend.db.repos import BotActionAuditRepo

# Every attempt that reached the provider path.
COUNTED_STATUSES = ("ok", "delivery_failed", "transient_failure")
LIVE_TEST_COMMAND = "live_test"
# The wait after 1, 2, 3, 4, then 5 or more transient failures in a row.
RETRY_PAUSES_SECONDS = (30, 60, 120, 240, 300)
ERROR_AFTER = 5
# Held cards whose last row is older than this are not posted any more.
HELD_LOOKBACK = timedelta(hours=1)
# Microsoft may turn a workflow off after 90 days without a run.
IDLE_WARN_DAYS = 80


def _as_utc(value: datetime) -> datetime:
    """Audit timestamps are written in UTC; SQLite hands them back naive."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PostBudget:
    limit: int
    window: timedelta


def budget_for(platform: str) -> PostBudget | None:
    """The post limit of ``platform``'s adapter; None for a platform without
    one (every platform but Microsoft Teams (Workflows))."""
    adapter = get_adapter(platform)
    limit = getattr(adapter, "posts_per_window", None)
    window = getattr(adapter, "post_window", None)
    if not limit or window is None:
        return None
    return PostBudget(limit=int(limit), window=window)


def budget_platforms() -> list[str]:
    return [platform for platform in list_platforms() if budget_for(platform)]


@dataclass(frozen=True)
class ChannelState:
    used: int  # counted attempts in the window
    held: int  # held posts in the window
    transient_streak: int  # transient failures in a row, newest first
    paused_until: datetime | None
    last_failed: bool  # the latest counted post was refused

    def blocked_reason(
        self, budget: PostBudget, *, platform: str, now: datetime
    ) -> str | None:
        """Why a post must wait now, or None when it may go."""
        if self.paused_until is not None and self.paused_until > now:
            return (
                f"{display_name(platform)} did not answer the last post, so "
                f"OpsMender waits until {self.paused_until:%H:%M:%S} UTC"
            )
        if self.used >= budget.limit:
            minutes = int(budget.window.total_seconds() // 60)
            return (
                f"OpsMender posted {self.used} times to this channel in the last "
                f"{minutes} minutes, the most {display_name(platform)} takes"
            )
        return None


async def channel_state(
    db: AsyncSession,
    org_id: uuid.UUID,
    connector_id: uuid.UUID,
    budget: PostBudget,
    *,
    now: datetime,
) -> ChannelState:
    since = now - budget.window
    used = await BotActionAuditRepo.count_posts_since(
        db, org_id, connector_id, since=since, statuses=COUNTED_STATUSES
    )
    held = await BotActionAuditRepo.count_posts_since(
        db, org_id, connector_id, since=since, statuses=("held",)
    )
    recent = list(
        await BotActionAuditRepo.latest_posts(
            db, org_id, connector_id, statuses=COUNTED_STATUSES, limit=ERROR_AFTER
        )
    )
    streak = 0
    for row in recent:
        if row.status != "transient_failure":
            break
        streak += 1
    paused_until = None
    if streak:
        pause = RETRY_PAUSES_SECONDS[min(streak, len(RETRY_PAUSES_SECONDS)) - 1]
        until = _as_utc(recent[0].created_at) + timedelta(seconds=pause)
        paused_until = until if until > now else None
    return ChannelState(
        used=used,
        held=held,
        transient_streak=streak,
        paused_until=paused_until,
        last_failed=bool(recent) and recent[0].status == "delivery_failed",
    )


async def activity_checks(
    db: AsyncSession,
    org_id: uuid.UUID,
    connector: BotConnector,
    budget: PostBudget,
    *,
    now: datetime,
) -> list[tuple[str, str, str]]:
    """Check configuration rows for the post limit and the last post, as
    ``(name, level, detail)``."""
    state = await channel_state(db, org_id, connector.id, budget, now=now)
    minutes = int(budget.window.total_seconds() // 60)
    counted = f"{state.used} of {budget.limit} posts in the last {minutes} minutes"
    if state.paused_until is not None:
        post_budget = (
            "warn",
            f"{display_name(connector.platform)} did not answer the last post; "
            f"OpsMender tries again after {state.paused_until:%H:%M:%S} UTC.",
        )
    elif state.last_failed:
        post_budget = (
            "warn",
            "The last post was refused, so held cards wait until a post goes "
            "through. Fix the cause, then Send live test.",
        )
    elif state.held:
        post_budget = (
            "warn",
            f"{counted}; {state.held} incident card(s) were held to post when "
            "there is room.",
        )
    else:
        post_budget = ("pass", f"{counted}.")

    last = await BotActionAuditRepo.latest_posts(
        db, org_id, connector.id, statuses=("ok",), limit=1
    )
    if last:
        days = (now - _as_utc(last[0].created_at)).days
        if days >= IDLE_WARN_DAYS:
            last_post = ("warn", _idle_detail(days))
        elif days == 0:
            last_post = ("pass", "Last post accepted today.")
        else:
            last_post = ("pass", f"Last post accepted {days} day(s) ago.")
    else:
        age = (
            (now - _as_utc(connector.created_at)).days
            if connector.created_at is not None
            else 0
        )
        last_post = (
            ("warn", _idle_detail(age))
            if age >= IDLE_WARN_DAYS
            else ("pass", "No post yet. Send live test posts one.")
        )
    return [("post_budget", *post_budget), ("last_post", *last_post)]


def _idle_detail(days: int) -> str:
    return (
        f"No post in {days} days. Microsoft may turn the workflow off after 90 "
        "days without a run; Send live test runs it."
    )
