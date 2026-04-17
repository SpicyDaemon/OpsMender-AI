"""Per-token sliding-window rate limiter for the ingest webhook.

Uses an in-memory dict of deques to track request timestamps per token ID.
Each token is allowed ``max_requests`` within a rolling ``window_seconds``
window.  Thread-safe via ``asyncio.Lock``.

Configure via:
- ``AIM_INGEST_RATE_LIMIT``  — max requests per window (default 60)
- ``AIM_INGEST_RATE_WINDOW`` — window duration in seconds (default 60)

Setting ``AIM_INGEST_RATE_LIMIT=0`` disables rate limiting entirely.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class RateLimitResult:
    """Outcome of a rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: float | None = None  # seconds until the next slot opens


class IngestRateLimiter:
    """Sliding-window rate limiter keyed by ingest token UUID."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[uuid.UUID, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    @property
    def disabled(self) -> bool:
        return self._max <= 0

    async def check(self, token_id: uuid.UUID) -> RateLimitResult:
        """Check and record a request for *token_id*.

        Returns a ``RateLimitResult`` indicating whether the request is
        allowed and how many requests remain in the current window.
        """
        if self.disabled:
            return RateLimitResult(allowed=True, limit=0, remaining=0)

        now = time.monotonic()
        cutoff = now - self._window

        async with self._lock:
            bucket = self._buckets[token_id]

            # Evict expired timestamps
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self._max:
                # Denied — calculate retry-after from the oldest entry
                retry_after = bucket[0] + self._window - now
                return RateLimitResult(
                    allowed=False,
                    limit=self._max,
                    remaining=0,
                    retry_after=max(0.0, retry_after),
                )

            # Allowed — record this request
            bucket.append(now)
            remaining = self._max - len(bucket)
            return RateLimitResult(
                allowed=True,
                limit=self._max,
                remaining=remaining,
            )

    async def reset(self, token_id: uuid.UUID) -> None:
        """Clear the bucket for a single token (e.g. on token deletion)."""
        async with self._lock:
            self._buckets.pop(token_id, None)

    async def clear_all(self) -> None:
        """Clear all buckets (useful for testing)."""
        async with self._lock:
            self._buckets.clear()
