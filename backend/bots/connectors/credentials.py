"""Sealed connector credentials: values stored as ``enc:<Fernet token>``.

The ``bot_connectors.credentials`` JSON column holds whatever a platform
needs. For the platforms in :data:`SEALED_PLATFORMS`, every value is sealed
with the project-wide key before it is written (M1-100 for Teams Workflows,
Google Chat and EventBridge; M1-113 for Slack), and read back through
:func:`readable`, which also accepts a row written before its platform was
sealed. A value this server's key cannot open reads as ``None``, so a
consumer reports "enter it again" instead of sending ciphertext to the
provider.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from backend.auth.secrets import decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)

ENCRYPTED_PREFIX = "enc:"

# Platforms whose stored credential values are sealed on every write.
SEALED_PLATFORMS = frozenset({"eventbridge", "google_chat", "teams_workflows", "slack"})

# What a consumer tells the operator when a stored value does not open with
# this server's key: the key changed, or another instance wrote the row.
UNREADABLE_HINT = "cannot be read with this server's secret key; enter it again"


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)


def seal(value: str) -> str:
    """``enc:<token>`` for *value*; a value already sealed is returned as is."""
    if is_sealed(value):
        return value
    return ENCRYPTED_PREFIX + encrypt_secret(value)


def plain(value: object) -> str:
    """The stored value in plain text. Raises ``ValueError`` when the value is
    sealed under another key and ``RuntimeError`` when no key is configured."""
    text = str(value or "")
    if text.startswith(ENCRYPTED_PREFIX):
        return decrypt_secret(text[len(ENCRYPTED_PREFIX) :])
    return text


def readable(value: object) -> str | None:
    """The stored value in plain text, stripped, or ``None`` when it is empty
    or this server's key cannot open it."""
    try:
        text = plain(value).strip()
    except (ValueError, RuntimeError):
        return None
    return text or None


def seal_credentials(
    platform: str, credentials: Mapping[str, object] | None
) -> dict[str, object] | None:
    """The credentials to store for *platform*: sealed for a sealed platform,
    unchanged otherwise. Empty values stay empty."""
    if credentials is None:
        return None
    if platform not in SEALED_PLATFORMS:
        return dict(credentials)
    sealed: dict[str, object] = {}
    for key, value in credentials.items():
        if value is None or value == "":
            sealed[key] = value
            continue
        sealed[key] = seal(str(value))
    return sealed


def has_plaintext(platform: str, credentials: Mapping[str, object] | None) -> bool:
    """Whether a sealed platform's row still holds a value in the clear."""
    if not credentials or platform not in SEALED_PLATFORMS:
        return False
    return any(
        value not in (None, "") and not is_sealed(value)
        for value in credentials.values()
    )


async def seal_legacy_credentials(session_factory) -> int:
    """Seal every Slack connector row written before M1-113, in place.

    Runs at startup. Returns how many rows changed. Without a configured key
    nothing is written and one warning says so; the rows keep working because
    every consumer reads plain values through :func:`readable`. No value is
    logged.
    """
    from sqlalchemy import select

    from backend.db.models import BotConnector

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(BotConnector).where(BotConnector.platform == "slack")
                )
            )
            .scalars()
            .all()
        )
        pending = [row for row in rows if has_plaintext(row.platform, row.credentials)]
        if not pending:
            return 0
        try:
            sealed = {
                row.id: seal_credentials(row.platform, row.credentials)
                for row in pending
            }
        except RuntimeError as exc:
            log.warning(
                "connectors: %d Slack connector(s) keep plain credentials: %s",
                len(pending),
                exc,
            )
            return 0
        for row in pending:
            row.credentials = sealed[row.id]
        await db.commit()
    log.info("connectors: sealed credentials of %d Slack connector(s)", len(pending))
    return len(pending)
