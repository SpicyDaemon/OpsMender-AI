"""Response-time aggregation helpers for reliability charts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, TypedDict


class LatencyPoint(TypedDict):
    ts: datetime
    avg_latency_ms: float
    min_latency_ms: int
    max_latency_ms: int
    samples: int


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def bucket_series(
    points: Iterable[LatencyPoint],
    *,
    since: datetime,
    until: datetime,
    buckets: int,
) -> list[dict]:
    """Aggregate latency points into a fixed number of chart buckets.

    Empty buckets are retained with null values so the frontend can render
    honest gaps instead of connecting periods where no latency was observed.
    """
    since = _aware(since)
    until = _aware(until)
    if buckets <= 0 or until <= since:
        return []

    width = (until - since) / buckets
    grouped: list[list[LatencyPoint]] = [[] for _ in range(buckets)]
    for point in points:
        point_ts = _aware(point["ts"])
        if point_ts < since or point_ts > until:
            continue
        index = min(int((point_ts - since) / width), buckets - 1)
        grouped[index].append(point)

    series: list[dict] = []
    for index, group in enumerate(grouped):
        bucket_start = since + width * index
        if not group:
            series.append(
                {
                    "ts": bucket_start,
                    "avg_latency_ms": None,
                    "min_latency_ms": None,
                    "max_latency_ms": None,
                    "samples": 0,
                }
            )
            continue

        sample_count = sum(point["samples"] for point in group)
        weighted_total = sum(
            point["avg_latency_ms"] * point["samples"] for point in group
        )
        series.append(
            {
                "ts": bucket_start,
                "avg_latency_ms": round(weighted_total / sample_count, 2),
                "min_latency_ms": min(point["min_latency_ms"] for point in group),
                "max_latency_ms": max(point["max_latency_ms"] for point in group),
                "samples": sample_count,
            }
        )
    return series


def summarize(series: Iterable[dict]) -> dict:
    populated = [
        point
        for point in series
        if point["avg_latency_ms"] is not None and point["samples"] > 0
    ]
    if not populated:
        return {
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "total_samples": 0,
        }

    total_samples = sum(point["samples"] for point in populated)
    return {
        "avg_latency_ms": round(
            sum(point["avg_latency_ms"] * point["samples"] for point in populated)
            / total_samples,
            2,
        ),
        "min_latency_ms": min(point["min_latency_ms"] for point in populated),
        "max_latency_ms": max(point["max_latency_ms"] for point in populated),
        "total_samples": total_samples,
    }
