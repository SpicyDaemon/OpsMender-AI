"""Platform-neutral execution for cryptographically verified native actions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config_loader import AppConfig
from backend.bots.action_ids import ACTION_ID_MAP
from backend.bots.actions import (
    ExternalActorIdentity,
    IncidentActionError,
    IncidentActionResult,
    IncidentActionTokenClaims,
    VerifiedNativeAction,
    execute_verified_native_action,
)
from backend.db.models import BotConnector
from backend.db.repos import BotConnectorRepo
from backend.paging.channel_factory import build_channel_factory


ERROR_MESSAGES = {
    "native_actions_disabled": "Native actions are disabled for this channel.",
    "actor_not_linked": (
        "Your external account isn't linked to OpsMender. "
        "Ask an admin to add an identity link, then try again."
    ),
    "actor_not_active": "Your linked OpsMender account is inactive.",
    "actor_not_authorized": "Your OpsMender role cannot perform this action.",
    "incident_not_found": "That incident no longer exists.",
    "action_in_progress": "That action is already being processed.",
}

RESULT_MESSAGES = {
    "acknowledged": "acknowledged",
    "already_acknowledged": "already acknowledged",
    "resolved": "resolved",
    "already_resolved": "already resolved",
    "escalated": "escalated",
    "no_escalation_target": "found no further escalation target for",
    "session_started": "started an AI session for",
    "session_queued": "queued an AI session for",
    "already_active": "found an active AI session for",
}


@dataclass(frozen=True)
class NormalizedNativeCallback:
    """Action context produced only after platform verification succeeds."""

    incident_id: uuid.UUID
    action_id: str
    external_actor: ExternalActorIdentity
    idempotency_key: str
    channel_id: str | None = None
    message_id: str | None = None
    callback_received_at: datetime | None = None


def callback_error_message(error: IncidentActionError) -> str:
    code = str(error)
    return ERROR_MESSAGES.get(code, f"Action rejected: {code}.")


def callback_result_message(result: IncidentActionResult, title: str) -> str:
    verb = RESULT_MESSAGES.get(result.status, result.status.replace("_", " "))
    return f"{verb} incident *{title}*."


async def execute_normalized_callback(
    db: AsyncSession,
    *,
    connector: BotConnector,
    callback: NormalizedNativeCallback,
    config: AppConfig | None = None,
) -> IncidentActionResult:
    """Promote callback readiness and invoke the common action coordinator."""

    action = ACTION_ID_MAP.get(callback.action_id)
    if action is None:
        raise IncidentActionError("unsupported_action")

    received_at = callback.callback_received_at or datetime.now(timezone.utc)
    await BotConnectorRepo.mark_callback_verified(db, connector.org_id, connector.id)
    connector.callback_status = "verified"
    connector.callback_last_verified_at = received_at

    claims = IncidentActionTokenClaims(
        org_id=connector.org_id,
        incident_id=callback.incident_id,
        action=action,
        channel_id=callback.channel_id,
        message_id=callback.message_id,
        expires_at=received_at + timedelta(minutes=5),
        nonce=callback.idempotency_key,
    )
    return await execute_verified_native_action(
        db,
        request=VerifiedNativeAction(
            connector=connector,
            claims=claims,
            external_actor=callback.external_actor,
            idempotency_key=callback.idempotency_key,
            callback_received_at=received_at,
            chat_id=callback.channel_id,
        ),
        channel_factory=build_channel_factory(),
        config=config,
    )


async def dispatch_native_session_result(
    app,
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    result: IncidentActionResult,
) -> None:
    """Commit and dispatch a verified chat session start after admission."""
    if result.session_id is None or result.status not in {
        "session_started",
        "session_queued",
    }:
        return
    await db.commit()
    if result.status == "session_queued":
        from backend.bots.notifier import schedule_session_chat_event

        schedule_session_chat_event(
            app.state.session_factory,
            org_id=org_id,
            task_registry=app.state.background_tasks,
            event_type="session.queued",
            session_id=result.session_id,
        )
        return

    from backend.services.session_orchestration import dispatch_session_ready

    await dispatch_session_ready(app, result.session_id)
