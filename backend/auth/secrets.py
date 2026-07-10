"""Symmetric encryption for tenant-scoped secrets at rest (e.g. SSO client
secrets).

Uses Fernet (AES-128-CBC + HMAC-SHA256) from ``cryptography``. The key is
derived deterministically from ``OPSMENDER_SECRET_KEY`` if set, otherwise from
``OPSMENDER_JWT_SECRET`` so existing single-tenant deployments don't need a new
env var. If neither is set we raise — secrets must never be written to the
DB in plain text.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _derive_key() -> bytes:
    seed = os.environ.get("OPSMENDER_SECRET_KEY") or os.environ.get(
        "OPSMENDER_JWT_SECRET"
    )
    if not seed:
        # Fall back to the loaded AppConfig (which reads .env via python-dotenv).
        try:
            from backend.config_loader import AppConfig

            seed = AppConfig.load().auth.jwt_secret
        except Exception:
            seed = None
    if not seed:
        raise RuntimeError(
            "Neither OPSMENDER_SECRET_KEY nor OPSMENDER_JWT_SECRET is set. "
            "Set OPSMENDER_SECRET_KEY (preferred) to enable encrypted secret storage."
        )
    return base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest())


def _fernet() -> Fernet:
    return Fernet(_derive_key())


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a UTF-8 string and return the URL-safe base64 ciphertext."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a Fernet token written by ``encrypt_secret``.

    Raises ``ValueError`` if the ciphertext was produced under a different
    key (e.g. OPSMENDER_SECRET_KEY was rotated).
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Failed to decrypt secret — the encryption key has changed."
        ) from exc
