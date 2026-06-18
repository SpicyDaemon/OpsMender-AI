"""Unit tests for the pure SLA metric helpers (Reliability v1 cleanup)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.sla import metrics


@dataclass
class FakeSample:
    observed_at: datetime
    up: bool
    suppressed: bool = False


def _series(pattern: str, *, start: datetime, step_seconds: int = 60):
    """Build samples from a string like 'UUDUU' (U=up, D=down, S=suppressed-up)."""
    out = []
    for i, ch in enumerate(pattern):
        out.append(
            FakeSample(
                observed_at=start + timedelta(seconds=i * step_seconds),
                up=ch != "D",
                suppressed=ch == "S",
            )
        )
    return out


def test_uptime_stats_empty_is_100():
    stats = metrics.uptime_stats([])
    assert stats["uptime_pct"] == 100.0
    assert stats["total_samples"] == 0


def test_uptime_stats_basic_percentage():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stats = metrics.uptime_stats(_series("U" * 9 + "D", start=start))
    assert stats["total_samples"] == 10
    assert stats["up_samples"] == 9
    assert stats["uptime_pct"] == 90.0
    assert stats["downtime_seconds"] == 60


def test_uptime_stats_suppressed_excluded():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 2 up, 1 suppressed → percentage over the 2 non-suppressed only.
    stats = metrics.uptime_stats(_series("USU", start=start))
    assert stats["uptime_pct"] == 100.0
    assert stats["suppressed_seconds"] == 60
    assert stats["total_samples"] == 3


def test_count_down_events_groups_consecutive():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Two separate downtime runs.
    assert metrics.count_down_events(_series("UUDDUUDU", start=start)) == 2


def test_mtbf_none_without_failures():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert metrics.mtbf_seconds(_series("UUUUU", start=start)) is None


def test_mtbf_divides_uptime_by_failures():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 8 up samples, 2 failure events → 8*60 / 2 = 240s.
    samples = _series("UUDUUUDUUU", start=start)
    assert metrics.count_down_events(samples) == 2
    assert metrics.mtbf_seconds(samples) == 240.0


def test_history_series_marks_unknown_buckets():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    until = start + timedelta(hours=4)
    # Two samples near the start; later buckets have no data → unknown.
    samples = [
        FakeSample(observed_at=start + timedelta(minutes=1), up=True),
        FakeSample(observed_at=start + timedelta(minutes=2), up=False),
    ]
    series = metrics.history_series(samples, since=start, until=until, buckets=4)
    assert len(series) == 4
    assert series[0]["status"] == "down"  # contains a down sample
    assert series[-1]["status"] == "unknown"  # no samples


# ---------------------------------------------------------------------------
# downtime_episodes (v1.2 — outage history)
# ---------------------------------------------------------------------------


def test_downtime_episodes_groups_runs_with_recovery():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # UU DDD UU D U  → two episodes (3-sample run, then 1-sample run)
    samples = _series("UUDDDUUDU", start=start)
    eps = metrics.downtime_episodes(samples)
    assert len(eps) == 2
    assert eps[0]["duration_seconds"] == 3 * 60
    assert eps[0]["started_at"] == start + timedelta(minutes=2)
    assert eps[0]["ended_at"] == start + timedelta(minutes=5)
    assert eps[0]["maintenance"] is False
    assert eps[1]["duration_seconds"] == 60


def test_downtime_episodes_ongoing_has_no_end():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = _series("UUDD", start=start)  # still down at the end
    eps = metrics.downtime_episodes(samples)
    assert len(eps) == 1
    assert eps[0]["ended_at"] is None
    assert eps[0]["duration_seconds"] >= 60


def test_downtime_episodes_flags_maintenance_when_fully_suppressed():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Two suppressed-down samples bracketed by up samples → a maintenance outage.
    samples = [
        FakeSample(observed_at=start, up=True),
        FakeSample(observed_at=start + timedelta(minutes=1), up=False, suppressed=True),
        FakeSample(observed_at=start + timedelta(minutes=2), up=False, suppressed=True),
        FakeSample(observed_at=start + timedelta(minutes=3), up=True),
    ]
    eps = metrics.downtime_episodes(samples)
    assert len(eps) == 1
    assert eps[0]["maintenance"] is True


def test_downtime_episodes_partial_suppression_is_real_outage():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # One real down + one suppressed down in the same run → counts as a real outage.
    samples = [
        FakeSample(observed_at=start, up=False),
        FakeSample(observed_at=start + timedelta(minutes=1), up=False, suppressed=True),
        FakeSample(observed_at=start + timedelta(minutes=2), up=True),
    ]
    eps = metrics.downtime_episodes(samples)
    assert len(eps) == 1
    assert eps[0]["maintenance"] is False


def test_downtime_episodes_none_when_all_up():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert metrics.downtime_episodes(_series("UUUU", start=start)) == []
