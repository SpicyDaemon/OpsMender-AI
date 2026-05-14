"""In-memory rate limiter for inbound bot connector commands.

A simple sliding-window counter per (connector_id, scope_key). Scope key is
typically the chat ID. Limits live on the connector's ``config`` JSON under
``rate_limit_per_minute`` (default 30). Set to 0 to disable.

The state is process-local — appropriate for single-process OpsMender deployments
(the v1 target). When OpsMender is run behind multiple workers, each worker has
its own counter; effective limit becomes ``rate_limit_per_minute * N``.
This is acceptable for v1 since these limits exist as abuse-control floor,
not exact-quota accounting.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Deque


_DEFAULT_PER_MINUTE = 30
_WINDOW_SECONDS = 60.0


class _Counter:
    __slots__ = ("hits",)

    def __init__(self) -> None:
        self.hits: Deque[float] = deque()


class BotRateLimiter:
    """Sliding-window per-(connector, scope) limiter."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[uuid.UUID, str], _Counter] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def check(
        self,
        connector_id: uuid.UUID,
        scope_key: str,
        *,
        per_minute: int,
    ) -> tuple[bool, int]:
        """Record a new request and return ``(allowed, remaining)``.

        ``per_minute <= 0`` disables limiting (always allowed, remaining = 0).
        """
        if per_minute <= 0:
            return True, 0

        now = time.monotonic()
        cutoff = now - _WINDOW_SECONDS
        key = (connector_id, scope_key)

        with self._lock:
            counter = self._buckets.get(key)
            if counter is None:
                counter = _Counter()
                self._buckets[key] = counter

            while counter.hits and counter.hits[0] < cutoff:
                counter.hits.popleft()

            if len(counter.hits) >= per_minute:
                remaining = 0
                return False, remaining

            counter.hits.append(now)
            remaining = max(0, per_minute - len(counter.hits))
            return True, remaining


# Process-wide instance. Tests reset via ``rate_limiter.reset()``.
rate_limiter = BotRateLimiter()


def resolve_per_minute(config: dict | None) -> int:
    """Read the per-minute limit from a connector config dict."""
    if not config:
        return _DEFAULT_PER_MINUTE
    raw = config.get("rate_limit_per_minute")
    if raw is None:
        return _DEFAULT_PER_MINUTE
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PER_MINUTE
    return max(0, value)
