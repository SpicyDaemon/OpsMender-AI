"""Helpers for named REST API bearer tokens."""

from __future__ import annotations

import hashlib
import secrets

API_TOKEN_PREFIX = "omk_"


def hash_api_token(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def mint_api_token() -> tuple[str, str, str]:
    secret = f"{API_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    return secret, secret[:12], hash_api_token(secret)
