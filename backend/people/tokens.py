"""Opaque-token mint + hash helpers shared by invites + password resets.

We mint a URL-safe random token, return the raw value to the admin exactly
once, and persist only the sha256 hash. Validation: hash the bearer
token and look up by hash.

256 bits of entropy from ``secrets.token_urlsafe(32)``; the resulting
string is ~43 characters and safe to embed in a URL path segment.
"""

from __future__ import annotations

import hashlib
import secrets


def mint() -> tuple[str, str]:
    """Return ``(raw_token, sha256_hex)``.

    Persist the hash; return the raw token to the caller exactly once.
    """

    raw = secrets.token_urlsafe(32)
    return raw, _hash(raw)


def hash_token(raw: str) -> str:
    """Hash a bearer token for lookup against the persisted ``token_hash``."""

    return _hash(raw)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
