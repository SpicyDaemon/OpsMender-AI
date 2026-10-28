"""Signed action links for chat cards (M1-99).

A card that cannot carry buttons that act inside the chat (a Teams Workflows
card can only open links) can carry one link per incident action; nothing
posts these links yet, the Teams Workflows connector will. The link opens a
confirm page in OpsMender, and opening it never acts, since link scanners in
mail and chat follow links. The person signs in, sees the incident and the
action, and confirms; the action then runs once, under their own role, through
the same code as a chat button. The token rides in the URL fragment, so it
never reaches a server or proxy log.

The token names the org, the incident, the action, the Notification Channel
whose card carried it, an expiry and a nonce, signed with a key derived from
the JWT secret. It is not authorization: the person must be signed in and their
role must allow the action. A use is recorded as a native action invocation
keyed by the nonce, written in the same transaction as the action, so a second
use or a double submit loses. A refused attempt records no use, so the link
stays usable by someone who may act.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.bots.actions import (
    IncidentActionError,
    IncidentActionResult,
    IncidentActionTokenClaims,
    execute_incident_action,
    make_incident_action_token,
    verify_incident_action_token,
)
from backend.bots.incident_card import public_base_url
from backend.config_loader import AppConfig
from backend.db.models import BotConnector, Incident, NativeActionInvocation
from backend.db.repos import BotConnectorRepo, IncidentRepo, NativeActionInvocationRepo

LINK_PATH = "/actions/confirm"
VIA = "link"
KEY_PREFIX = "link:"
MIN_TTL_MINUTES = 5
MAX_TTL_MINUTES = 7 * 24 * 60
_KEY_PURPOSE = b"opsmender incident action links"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# An AI session the admission refused; the reason travels as the cause.
SESSION_REFUSED = "session_not_admitted"

# What a link that cannot be used says, by state.
REFUSALS = {
    "invalid": "invalid_action_token",
    "channel_off": "connector_disabled",
    "used": "link_used",
    "expired": "expired_action_token",
}


@dataclass(frozen=True)
class ActionLink:
    """A link's state for the signed-in person's org: ready, used, expired,
    channel_off (the channel that posted it is turned off) or invalid."""

    state: str
    claims: IncidentActionTokenClaims | None = None
    incident: Incident | None = None
    connector: BotConnector | None = None
    use: NativeActionInvocation | None = None


def link_secret(config: AppConfig) -> str:
    """The key that signs action links. Derived from the JWT secret, so a link
    and a sign-in token can never stand in for each other."""
    return hmac.new(
        config.auth.jwt_secret.encode("utf-8"), _KEY_PURPOSE, hashlib.sha256
    ).hexdigest()


def link_ttl(config: AppConfig) -> timedelta:
    minutes = config.auth.action_link_ttl_minutes
    return timedelta(minutes=min(max(minutes, MIN_TTL_MINUTES), MAX_TTL_MINUTES))


def action_link(
    config: AppConfig,
    *,
    connector: BotConnector,
    incident_id: uuid.UUID,
    action: str,
    base_url: str | None = None,
    now: datetime | None = None,
) -> str:
    """A fresh link to the confirm page for one action on one incident."""
    token = make_incident_action_token(
        secret=link_secret(config),
        org_id=connector.org_id,
        incident_id=incident_id,
        action=action,
        connector_id=connector.id,
        expires_at=(now or datetime.now(timezone.utc)) + link_ttl(config),
    )
    return f"{public_base_url(base_url)}{LINK_PATH}#token={token}"


async def read_link(
    db: AsyncSession,
    config: AppConfig,
    token: str,
    *,
    org_id: uuid.UUID,
    now: datetime | None = None,
) -> ActionLink:
    """Check a link without changing anything."""
    try:
        # The signature here and the expiry below, so an expired link can
        # still point at its incident.
        claims = verify_incident_action_token(
            token, secret=link_secret(config), now=_EPOCH
        )
    except IncidentActionError:
        return ActionLink("invalid")
    if claims.org_id != org_id or claims.connector_id is None:
        return ActionLink("invalid")
    connector = await BotConnectorRepo.get_by_id(db, org_id, claims.connector_id)
    incident = await IncidentRepo.get_by_id(db, org_id, claims.incident_id)
    if connector is None or incident is None:
        return ActionLink("invalid")
    if not connector.is_enabled:
        return ActionLink("channel_off", claims, incident, connector)
    use = await NativeActionInvocationRepo.get_by_key(
        db, org_id, connector.id, KEY_PREFIX + claims.nonce
    )
    if use is not None:
        return ActionLink("used", claims, incident, connector, use)
    if claims.expires_at <= (now or datetime.now(timezone.utc)):
        return ActionLink("expired", claims, incident, connector)
    return ActionLink("ready", claims, incident, connector)


async def use_link(
    db: AsyncSession,
    config: AppConfig,
    token: str,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    channel_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
) -> IncidentActionResult:
    """Run a link's action once for the signed-in person.

    Raises ``IncidentActionError`` with the reason when the link cannot be used
    or the person may not act; nothing is recorded as a use then.
    """
    now = now or datetime.now(timezone.utc)
    link = await read_link(db, config, token, org_id=org_id, now=now)
    if link.state != "ready":
        raise IncidentActionError(REFUSALS[link.state])
    claims, connector = link.claims, link.connector
    # The use and the action share one savepoint: a refusal rolls both back, so
    # the link stays usable, and a concurrent second use waits on the key and
    # then finds it taken.
    async with db.begin_nested():
        use, reserved = await NativeActionInvocationRepo.reserve(
            db,
            org_id,
            connector_id=connector.id,
            platform=connector.platform,
            idempotency_key=KEY_PREFIX + claims.nonce,
            incident_id=claims.incident_id,
            action=claims.action,
            external_user_id=f"user:{user_id}",
            callback_received_at=now,
        )
        if not reserved:
            raise IncidentActionError(REFUSALS["used"])
        try:
            result = await execute_incident_action(
                db,
                claims=claims,
                actor_user_id=user_id,
                channel_factory=channel_factory,
                config=config,
                via=VIA,
            )
        except IncidentActionError:
            raise
        except ValueError as exc:
            # Session admission refuses with a reason for the person (every
            # model at capacity, the incident no longer waiting for one).
            raise IncidentActionError(SESSION_REFUSED) from exc
        await NativeActionInvocationRepo.finish(
            db,
            use,
            status="applied",
            actor_user_id=result.actor_user_id,
            result_status=result.status,
            session_id=result.session_id,
        )
    return result
