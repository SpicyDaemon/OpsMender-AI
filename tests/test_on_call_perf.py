"""Sprint 40 step 2 — on_call_at performance pass.

Goal: prove the deterministic on-call resolver stays comfortably fast at
the scale the spec calls out (1k rosters × 50 members). The function is
pure, so we don't need a DB — we just build 1000 in-memory snapshots and
call ``on_call_at`` on each, walking a few different timestamps.

Budget: 1000 calls × 4 timestamps = 4000 invocations should land well
under 500 ms total on a modest dev machine. If this regresses, the most
likely culprit is the per-call ``sorted(members)`` allocation or the
``_parse_handoff`` reparse on every invocation — both addressed in this
module's accompanying micro-optimizations.
"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timezone

import pytest

from backend.paging.on_call import OnCallContext, OnCallMember, on_call_at


def _build_context(seed: int, member_count: int = 50) -> OnCallContext:
    # Use deterministic UUIDs so test output is reproducible.
    rng = uuid.UUID(int=seed << 16)
    members = tuple(
        OnCallMember(
            user_id=uuid.UUID(int=(seed << 16) | i),
            position_index=i,
        )
        for i in range(member_count)
    )
    return OnCallContext(
        members=members,
        time_zone="America/New_York",
        pattern="weekly",
        pattern_length=7,
        coverage_start_time="00:00",
        coverage_end_time="00:00",
        handoff_time="09:00",
        anchor_date=date(2025, 1, 6),
    )


class TestOnCallPerf:
    def test_resolves_1k_rosters_50_members_under_budget(self):
        """1k rosters × 50 members × 4 time-of-day samples completes fast."""

        rosters = [_build_context(seed=i) for i in range(1000)]
        samples = [
            datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc),
            datetime(2026, 5, 18, 23, 45, tzinfo=timezone.utc),
            datetime(2026, 8, 4, 6, 15, tzinfo=timezone.utc),
        ]

        started = time.perf_counter()
        miss = 0
        for ctx in rosters:
            for t in samples:
                if on_call_at(ctx, t) is None:
                    miss += 1
        elapsed = time.perf_counter() - started

        assert miss == 0, "every roster has 50 members, so no result should be None"
        # 4000 invocations should finish well under 500ms on CI hardware.
        # If this trips, the function has regressed — investigate the hot
        # path (likely sorted/parse_handoff per call) before bumping the bound.
        assert elapsed < 0.5, (
            f"on_call_at slowed down: 4000 calls took {elapsed*1000:.1f}ms "
            "(budget: 500ms)"
        )

    def test_single_call_stays_microsecond_class(self):
        """Single on_call_at resolution stays under 1ms for a 50-member roster."""

        ctx = _build_context(seed=42)
        t = datetime(2026, 5, 18, 14, 30, tzinfo=timezone.utc)

        # Warm the ZoneInfo cache.
        on_call_at(ctx, t)

        N = 1000
        started = time.perf_counter()
        for _ in range(N):
            on_call_at(ctx, t)
        elapsed = time.perf_counter() - started

        per_call_ms = (elapsed / N) * 1000
        assert per_call_ms < 1.0, (
            f"on_call_at average runtime regressed: {per_call_ms:.3f}ms/call "
            "(budget: 1.0ms)"
        )
