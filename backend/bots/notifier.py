"""Outbound delivery of OpsMender events into chat connectors."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Iterable

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import backend.bots  # noqa: F401  -- registers built-in adapters
from backend.bots.capabilities import (
    supports_interactive_actions,
    supports_message_update,
)
from backend.bots.connectors import get_adapter
from backend.bots.delivery import DeliveryReceipt, UpdateResult
from backend.bots.incident_card import build_incident_message
from backend.db.models import BotActionAudit, BotConnector
from backend.db.repos import (
    BotActionAuditRepo,
    BotConnectorRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentNotificationReceiptRepo,
    IncidentTrackPostRepo,
    IncidentPageRepo,
    IncidentRepo,
    OrganizationRepo,
    ServiceRepo,
    SessionRepo,
    TeamRepo,
    UserRepo,
)


async def _resolve_org_name(db: AsyncSession, org_id: uuid.UUID) -> str | None:
    """Org name for comms surfaces (None only if it can't be found)."""
    org = await OrganizationRepo.get_by_id(db, org_id)
    return org.name if org is not None else None


log = logging.getLogger(__name__)


SESSION_CHAT_EVENTS = {
    "session.created",
    "session.queued",
    "session.started_from_queue",
    "session.queue_cancelled",
    "session.queue_expired",
    "session.awaiting_approval",
    "session.active",
    "session.completed",
    "session.failed",
    "session.timed_out",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _allowed_chat_ids(connector: BotConnector) -> list[str]:
    if connector.platform == "eventbridge":
        return ["eventbridge"]
    config = connector.config or {}
    raw = config.get("allowed_chat_ids") or []
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw if item is not None]
    default_chat_id = config.get("default_chat_id")
    return [str(default_chat_id)] if default_chat_id else []


def _has_capability(connector: BotConnector, capability: str) -> bool:
    return capability in set(connector.allowed_capabilities or [])


def _has_lane(connector: BotConnector, lane: str) -> bool:
    lanes = list(connector.lanes or [])
    if not lanes and _has_capability(connector, "notifications"):
        lanes = ["respond"]
    return lane in lanes


def _connector_team_scope(connector: BotConnector) -> tuple[str, set[uuid.UUID]]:
    config = connector.config or {}
    if config.get("team_scope") != "teams":
        return "workspace", set()
    team_ids: set[uuid.UUID] = set()
    for raw in config.get("team_ids") or []:
        try:
            team_ids.add(raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not team_ids:
        return "workspace", set()
    return "teams", team_ids


def _connector_matches_team(
    connector: BotConnector,
    team_id: uuid.UUID | None,
) -> bool:
    scope, team_ids = _connector_team_scope(connector)
    if scope == "workspace":
        return True
    if team_id is None:
        return False
    return team_id in team_ids


async def _resolve_incident_team(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident,
) -> tuple[uuid.UUID | None, str | None, str | None]:
    """Resolve incident ownership deterministically for channel filtering.

    Precedence: incident service -> service.team; incident escalation chain ->
    escalation_chain.team; no free-text or AI inference.
    """
    if incident is None:
        return None, None, None

    if incident.service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, incident.service_id)
        if service is not None:
            team = await TeamRepo.get_by_id(db, org_id, service.team_id)
            return (
                service.team_id,
                team.name if team is not None else None,
                service.name,
            )

    state = await IncidentChainStateRepo.get_for_incident(db, org_id, incident.id)
    if state is not None and state.chain_id is not None:
        from backend.db.repos import EscalationChainRepo

        chain = await EscalationChainRepo.get_by_id(db, org_id, state.chain_id)
        if chain is not None:
            team = await TeamRepo.get_by_id(db, org_id, chain.team_id)
            return chain.team_id, team.name if team is not None else None, None

    return None, None, None


def _auth_link(base_url: str | None, path: str) -> str:
    root = (base_url or os.environ.get("OPSMENDER_PUBLIC_URL") or "").rstrip("/")
    return f"{root}{path}" if root else path


def _format_session_event(
    *,
    event_type: str,
    session_id: uuid.UUID,
    session,
    incident,
    service_name: str | None = None,
    team_name: str | None = None,
    org_name: str | None = None,
    actor_name: str | None = None,
    base_url: str | None = None,
) -> str:
    label = event_type.replace("session.", "").replace("_", " ").title()
    if event_type == "session.created":
        headline = (
            f"*AI session started by {actor_name}*"
            if actor_name
            else "*AI session started*"
        )
    elif event_type == "session.queued":
        headline = "*AI session queued for capacity*"
    elif event_type == "session.started_from_queue":
        headline = "*AI session started after waiting for capacity*"
    elif event_type == "session.queue_cancelled":
        headline = "*Queued AI session cancelled*"
    elif event_type == "session.queue_expired":
        headline = "*Queued AI session expired*"
    elif event_type == "session.completed":
        headline = "*AI session completed*"
    elif event_type in {"session.failed", "session.timed_out"}:
        headline = "*AI session failed*"
    else:
        headline = f"*AI session {label.lower()}*"

    lines = [headline, f"Session ID: `{session_id}`"]
    if session is not None:
        lines.append(f"Session status: `{session.status}`")
    if incident is not None:
        lines.append(f"Incident: `{incident.title}` ({incident.severity or 'unknown'})")
    if service_name:
        lines.append(f"Service: `{service_name}`")
    if team_name:
        lines.append(f"Team: `{team_name}`")
    if org_name:
        lines.append(f"Org: `{org_name}`")
    # Richer completion post: surface the AI's summary so responders get the
    # outcome in-channel, not just a status change.
    if event_type == "session.completed" and session is not None:
        summary = (getattr(session, "summary", None) or "").strip()
        if summary:
            if len(summary) > 500:
                summary = summary[:499].rstrip() + "…"
            lines.append(f"Summary: {summary}")
    if incident is not None:
        lines.append(
            "Open incident/session: "
            + _auth_link(
                base_url,
                f"/dashboard/sessions/detail?id={session_id}",
            )
        )
    return "\n".join(lines)


def _format_copilot_reply(text: str) -> str:
    return f"*Co-pilot reply:*\n{text}"


async def _deliver_to_telegram(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    text: str,
    command_label: str,
    session_id: uuid.UUID | None,
) -> DeliveryReceipt:
    bot_token = (connector.credentials or {}).get("bot_token")
    from backend.bots.telegram import send_message as telegram_send

    ok, error = await telegram_send(
        bot_token=str(bot_token) if bot_token else "",
        chat_id=chat_id,
        text=text,
    )

    return DeliveryReceipt(
        ok=ok,
        error=error,
        external_channel_id=chat_id,
        can_update=False,
    )


async def _deliver_via_adapter(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    text: str,
    command_label: str,
    session_id: uuid.UUID | None,
    incident=None,
    native_actions_ready: bool = False,
    service_name: str | None = None,
    team_name: str | None = None,
    delivery_lane: str = "respond",
) -> DeliveryReceipt:
    adapter = get_adapter(connector.platform)
    if adapter is None:
        return DeliveryReceipt(ok=False, error="adapter_not_found")

    if hasattr(adapter, "send_incident_update") and command_label.startswith("notify:"):
        if supports_interactive_actions(connector.platform):
            kwargs = {
                "chat_id": chat_id,
                "text": text,
                "incident": incident,
                "native_actions_ready": native_actions_ready,
            }
            if delivery_lane == "track":
                kwargs["status_update"] = True
            receipt = await adapter.send_incident_update(connector, **kwargs)
        elif connector.platform == "eventbridge":
            receipt = await adapter.send_incident_update(
                connector,
                chat_id=chat_id,
                text=text,
                incident=incident,
                native_actions_ready=False,
                service_name=service_name,
                team_name=team_name,
            )
        else:
            receipt = await adapter.send_incident_update(
                connector,
                chat_id=chat_id,
                text=text,
            )
        if receipt.external_channel_id is None:
            return DeliveryReceipt(
                ok=receipt.ok,
                error=receipt.error,
                external_message_id=receipt.external_message_id,
                external_thread_id=receipt.external_thread_id,
                external_channel_id=chat_id,
                can_update=receipt.can_update,
            )
        return receipt

    ok, error = await adapter.send_message(connector, chat_id=chat_id, text=text)
    return DeliveryReceipt(
        ok=ok,
        error=error,
        external_channel_id=chat_id,
        can_update=False,
    )


async def _record_delivery(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    command_label: str,
    session_id: uuid.UUID | None,
    receipt: DeliveryReceipt,
    incident_id: uuid.UUID | None,
    lifecycle_event: str | None,
    rendered_status: str | None,
    delivery_status: str = "delivered",
    last_updated_at: datetime | None = None,
    delivery_lane: str = "respond",
) -> None:
    await BotActionAuditRepo.create(
        db,
        org_id,
        connector_id=connector.id,
        platform=connector.platform,
        chat_id=chat_id,
        command=command_label,
        status="ok" if receipt.ok else "delivery_failed",
        detail=None if receipt.ok else (receipt.error or "")[:1000],
        session_id=session_id,
    )
    if not receipt.ok:
        await BotConnectorRepo.mark_status(
            db,
            org_id,
            connector.id,
            status="error",
            error=(receipt.error or "")[:1000],
        )
        return
    if (
        delivery_lane == "track"
        and incident_id is not None
        and connector.platform != "eventbridge"
    ):
        await IncidentTrackPostRepo.upsert(
            db,
            org_id,
            incident_id=incident_id,
            connector_id=connector.id,
            external_message_id=receipt.external_message_id,
            channel_ref=receipt.external_channel_id or chat_id,
        )
    if incident_id is not None and lifecycle_event is not None:
        await IncidentNotificationReceiptRepo.create(
            db,
            org_id,
            incident_id=incident_id,
            connector_id=connector.id,
            platform=connector.platform,
            lifecycle_event=lifecycle_event,
            external_channel_id=receipt.external_channel_id or chat_id,
            external_message_id=receipt.external_message_id,
            external_thread_id=receipt.external_thread_id,
            rendered_status=rendered_status,
            can_update=receipt.can_update,
            session_id=session_id,
            delivery_status=delivery_status,
            last_updated_at=last_updated_at,
        )


async def _try_update_incident_notification(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    text: str,
    command_label: str,
    session_id: uuid.UUID | None,
    incident_id: uuid.UUID,
    lifecycle_event: str,
    rendered_status: str | None,
    incident=None,
    native_actions_ready: bool = False,
    delivery_lane: str = "respond",
) -> bool:
    """Edit the prior incident message in place when the platform supports it.

    Returns ``True`` only when the message was edited and recorded. Returns
    ``False`` — leaving the caller to post a fresh follow-up message — when the
    platform cannot edit, there is no updateable prior message, or the provider
    reports a recoverable edit failure. A recoverable failure is *not* treated
    as a connector error; only the follow-up post records the durable receipt.
    """
    # Update-in-place needs the incident object to re-render the full card; the
    # pre-built-text paths (escalation, session events) post a follow-up instead.
    if incident is None:
        return False
    if not supports_message_update(connector.platform):
        return False
    adapter = get_adapter(connector.platform)
    if adapter is None or not hasattr(adapter, "update_incident_update"):
        return False

    async with factory() as db:
        if delivery_lane == "track":
            prior = await IncidentTrackPostRepo.get(
                db,
                org_id,
                incident_id=incident_id,
                connector_id=connector.id,
            )
        else:
            prior = await IncidentNotificationReceiptRepo.latest_for_incident_channel(
                db,
                org_id,
                incident_id=incident_id,
                connector_id=connector.id,
                external_channel_id=chat_id,
                updateable_only=True,
            )
    if prior is None or prior.external_message_id is None:
        return False

    update_kwargs = {
        "chat_id": chat_id,
        "text": text,
        "external_message_id": prior.external_message_id,
        "external_thread_id": getattr(prior, "external_thread_id", None),
        "incident": incident,
        "native_actions_ready": native_actions_ready,
    }
    if delivery_lane == "track":
        update_kwargs["status_update"] = True
    result: UpdateResult = await adapter.update_incident_update(
        connector,
        **update_kwargs,
    )

    if not (result.ok and not result.fallback_to_followup):
        # Recoverable failure (or explicit fallback request): let the caller post
        # a new message. Audit the attempt without marking the connector errored.
        log.info(
            "incident notification update fell back to follow-up "
            "(platform=%s incident=%s): %s",
            connector.platform,
            incident_id,
            result.error,
        )
        return False

    receipt = result.receipt or DeliveryReceipt(
        ok=True,
        external_message_id=prior.external_message_id,
        external_thread_id=getattr(prior, "external_thread_id", None),
        external_channel_id=chat_id,
        can_update=True,
    )
    async with factory() as db:
        await _record_delivery(
            db,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            command_label=command_label,
            session_id=session_id,
            receipt=receipt,
            incident_id=incident_id,
            lifecycle_event=lifecycle_event,
            rendered_status=rendered_status,
            delivery_status="updated",
            last_updated_at=_utcnow(),
            delivery_lane=delivery_lane,
        )
        await db.commit()
    return True


async def _deliver(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    text: str,
    command_label: str,
    session_id: uuid.UUID | None,
    incident_id: uuid.UUID | None = None,
    lifecycle_event: str | None = None,
    rendered_status: str | None = None,
    incident=None,
    native_actions_ready: bool = False,
    delivery_lane: str = "respond",
    service_name: str | None = None,
    team_name: str | None = None,
) -> None:
    if incident_id is not None and lifecycle_event is not None:
        updated = await _try_update_incident_notification(
            factory,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            text=text,
            command_label=command_label,
            session_id=session_id,
            incident_id=incident_id,
            lifecycle_event=lifecycle_event,
            rendered_status=rendered_status,
            incident=incident,
            native_actions_ready=native_actions_ready,
            delivery_lane=delivery_lane,
        )
        if updated:
            return

    if connector.platform == "telegram":
        receipt = await _deliver_to_telegram(
            factory,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            text=text,
            command_label=command_label,
            session_id=session_id,
        )
    else:
        receipt = await _deliver_via_adapter(
            factory,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            text=text,
            command_label=command_label,
            session_id=session_id,
            incident=incident,
            native_actions_ready=native_actions_ready,
            service_name=service_name,
            team_name=team_name,
            delivery_lane=delivery_lane,
        )
    async with factory() as db:
        await _record_delivery(
            db,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            command_label=command_label,
            session_id=session_id,
            receipt=receipt,
            incident_id=incident_id,
            lifecycle_event=lifecycle_event,
            rendered_status=rendered_status,
            delivery_lane=delivery_lane,
        )
        await db.commit()


async def deliver_session_chat_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    event_type: str,
    session_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    base_url: str | None = None,
) -> None:
    if event_type not in SESSION_CHAT_EVENTS:
        return

    async with factory() as db:
        session = await SessionRepo.get_by_id(db, org_id, session_id)
        incident = None
        if session and session.incident_id:
            incident = await IncidentRepo.get_by_id(db, org_id, session.incident_id)
        team_id, team_name, service_name = await _resolve_incident_team(
            db, org_id, incident
        )
        org_name = await _resolve_org_name(db, org_id)
        actor = await UserRepo.get_by_id(db, actor_user_id) if actor_user_id else None
        connectors = list(
            await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        )

    if session is None:
        return

    text = _format_session_event(
        event_type=event_type,
        session_id=session_id,
        session=session,
        incident=incident,
        service_name=service_name,
        team_name=team_name,
        org_name=org_name,
        actor_name=_display_name(actor),
        base_url=base_url,
    )

    for connector in connectors:
        if not _has_capability(connector, "notifications"):
            continue
        if not _has_lane(connector, "respond"):
            continue
        if get_adapter(connector.platform) is None and connector.platform != "telegram":
            continue
        if not _connector_matches_team(connector, team_id):
            continue
        for chat_id in _allowed_chat_ids(connector):
            await _deliver(
                factory,
                org_id=org_id,
                connector=connector,
                chat_id=chat_id,
                text=text,
                command_label=f"notify:{event_type}",
                session_id=session_id,
                incident_id=incident.id if incident is not None else None,
                lifecycle_event=event_type if incident is not None else None,
                rendered_status=session.status if session is not None else None,
            )


def schedule_session_chat_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    task_registry: set[asyncio.Task] | None,
    event_type: str,
    session_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    base_url: str | None = None,
) -> asyncio.Task:
    task = asyncio.create_task(
        deliver_session_chat_event(
            factory,
            org_id=org_id,
            event_type=event_type,
            session_id=session_id,
            actor_user_id=actor_user_id,
            base_url=base_url,
        )
    )
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


INCIDENT_CHAT_EVENTS = {
    "incident.created",
    "incident.acknowledged",
    "incident.resolved",
    "incident.escalated",
    "incident.updated",
}


def _display_name(user) -> str | None:
    if user is None or getattr(user, "deleted_at", None) is not None:
        return None
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full or user.username


async def _resolve_incident_responder(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
) -> dict:
    """Lightweight responder snapshot for the card.

    Mirrors the incidents route precedence (acknowledged assignment wins,
    otherwise the latest escalation page) without importing the route module.
    Also surfaces escalation context — the level (latest page step index) and
    the previous responder paged before the current one — for escalation cards.
    """
    assignment = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    pages = list(await IncidentPageRepo.list_for_incident(db, org_id, incident_id))
    latest = pages[-1] if pages else None

    ack_uid = assignment.assigned_to if assignment is not None else None
    esc_uid = latest.user_id if latest is not None else None
    esc_step = latest.step_index if latest is not None else None

    if ack_uid is not None:
        state, resp_uid = "assigned", ack_uid
    elif esc_uid is not None:
        state = "escalated" if (esc_step or 0) > 0 else "awaiting"
        resp_uid = esc_uid
    else:
        state, resp_uid = "unassigned", None

    # Previous responder = the most recent page target *before* the latest one,
    # skipping repeats of the current target.
    prev_uid = None
    for page in reversed(pages[:-1]) if len(pages) > 1 else []:
        if page.user_id != esc_uid:
            prev_uid = page.user_id
            break

    async def _name(uid):
        if uid is None:
            return None
        return _display_name(await UserRepo.get_by_id(db, uid))

    return {
        "responder_state": state,
        "responder_display_name": await _name(resp_uid),
        "acknowledged_by_display_name": await _name(ack_uid),
        "escalation_level": esc_step,
        "previous_responder_display_name": await _name(prev_uid),
    }


async def deliver_incident_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    event_type: str,
    base_url: str | None = None,
) -> None:
    """Post an incident card/message to every enabled Notification Channel that
    has the ``notifications`` capability.

    Honest delivery: each platform receives the same useful incident message
    with an authenticated incident link. Interactive action controls are only
    rendered when the platform supports interactive callbacks and the channel
    has native actions enabled with a configured verifier. Slack and Teams
    verify every callback before the shared action coordinator permits a
    mutation.
    Delivery-only platforms (SMS, email, custom webhook, …) get the same
    message minus any card framing, which their adapters already handle.
    """
    if event_type not in INCIDENT_CHAT_EVENTS:
        return

    async with factory() as db:
        incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
        if incident is None:
            return

        team_id, team_name, service_name = await _resolve_incident_team(
            db, org_id, incident
        )
        org_name = await _resolve_org_name(db, org_id)

        responder = await _resolve_incident_responder(db, org_id, incident.id)
        connectors = list(
            await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        )

    for connector in connectors:
        if not _has_capability(connector, "notifications"):
            continue
        if get_adapter(connector.platform) is None and connector.platform != "telegram":
            continue
        if not _connector_matches_team(connector, team_id):
            continue
        is_track = _has_lane(connector, "track")
        if is_track and connector.platform not in {
            "slack",
            "teams",
            "discord",
            "google_chat",
            "eventbridge",
        }:
            continue
        if not is_track and not _has_lane(connector, "respond"):
            continue
        native_actions_ready = bool(
            supports_interactive_actions(connector.platform)
            and connector.native_actions_enabled
            and connector.callback_status in {"configured", "verified"}
            and not is_track
        )
        text = build_incident_message(
            incident,
            event_type=event_type,
            base_url=base_url,
            responder=responder,
            service_name=service_name,
            team_name=team_name,
            org_name=org_name,
            supports_actions=native_actions_ready,
        )
        chat_ids = _allowed_chat_ids(connector)
        if is_track:
            chat_ids = chat_ids[:1]
        for chat_id in chat_ids:
            await _deliver(
                factory,
                org_id=org_id,
                connector=connector,
                chat_id=chat_id,
                text=text,
                command_label=f"notify:{event_type}",
                session_id=None,
                incident_id=incident_id,
                lifecycle_event=event_type,
                rendered_status=incident.status,
                incident=incident,
                native_actions_ready=native_actions_ready,
                delivery_lane="track" if is_track else "respond",
                service_name=service_name,
                team_name=team_name,
            )


def schedule_incident_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    event_type: str,
    incident_id: uuid.UUID,
    base_url: str | None = None,
    task_registry: set[asyncio.Task] | None = None,
) -> asyncio.Task:
    task = asyncio.create_task(
        deliver_incident_event(
            factory,
            org_id=org_id,
            incident_id=incident_id,
            event_type=event_type,
            base_url=base_url,
        )
    )
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


async def deliver_incident_text(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    text: str,
    event_type: str,
    team_id: uuid.UUID | None = None,
    incident_id: uuid.UUID | None = None,
    rendered_status: str | None = None,
) -> None:
    """Fan out a *pre-built* incident message to enabled Notification Channels
    with the ``notifications`` capability.

    Used when the caller has already resolved the message from a live session
    (e.g. the escalation engine, where the new page row may not be committed
    yet) so the background delivery does not have to re-read incident state.
    """
    async with factory() as db:
        connectors = list(
            await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        )
        incident = (
            await IncidentRepo.get_by_id(db, org_id, incident_id)
            if incident_id is not None
            else None
        )
        resolved_team_id = team_id
        team_name = None
        service_name = None
        if incident is not None:
            resolved_team_id, team_name, service_name = await _resolve_incident_team(
                db, org_id, incident
            )

    for connector in connectors:
        if not _has_capability(connector, "notifications"):
            continue
        is_track = _has_lane(connector, "track")
        if is_track and connector.platform not in {
            "slack",
            "teams",
            "discord",
            "google_chat",
            "eventbridge",
        }:
            continue
        if not is_track and not _has_lane(connector, "respond"):
            continue
        if get_adapter(connector.platform) is None and connector.platform != "telegram":
            continue
        if not _connector_matches_team(connector, resolved_team_id):
            continue
        chat_ids = _allowed_chat_ids(connector)
        if is_track:
            chat_ids = chat_ids[:1]
        for chat_id in chat_ids:
            await _deliver(
                factory,
                org_id=org_id,
                connector=connector,
                chat_id=chat_id,
                text=text,
                command_label=f"notify:{event_type}",
                session_id=None,
                incident_id=incident_id,
                lifecycle_event=event_type if incident_id is not None else None,
                rendered_status=rendered_status,
                incident=incident,
                delivery_lane="track" if is_track else "respond",
                service_name=service_name,
                team_name=team_name,
            )


def schedule_incident_text(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    text: str,
    event_type: str,
    team_id: uuid.UUID | None = None,
    incident_id: uuid.UUID | None = None,
    rendered_status: str | None = None,
    task_registry: set[asyncio.Task] | None = None,
) -> asyncio.Task:
    task = asyncio.create_task(
        deliver_incident_text(
            factory,
            org_id=org_id,
            text=text,
            event_type=event_type,
            team_id=team_id,
            incident_id=incident_id,
            rendered_status=rendered_status,
        )
    )
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


async def _resolve_relay_targets(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    session_id: uuid.UUID,
) -> Iterable[tuple[BotConnector, str]]:
    async with factory() as db:
        stmt = (
            sqlalchemy.select(BotActionAudit)
            .where(
                BotActionAudit.org_id == org_id,
                BotActionAudit.session_id == session_id,
                BotActionAudit.command == "/chat",
                BotActionAudit.status == "ok",
            )
            .order_by(BotActionAudit.created_at.desc())
        )
        rows = (await db.execute(stmt)).scalars().all()

        seen: set[tuple[uuid.UUID, str]] = set()
        targets: list[tuple[BotConnector, str]] = []
        for row in rows:
            if row.chat_id is None:
                continue
            key = (row.connector_id, row.chat_id)
            if key in seen:
                continue
            connector = await BotConnectorRepo.get_by_id(db, org_id, row.connector_id)
            if connector is None or not connector.is_enabled:
                continue
            if not _has_capability(connector, "copilot_chat"):
                continue
            seen.add(key)
            targets.append((connector, row.chat_id))
        return targets


async def deliver_copilot_relay(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    session_id: uuid.UUID,
    reply_text: str,
) -> None:
    targets = await _resolve_relay_targets(
        factory, org_id=org_id, session_id=session_id
    )
    formatted = _format_copilot_reply(reply_text)
    for connector, chat_id in targets:
        await _deliver(
            factory,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            text=formatted,
            command_label="copilot_relay",
            session_id=session_id,
        )


def schedule_copilot_relay(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    task_registry: set[asyncio.Task] | None,
    session_id: uuid.UUID,
    reply_text: str,
) -> asyncio.Task:
    return asyncio.create_task(
        deliver_copilot_relay(
            factory,
            org_id=org_id,
            session_id=session_id,
            reply_text=reply_text,
        )
    )
