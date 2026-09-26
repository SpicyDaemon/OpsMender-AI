"""Failed sign-in throttling (KI-014).

Counts failed attempts in a sliding window per (account, client address) and
per client address. Like the intake and chat-command limiters, the counts live
in memory on each app replica. A blocked caller gets 429 before any credential
is checked, so a correct password is refused too until the window moves on.

Why not lock an account outright: anyone who knows an on-call admin's
username could then lock them out from anywhere. Keying on the address as
well stops guessing from one address without that, and the per-address limit
stops one address spraying many accounts.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# Prune expired buckets once the table grows past this many keys.
_PRUNE_AT = 10_000


@dataclasses.dataclass(frozen=True)
class Lockout:
    scope: str  # "account" or "address"
    retry_after: float


class SignInThrottle:
    def __init__(
        self,
        *,
        account_limit: int = 5,
        address_limit: int = 100,
        window_seconds: int = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.account_limit = account_limit
        self.address_limit = address_limit
        self.window = window_seconds
        self._clock = clock
        self._failures: dict[tuple, deque[float]] = {}

    def _limits(self, address: str, account: str | None):
        if self.address_limit > 0:
            yield ("address", address), self.address_limit, "address"
        if account is not None and self.account_limit > 0:
            yield ("account", account, address), self.account_limit, "account"

    def _recent(self, key: tuple) -> deque[float]:
        bucket = self._failures.get(key)
        if bucket is None:
            return deque()
        cutoff = self._clock() - self.window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            del self._failures[key]
        return bucket

    def retry_after(self, *, address: str, account: str | None = None) -> float | None:
        """Seconds until this caller may try again, or None when not blocked."""
        for key, limit, _scope in self._limits(address, account):
            bucket = self._recent(key)
            if len(bucket) >= limit:
                return max(1.0, bucket[0] + self.window - self._clock())
        return None

    def record_failure(
        self, *, address: str, account: str | None = None
    ) -> list[Lockout]:
        """Count one failure; return the limits this failure just reached."""
        if len(self._failures) > _PRUNE_AT:
            for key in list(self._failures):
                self._recent(key)
        now = self._clock()
        reached: list[Lockout] = []
        for key, limit, scope in self._limits(address, account):
            bucket = self._recent(key)
            if not bucket:
                bucket = self._failures.setdefault(key, deque())
            bucket.append(now)
            if len(bucket) == limit:
                reached.append(Lockout(scope=scope, retry_after=float(self.window)))
        return reached

    def clear(self, *, address: str, account: str) -> None:
        """A success clears that account's failures from that address."""
        self._failures.pop(("account", account, address), None)


def client_address(request: Request) -> str:
    """The caller's address as uvicorn reports it.

    Behind a reverse proxy, set ``FORWARDED_ALLOW_IPS`` to the proxy's address
    so this is the real client, not the proxy.
    """
    return request.client.host if request.client else "unknown"


async def _audit_lockout(
    *, endpoint: str, scope: str, address: str, account: str | None
) -> None:
    """Record a lockout in its own session, so a failed request's partial
    changes are never committed along with it."""
    from backend.api.deps import get_current_session_factory
    from backend.db.repos import AuditEntryRepo, OrganizationRepo

    try:
        factory = get_current_session_factory()
        async with factory() as db:
            orgs = await OrganizationRepo.list_all(db)
            if not orgs:
                return
            await AuditEntryRepo.create(
                db,
                orgs[0].id,
                session_id=None,
                tier=0,
                entry_type="sign_in_lockout",
                tool_name="sign_in",
                tool_parameters={
                    "endpoint": endpoint,
                    "scope": scope,
                    "address": address,
                    "account": account,
                },
                result={"locked": True},
                permitted=False,
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - auditing must never break sign-in
        logger.exception("sign-in lockout audit failed endpoint=%s", endpoint)


@asynccontextmanager
async def sign_in_attempt(
    request: Request,
    *,
    endpoint: str,
    account: str | None = None,
    failures: tuple[int, ...] = (400, 401, 403),
) -> AsyncIterator[None]:
    """Refuse a blocked caller with 429, then count the attempt's outcome.

    Only an ``HTTPException`` with a status in ``failures`` counts; a
    success clears the (account, address) count.
    """
    throttle: SignInThrottle | None = getattr(
        request.app.state, "signin_throttle", None
    )
    if throttle is None:
        yield
        return
    address = client_address(request)
    wait = throttle.retry_after(address=address, account=account)
    if wait is not None:
        minutes = max(1, math.ceil(wait / 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Too many failed attempts. Try again in "
                f"{minutes} minute{'s' if minutes != 1 else ''}."
            ),
            headers={"Retry-After": str(math.ceil(wait))},
        )
    try:
        yield
    except HTTPException as exc:
        if exc.status_code in failures:
            for lockout in throttle.record_failure(address=address, account=account):
                await _audit_lockout(
                    endpoint=endpoint,
                    scope=lockout.scope,
                    address=address,
                    account=account,
                )
        raise
    else:
        if account is not None:
            throttle.clear(address=address, account=account)
