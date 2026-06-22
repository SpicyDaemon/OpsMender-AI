"""Secure foundation for future native incident actions in chat adapters."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config_loader import AppConfig
from backend.db.models import BotConnector, User, UserOrganization
from backend.db.repos import (
    BotActionAuditRepo,
    BotUserLinkRepo,
    IncidentAssignmentRepo,
    IncidentRepo,
    NativeActionInvocationRepo,
    SessionRepo,
    UserRepo,
)
from backend.paging import escalation as _escalation
from backend.services.session_orchestration import admit_session
from backend.tiers.resolution import resolve_session_tier_for_incident


SUPPORTED_INCIDENT_ACTIONS = {
    "acknowledge",
    "resolve",
    "escalate",
    "start_ai_session",
}
ACTIVE_SESSION_STATUSES = {"queued", "active", "awaiting_approval"}
TERMINAL_SESSION_STATUSES = {"completed", "failed", "timed_out"}


class IncidentActionError(ValueError):
    """Raised when an incident action cannot be authenticated or authorized."""


@dataclass(frozen=True)
class IncidentActionTokenClaims:
    org_id: uuid.UUID
    incident_id: uuid.UUID
    action: str
    channel_id: str | None
    message_id: str | None
    expires_at: datetime
    nonce: str


@dataclass(frozen=True)
class ExternalActorIdentity:
    platform_user_id: str | None = None
    username: str | None = None
    display_name: str | None = None
    email: str | None = None
    email_verified: bool = False


@dataclass(frozen=True)
class IncidentActionResult:
    action: str
    status: str
    incident_id: uuid.UUID
    actor_user_id: uuid.UUID
    session_id: uuid.UUID | None = None
    detail: str | None = None


@dataclass(frozen=True)
class VerifiedNativeAction:
    """Platform-verified callback normalized for the common action handler."""

    connector: BotConnector
    claims: IncidentActionTokenClaims
    external_actor: ExternalActorIdentity
    idempotency_key: str
    callback_received_at: datetime
    chat_id: str | None = None


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _canonical_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_incident_action_token(
    *,
    secret: str,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    action: str,
    channel_id: str | None = None,
    message_id: str | None = None,
    expires_at: datetime | None = None,
    nonce: str | None = None,
) -> str:
    if not secret:
        raise IncidentActionError("missing_action_secret")
    if action not in SUPPORTED_INCIDENT_ACTIONS:
        raise IncidentActionError("unsupported_action")
    exp = expires_at or datetime.now(timezone.utc) + timedelta(minutes=15)
    payload = {
        "org_id": str(org_id),
        "incident_id": str(incident_id),
        "action": action,
        "channel_id": channel_id,
        "message_id": message_id,
        "exp": int(exp.timestamp()),
        "nonce": nonce or uuid.uuid4().hex,
    }
    body = _b64encode(_canonical_json(payload))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256)
    return f"{body}.{_b64encode(sig.digest())}"


def verify_incident_action_token(
    token: str,
    *,
    secret: str,
    expected_action: str | None = None,
    now: datetime | None = None,
) -> IncidentActionTokenClaims:
    if not secret:
        raise IncidentActionError("missing_action_secret")
    try:
        body, raw_sig = token.split(".", 1)
        expected_sig = hmac.new(
            secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected_sig, _b64decode(raw_sig)):
            raise IncidentActionError("invalid_action_token")
        payload = json.loads(_b64decode(body))
        action = str(payload["action"])
        if action not in SUPPORTED_INCIDENT_ACTIONS:
            raise IncidentActionError("unsupported_action")
        if expected_action is not None and action != expected_action:
            raise IncidentActionError("action_mismatch")
        exp = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
        if exp <= (now or datetime.now(timezone.utc)):
            raise IncidentActionError("expired_action_token")
        return IncidentActionTokenClaims(
            org_id=uuid.UUID(str(payload["org_id"])),
            incident_id=uuid.UUID(str(payload["incident_id"])),
            action=action,
            channel_id=payload.get("channel_id"),
            message_id=payload.get("message_id"),
            expires_at=exp,
            nonce=str(payload["nonce"]),
        )
    except IncidentActionError:
        raise
    except Exception as exc:
        raise IncidentActionError("invalid_action_token") from exc


async def resolve_external_actor(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    identity: ExternalActorIdentity,
) -> User | None:
    if identity.platform_user_id:
        link = await BotUserLinkRepo.get_by_platform_user(
            db,
            org_id,
            connector_id=connector.id,
            platform_user_id=identity.platform_user_id,
        )
        if link is not None and link.verified:
            await BotUserLinkRepo.mark_seen(
                db,
                org_id,
                link.id,
                external_username=identity.username,
                external_display_name=identity.display_name,
            )
            return await UserRepo.get_by_id(db, link.opsmender_user_id)
    if identity.email and identity.email_verified:
        return await UserRepo.get_by_email(db, identity.email.lower().strip())
    return None


async def _authorized_operator(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
) -> User:
    user = await UserRepo.get_by_id(db, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise IncidentActionError("actor_not_active")
    membership = (
        await db.execute(
            select(UserOrganization).where(
                UserOrganization.org_id == org_id,
                UserOrganization.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise IncidentActionError("actor_not_authorized")
    role = membership.role
    if role not in {"admin", "operator"}:
        raise IncidentActionError("actor_not_authorized")
    return user


async def resolve_authorized_external_actor(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    identity: ExternalActorIdentity,
) -> User:
    """Resolve a verified external identity and enforce active operator RBAC."""

    actor = await resolve_external_actor(
        db,
        org_id=org_id,
        connector=connector,
        identity=identity,
    )
    if actor is None:
        raise IncidentActionError("actor_not_linked")
    return await _authorized_operator(db, org_id=org_id, user_id=actor.id)


async def execute_incident_action(
    db: AsyncSession,
    *,
    claims: IncidentActionTokenClaims,
    actor_user_id: uuid.UUID | None = None,
    connector: BotConnector | None = None,
    external_actor: ExternalActorIdentity | None = None,
    channel_factory: async_sessionmaker[AsyncSession] | None = None,
    config: AppConfig | None = None,
) -> IncidentActionResult:
    """Execute a verified incident action.

    Signed token claims identify the incident/action context. They are not
    authorization. A caller must also provide either an authenticated OpsMender
    user id or a verified external actor resolvable through a connector link.
    """

    if actor_user_id is None:
        if connector is None or external_actor is None:
            raise IncidentActionError("actor_required")
        actor = await resolve_external_actor(
            db,
            org_id=claims.org_id,
            connector=connector,
            identity=external_actor,
        )
        if actor is None:
            raise IncidentActionError("actor_not_linked")
        actor_user_id = actor.id

    await _authorized_operator(db, org_id=claims.org_id, user_id=actor_user_id)
    incident = await IncidentRepo.get_by_id(db, claims.org_id, claims.incident_id)
    if incident is None:
        raise IncidentActionError("incident_not_found")

    if claims.action == "acknowledge":
        await SessionRepo.cancel_queued_for_incident(
            db,
            claims.org_id,
            claims.incident_id,
            reason="Incident was acknowledged before AI capacity became available.",
        )
        active = await IncidentAssignmentRepo.get_active(
            db, claims.org_id, claims.incident_id
        )
        if active is not None and active.assigned_to == actor_user_id:
            return IncidentActionResult(
                action=claims.action,
                status="already_acknowledged",
                incident_id=claims.incident_id,
                actor_user_id=actor_user_id,
            )
        await _escalation.handle_ack(
            db,
            claims.org_id,
            incident_id=claims.incident_id,
            user_id=actor_user_id,
        )
        return IncidentActionResult(
            action=claims.action,
            status="acknowledged",
            incident_id=claims.incident_id,
            actor_user_id=actor_user_id,
        )

    if claims.action == "resolve":
        await SessionRepo.cancel_queued_for_incident(
            db,
            claims.org_id,
            claims.incident_id,
            reason="Incident was resolved before AI capacity became available.",
        )
        if incident.status == "resolved":
            return IncidentActionResult(
                action=claims.action,
                status="already_resolved",
                incident_id=claims.incident_id,
                actor_user_id=actor_user_id,
            )
        await _escalation.cancel_chain(
            db,
            claims.org_id,
            incident_id=claims.incident_id,
        )
        await IncidentRepo.update_status(
            db, claims.org_id, claims.incident_id, "resolved"
        )
        return IncidentActionResult(
            action=claims.action,
            status="resolved",
            incident_id=claims.incident_id,
            actor_user_id=actor_user_id,
        )

    if claims.action == "escalate":
        if channel_factory is None:
            raise IncidentActionError("channel_factory_required")
        outcome = await _escalation.escalate_now(
            db,
            claims.org_id,
            incident_id=claims.incident_id,
            channel_factory=channel_factory,
        )
        return IncidentActionResult(
            action=claims.action,
            status="escalated" if outcome else "no_escalation_target",
            incident_id=claims.incident_id,
            actor_user_id=actor_user_id,
        )

    if claims.action == "start_ai_session":
        sessions = await SessionRepo.list_by_incident(
            db, claims.org_id, claims.incident_id
        )
        for session in sessions:
            if session.status in ACTIVE_SESSION_STATUSES:
                return IncidentActionResult(
                    action=claims.action,
                    status="already_active",
                    incident_id=claims.incident_id,
                    actor_user_id=actor_user_id,
                    session_id=session.id,
                )
        resolved_tier = await resolve_session_tier_for_incident(
            db,
            claims.org_id,
            config,
            incident=incident,
        )
        admission = await admit_session(
            db,
            claims.org_id,
            tier=resolved_tier,
            incident=incident,
            actor_user_id=actor_user_id,
            queue_ttl_seconds=(
                config.sessions.queue_ttl_seconds if config is not None else 900
            ),
        )
        session = admission.session
        return IncidentActionResult(
            action=claims.action,
            status="session_queued" if admission.queued else "session_started",
            incident_id=claims.incident_id,
            actor_user_id=actor_user_id,
            session_id=session.id,
            detail=admission.warning,
        )

    raise IncidentActionError("unsupported_action")


async def execute_verified_native_action(
    db: AsyncSession,
    *,
    request: VerifiedNativeAction,
    channel_factory: async_sessionmaker[AsyncSession] | None = None,
    config: AppConfig | None = None,
) -> IncidentActionResult:
    """Deduplicate, authorize, execute, and audit a verified chat callback.

    Platform routes must verify their native signature/secret before creating
    this request. This function deliberately has no ``verified=False`` mode.
    """

    connector = request.connector
    claims = request.claims
    external_user_id = (request.external_actor.platform_user_id or "").strip()
    idempotency_key = request.idempotency_key.strip()

    if connector.org_id != claims.org_id:
        raise IncidentActionError("connector_org_mismatch")
    if connector.platform not in {"slack", "teams", "discord", "telegram"}:
        raise IncidentActionError("unsupported_callback_platform")
    if not connector.is_enabled:
        raise IncidentActionError("connector_disabled")
    if not connector.native_actions_enabled:
        raise IncidentActionError("native_actions_disabled")
    if connector.callback_status != "verified":
        raise IncidentActionError("callback_not_verified")
    if not external_user_id:
        raise IncidentActionError("external_user_required")
    if not idempotency_key:
        raise IncidentActionError("idempotency_key_required")

    invocation, reserved = await NativeActionInvocationRepo.reserve(
        db,
        claims.org_id,
        connector_id=connector.id,
        platform=connector.platform,
        idempotency_key=idempotency_key,
        incident_id=claims.incident_id,
        action=claims.action,
        external_user_id=external_user_id,
        callback_received_at=request.callback_received_at,
    )
    if not reserved:
        await BotActionAuditRepo.create(
            db,
            claims.org_id,
            connector_id=connector.id,
            platform=connector.platform,
            chat_id=request.chat_id,
            command=claims.action,
            status="native_action_deduplicated",
            detail=invocation.status,
            session_id=invocation.session_id,
            incident_id=claims.incident_id,
            actor_user_id=invocation.actor_user_id,
            external_user_id=external_user_id,
            idempotency_key=idempotency_key,
        )
        if invocation.status == "applied" and invocation.actor_user_id is not None:
            return IncidentActionResult(
                action=claims.action,
                status=invocation.result_status or "deduplicated",
                incident_id=claims.incident_id,
                actor_user_id=invocation.actor_user_id,
                session_id=invocation.session_id,
                detail="deduplicated",
            )
        if invocation.status == "rejected":
            raise IncidentActionError(invocation.error_code or "action_rejected")
        raise IncidentActionError("action_in_progress")
    await BotActionAuditRepo.create(
        db,
        claims.org_id,
        connector_id=connector.id,
        platform=connector.platform,
        chat_id=request.chat_id,
        command=claims.action,
        status="callback_verified",
        incident_id=claims.incident_id,
        external_user_id=external_user_id,
        idempotency_key=idempotency_key,
    )

    try:
        result = await execute_incident_action(
            db,
            claims=claims,
            connector=connector,
            external_actor=request.external_actor,
            channel_factory=channel_factory,
            config=config,
        )
    except IncidentActionError as exc:
        error_code = str(exc)
        await NativeActionInvocationRepo.finish(
            db,
            invocation,
            status="rejected",
            error_code=error_code,
        )
        await BotActionAuditRepo.create(
            db,
            claims.org_id,
            connector_id=connector.id,
            platform=connector.platform,
            chat_id=request.chat_id,
            command=claims.action,
            status="native_action_rejected",
            detail=error_code,
            incident_id=claims.incident_id,
            external_user_id=external_user_id,
            idempotency_key=idempotency_key,
        )
        # Rejections must remain auditable even though the public callback
        # route will translate the exception into a non-2xx response.
        await db.commit()
        raise

    await NativeActionInvocationRepo.finish(
        db,
        invocation,
        status="applied",
        actor_user_id=result.actor_user_id,
        result_status=result.status,
        session_id=result.session_id,
    )
    await BotActionAuditRepo.create(
        db,
        claims.org_id,
        connector_id=connector.id,
        platform=connector.platform,
        chat_id=request.chat_id,
        command=claims.action,
        status="native_action_applied",
        detail=result.status,
        session_id=result.session_id,
        incident_id=claims.incident_id,
        actor_user_id=result.actor_user_id,
        external_user_id=external_user_id,
        idempotency_key=idempotency_key,
    )
    return result
