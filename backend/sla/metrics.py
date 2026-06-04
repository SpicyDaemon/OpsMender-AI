"""Pure uptime/SLA metric helpers (Sprint — Reliability v1 cleanup).

These functions operate on plain sample sequences so they can be unit-tested
without a database. A "sample" is anything with ``observed_at`` (aware
datetime), ``up`` (bool), and ``suppressed`` (bool) attributes — the
``UptimeSample`` ORM rows satisfy this, as do lightweight stand-ins in tests.

Conventions (kept consistent with the existing poller/repo):
- The poller records one sample per probe; uptime math treats each
  non-suppressed sample as covering ``SAMPLE_INTERVAL_SECONDS`` of wall clock.
- Suppressed samples (recorded during a maintenance window) are **excluded**
  from the uptime percentage and from downtime — i.e. maintenance windows are
  excluded from SLA impact, not merely silenced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, Sequence


SAMPLE_INTERVAL_SECONDS = 60


def _aware(dt: datetime) -> datetime:
    """Coerce a naive datetime to UTC.

    SQLite returns naive datetimes for ``DateTime(timezone=True)`` columns
    while the routes pass aware bounds; normalize so arithmetic never mixes
    naive and aware values.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class _Sample(Protocol):
    observed_at: datetime
    up: bool
    suppressed: bool


def uptime_stats(samples: Sequence[_Sample]) -> dict[str, Any]:
    """Aggregate uptime over *samples*.

    Returns ``uptime_pct`` (0–100, rounded to 4 dp so 99.999 survives),
    ``total_samples``, ``up_samples``, ``downtime_seconds``,
    ``suppressed_seconds``.
    """
    total = len(samples)
    if total == 0:
        return {
            "uptime_pct": 100.0,
            "total_samples": 0,
            "up_samples": 0,
            "downtime_seconds": 0,
            "suppressed_seconds": 0,
        }
    non_suppressed = [s for s in samples if not s.suppressed]
    suppressed_count = total - len(non_suppressed)
    up_count = sum(1 for s in non_suppressed if s.up)
    ns_total = len(non_suppressed)
    uptime_pct = up_count / ns_total * 100.0 if ns_total > 0 else 100.0
    downtime_seconds = (ns_total - up_count) * SAMPLE_INTERVAL_SECONDS
    suppressed_seconds = suppressed_count * SAMPLE_INTERVAL_SECONDS
    return {
        "uptime_pct": round(uptime_pct, 4),
        "total_samples": total,
        "up_samples": up_count,
        "downtime_seconds": downtime_seconds,
        "suppressed_seconds": suppressed_seconds,
    }


def count_down_events(samples: Sequence[_Sample]) -> int:
    """Number of distinct downtime events (a run of consecutive down samples).

    Suppressed samples are ignored — they neither start nor break a run, so a
    maintenance window doesn't fabricate or split a failure.
    """
    events = 0
    in_down = False
    for s in samples:
        if s.suppressed:
            continue
        if not s.up:
            if not in_down:
                events += 1
                in_down = True
        else:
            in_down = False
    return events


def mtbf_seconds(samples: Sequence[_Sample]) -> float | None:
    """Mean Time Between Failures, in seconds.

    Defined as total operational (up) time divided by the number of downtime
    events. Returns ``None`` when there were no failures in the window (nothing
    to average over — the UI shows this as "no downtime").
    """
    failures = count_down_events(samples)
    if failures == 0:
        return None
    up_samples = sum(1 for s in samples if not s.suppressed and s.up)
    up_seconds = up_samples * SAMPLE_INTERVAL_SECONDS
    return round(up_seconds / failures, 1)


def history_series(
    samples: Sequence[_Sample],
    *,
    since: datetime,
    until: datetime,
    buckets: int = 48,
) -> list[dict[str, Any]]:
    """Bucket *samples* into a fixed number of equal time slices for a strip.

    Each bucket carries ``ts`` (bucket start), ``up_pct`` (0–100 over
    non-suppressed samples in the bucket), and ``status``:
      - ``"unknown"`` — no samples landed in the bucket
      - ``"up"``      — every non-suppressed sample was up
      - ``"down"``    — at least one non-suppressed sample was down
    A bucket containing only suppressed samples is ``"unknown"`` (excluded).
    """
    buckets = max(1, buckets)
    since = _aware(since)
    until = _aware(until)
    span = (until - since).total_seconds()
    if span <= 0:
        return []
    width = span / buckets

    grouped: list[list[_Sample]] = [[] for _ in range(buckets)]
    for s in samples:
        offset = (_aware(s.observed_at) - since).total_seconds()
        if offset < 0 or offset > span:
            continue
        idx = min(buckets - 1, int(offset / width))
        grouped[idx].append(s)

    series: list[dict[str, Any]] = []
    for i, group in enumerate(grouped):
        ts = datetime.fromtimestamp(
            since.timestamp() + i * width, tz=timezone.utc
        )
        non_suppressed = [s for s in group if not s.suppressed]
        if not non_suppressed:
            series.append({"ts": ts, "up_pct": 100.0, "status": "unknown"})
            continue
        up = sum(1 for s in non_suppressed if s.up)
        up_pct = round(up / len(non_suppressed) * 100.0, 4)
        status = "up" if up == len(non_suppressed) else "down"
        series.append({"ts": ts, "up_pct": up_pct, "status": status})
    return series
