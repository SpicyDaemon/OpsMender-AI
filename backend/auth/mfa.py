"""TOTP and recovery-code primitives for local-account MFA."""

from __future__ import annotations

import base64
import io
import secrets
from datetime import datetime, timezone

import bcrypt
import pyotp
import qrcode


def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=email,
        issuer_name="OpsMender",
    )


def qr_data_url(value: str) -> str:
    image = qrcode.make(value)
    output = io.BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def matching_totp_counter(
    secret: str,
    code: str,
    *,
    at: datetime | None = None,
    valid_window: int = 1,
) -> int | None:
    now = at or datetime.now(timezone.utc)
    totp = pyotp.TOTP(secret)
    current = totp.timecode(now)
    for offset in range(-valid_window, valid_window + 1):
        counter = current + offset
        if counter >= 0 and secrets.compare_digest(
            totp.generate_otp(counter),
            code.strip(),
        ):
            return counter
    return None


def generate_recovery_codes(count: int = 8) -> list[str]:
    return [
        f"{secrets.token_hex(2)}-{secrets.token_hex(2)}".upper()
        for _ in range(count)
    ]


def hash_recovery_code(code: str) -> str:
    normalized = code.strip().upper().encode("utf-8")
    return bcrypt.hashpw(normalized, bcrypt.gensalt()).decode("utf-8")


def find_recovery_code(code: str, hashes: list[str]) -> int | None:
    normalized = code.strip().upper().encode("utf-8")
    for index, hashed in enumerate(hashes):
        if bcrypt.checkpw(normalized, hashed.encode("utf-8")):
            return index
    return None
