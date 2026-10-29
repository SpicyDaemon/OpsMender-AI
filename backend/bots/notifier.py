"""Outbound delivery of OpsMender events into chat connectors."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from weakref import WeakValueDictionary
from typing import Iterable

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import backend.bots  # noqa: F401  -- registers built-in adapters
from backend.bots.capabilities import (
    platform_delivers,
    supports_interactive_actions,
    supports_link_actions,
    supports_message_update,
)
from backend.bots.connectors import get_adapter
from backend.bots.delivery import DeliveryReceipt, UpdateResult
from backend.bots.incident_card import (
    MOVED_TO_ANOTHER_TEAM,
    SIGN_IN_TO_ACT_HINT,
    build_incident_message,
    incident_card_facts,
)
from backend.bots.post_budget import (
    ERROR_AFTER,
    HELD_LOOKBACK,
    budget_for,
    budget_platforms,
    channel_state,
)
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
    take_turns,
)
from backend.paging.teams_cards import CLOSED_STATUSES


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
    # A channel with one fixed destination (M1-100: the Teams workflow's
    # channel or chat) has no chat ids to configure.
    fixed = getattr(get_adapter(connector.platform), "fixed_destination", None)
    if fixed:
        return [fixed]
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


def _native_actions_ready(connector: BotConnector, *, is_track: bool) -> bool:
    """Whether this channel's incident card carries action buttons, on the
    Respond lane only (Track posts are status-only): an interactive platform,
    opted in, with a configured verifier; or a platform whose buttons are
    signed links to OpsMender's confirm page (M1-100), which need no callback
    and no opt-in, since acting there needs sign-in and Confirm."""
    if is_track:
        return False
    if supports_link_actions(connector.platform):
        return True
    return bool(
        supports_interactive_actions(connector.platform)
        and connector.native_actions_enabled
        and connector.callback_status in {"configured", "verified"}
    )


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

    Precedence: the team it was reassigned to; incident service ->
    service.team; incident escalation chain -> escalation_chain.team; no
    free-text or AI inference.
    """
    if incident is None:
        return None, None, None

    service = (
        await ServiceRepo.get_by_id(db, org_id, incident.service_id)
        if incident.service_id is not None
        else None
    )
    team_id = getattr(incident, "team_id", None) or (
        service.team_id if service is not None else None
    )
    if team_id is not None:
        team = await TeamRepo.get_by_id(db, org_id, team_id)
        return (
            team_id,
            team.name if team is not None else None,
            service.name if service is not None else None,
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
    incident_id: uuid.UUID | None = None,
    incident=None,
    native_actions_ready: bool = False,
    service_name: str | None = None,
    team_name: str | None = None,
    delivery_lane: str = "respond",
    facts: list[tuple[str, str]] | None = None,
    acknowledged_by: str | None = None,
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
                "facts": facts,
                "acknowledged_by": acknowledged_by,
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
        elif supports_link_actions(connector.platform):
            # M1-100: the card is built from the incident, with signed links
            # minted only when this channel's card may carry them.
            receipt = await adapter.send_incident_update(
                connector,
                chat_id=chat_id,
                text=text,
                incident=incident,
                links_ready=native_actions_ready,
                facts=facts,
                acknowledged_by=acknowledged_by,
                event_type=command_label.removeprefix("notify:"),
                incident_id=incident_id,
            )
        else:
            receipt = await adapter.send_incident_update(
                connector,
                chat_id=chat_id,
                text=text,
            )
        if receipt.external_channel_id is None:
            # Keeps the receipt's delivery status and retry hint.
            return dataclasses.replace(receipt, external_channel_id=chat_id)
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
    generated_at: datetime | None = None,
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
            delivery_status=receipt.delivery_status or delivery_status,
            last_updated_at=last_updated_at,
            event_generated_at=generated_at,
        )


async def _record_not_posted(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    command_label: str,
    session_id: uuid.UUID | None,
    incident_id: uuid.UUID | None,
    lifecycle_event: str | None,
    rendered_status: str | None,
    generated_at: datetime | None,
    status: str,
    detail: str,
    held_receipt: bool,
) -> None:
    """Record a post that did not go out (M1-100): held or limited by the
    post limit, or a transient failure. Never touches the connector status.

    A held card also gets a ``held`` receipt. As the channel's latest card
    receipt it makes an older event still in flight stale, and it is what
    :func:`post_held_cards` looks for.
    """
    await BotActionAuditRepo.create(
        db,
        org_id,
        connector_id=connector.id,
        platform=connector.platform,
        chat_id=chat_id,
        command=command_label,
        status=status,
        detail=detail[:1000],
        session_id=session_id,
        incident_id=incident_id,
    )
    if held_receipt and incident_id is not None and lifecycle_event is not None:
        await IncidentNotificationReceiptRepo.create(
            db,
            org_id,
            incident_id=incident_id,
            connector_id=connector.id,
            platform=connector.platform,
            lifecycle_event=lifecycle_event,
            external_channel_id=chat_id,
            rendered_status=rendered_status,
            can_update=False,
            session_id=session_id,
            delivery_status="held",
            event_generated_at=generated_at,
        )


async def _status_changed_since_last_card(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    incident_id: uuid.UUID | None,
    incident,
    rendered_status: str | None,
) -> bool:
    """Whether an edit-only refresh still posts a new card (M1-100): on a
    link-button channel that cannot edit, when the status differs from the
    latest card's (a reopen), so the last card does not keep saying
    resolved. Ownership changes keep the status and post nothing."""
    if incident is None or incident_id is None:
        return False
    if not supports_link_actions(connector.platform):
        return False
    if supports_message_update(connector.platform):
        return False
    latest = await IncidentNotificationReceiptRepo.latest_for_incident_channel(
        db,
        org_id,
        incident_id=incident_id,
        connector_id=connector.id,
        external_channel_id=chat_id,
        lifecycle_events=sorted(INCIDENT_CHAT_EVENTS),
    )
    return latest is not None and latest.rendered_status != rendered_status


async def _try_update_incident_notification(
    db: AsyncSession,
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
    generated_at: datetime | None = None,
    facts: list[tuple[str, str]] | None = None,
    acknowledged_by: str | None = None,
) -> bool:
    """Edit the prior incident message in place when the platform supports it.

    Returns ``True`` only when the message was edited and recorded. Returns
    ``False`` - leaving the caller to post a fresh follow-up message - when the
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

    if delivery_lane == "track":
        prior = await IncidentTrackPostRepo.get(
            db,
            org_id,
            incident_id=incident_id,
            connector_id=connector.id,
        )
    else:
        # The incident card, never a session message posted after it.
        prior = await IncidentNotificationReceiptRepo.latest_for_incident_channel(
            db,
            org_id,
            incident_id=incident_id,
            connector_id=connector.id,
            external_channel_id=chat_id,
            updateable_only=True,
            lifecycle_events=sorted(INCIDENT_CHAT_EVENTS),
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
    if supports_interactive_actions(connector.platform):
        # The native cards re-render the same facts a new post would carry.
        update_kwargs["facts"] = facts
        update_kwargs["acknowledged_by"] = acknowledged_by
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
        generated_at=generated_at,
    )
    return True


def _as_utc(value: datetime) -> datetime:
    """Receipt timestamps are written in UTC; SQLite hands them back naive."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# Weak values retain a lock while held or awaited, without retaining idle channels.
_card_turns: WeakValueDictionary = WeakValueDictionary()


@asynccontextmanager
async def _delivery_turn(factory, key: str | None):
    # Acquire the process turn before checking out a connection. PostgreSQL's
    # transaction turn also serializes deliveries from other processes.
    lock_key = (asyncio.get_running_loop(), key)
    lock = (
        _card_turns.setdefault(lock_key, asyncio.Lock())
        if key is not None
        else asyncio.Lock()
    )
    async with lock:
        async with factory() as db:
            if key is not None:
                await take_turns(db, key)
            yield db
            await db.commit()


async def _newer_card_in_channel(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    incident_id: uuid.UUID,
    generated_at: datetime,
) -> bool:
    """Compare the represented event, never the time the provider replied."""
    latest = await IncidentNotificationReceiptRepo.latest_for_incident_channel(
        db,
        org_id,
        incident_id=incident_id,
        connector_id=connector.id,
        external_channel_id=chat_id,
        lifecycle_events=sorted(INCIDENT_CHAT_EVENTS),
    )
    if latest is None or latest.event_generated_at is None:
        return False
    return _as_utc(latest.event_generated_at) > _as_utc(generated_at)


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
    facts: list[tuple[str, str]] | None = None,
    acknowledged_by: str | None = None,
    edit_only: bool = False,
    generated_at: datetime | None = None,
    catch_up: bool = False,
    planned_receipt_id: uuid.UUID | None = None,
) -> None:
    """Serialize a card's stale check, provider send and receipt commit.

    A channel with a post limit (M1-100: Microsoft Teams (Workflows)) takes
    one turn per channel for every post, so the count, the post and its
    record run one at a time across processes. Over the limit, or while the
    channel is paused after a transient failure, a card is held and a text
    post is dropped; neither marks the channel failed. ``catch_up`` is the
    held-card sweep posting the current state of an incident whose latest
    card here is still held.
    """
    if not platform_delivers(connector.platform):
        # M1-96: a platform being rebuilt (Teams) receives nothing, and is not
        # marked failed on every incident event either.
        return
    card_event = incident_id is not None and lifecycle_event in INCIDENT_CHAT_EVENTS
    budget = budget_for(connector.platform)
    if budget is not None:
        # One turn per channel for every post: the count, the post and its
        # record run one at a time across processes (M1-100).
        key = f"connector-posts:{org_id}:{connector.id}"
    else:
        key = (
            f"incident-card:{org_id}:{incident_id}:{connector.id}:{chat_id}"
            if card_event
            else None
        )
    async with _delivery_turn(factory, key) as db:
        if card_event:
            if catch_up:
                latest = (
                    await IncidentNotificationReceiptRepo.latest_for_incident_channel(
                        db,
                        org_id,
                        incident_id=incident_id,
                        connector_id=connector.id,
                        external_channel_id=chat_id,
                        lifecycle_events=sorted(INCIDENT_CHAT_EVENTS),
                    )
                )
                if (
                    latest is None
                    or latest.delivery_status != "held"
                    or latest.id != planned_receipt_id
                ):
                    # A newer event arrived, or another sweep posted it.
                    return
            if generated_at is not None and await _newer_card_in_channel(
                db,
                org_id=org_id,
                connector=connector,
                chat_id=chat_id,
                incident_id=incident_id,
                generated_at=generated_at,
            ):
                log.debug(
                    "incident notification %s skipped: the channel already shows a "
                    "newer card (platform=%s chat=%s incident=%s)",
                    lifecycle_event,
                    connector.platform,
                    chat_id,
                    incident_id,
                )
                return
            updated = await _try_update_incident_notification(
                db,
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
                facts=facts,
                acknowledged_by=acknowledged_by,
                generated_at=generated_at,
            )
            if updated:
                return
        if edit_only and not await _status_changed_since_last_card(
            db,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            incident_id=incident_id,
            incident=incident,
            rendered_status=rendered_status,
        ):
            # Nothing to edit here: an edit-only refresh never posts a new
            # message, unless a channel that cannot edit would keep showing a
            # status the incident left (M1-100: a reopen).
            return

        state = None
        if budget is not None:
            now = _utcnow()
            state = await channel_state(db, org_id, connector.id, budget, now=now)
            reason = state.blocked_reason(budget, platform=connector.platform, now=now)
            if reason is not None or (catch_up and state.last_failed):
                if not catch_up:
                    await _record_not_posted(
                        db,
                        org_id=org_id,
                        connector=connector,
                        chat_id=chat_id,
                        command_label=command_label,
                        session_id=session_id,
                        incident_id=incident_id,
                        lifecycle_event=lifecycle_event,
                        rendered_status=rendered_status,
                        generated_at=generated_at,
                        status="held" if card_event else "limited",
                        detail=f"{reason}. "
                        + (
                            "OpsMender posts the newest state of this incident "
                            "when there is room."
                            if card_event
                            else "This message was not posted."
                        ),
                        held_receipt=card_event,
                    )
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
                incident_id=incident_id,
                incident=incident,
                native_actions_ready=native_actions_ready,
                service_name=service_name,
                team_name=team_name,
                delivery_lane=delivery_lane,
                facts=facts,
                acknowledged_by=acknowledged_by,
            )
        if state is not None and not receipt.ok and receipt.retryable:
            # The provider may take it later: hold the card, pause the channel
            # (derived from these rows) and leave its status alone until
            # several posts in a row did not go through.
            await _record_not_posted(
                db,
                org_id=org_id,
                connector=connector,
                chat_id=chat_id,
                command_label=command_label,
                session_id=session_id,
                incident_id=incident_id,
                lifecycle_event=lifecycle_event,
                rendered_status=rendered_status,
                generated_at=generated_at,
                status="transient_failure",
                detail=f"{receipt.error}. "
                + (
                    "OpsMender posts the newest state of this incident when the "
                    "workflow answers."
                    if card_event
                    else "This message is not retried."
                ),
                held_receipt=card_event and not catch_up,
            )
            streak = state.transient_streak + 1
            if streak >= ERROR_AFTER:
                await BotConnectorRepo.mark_status(
                    db,
                    org_id,
                    connector.id,
                    status="error",
                    error=(
                        f"{receipt.error}. {streak} posts in a row did not go "
                        "through; OpsMender keeps trying and posts held cards "
                        "when the workflow answers."
                    )[:1000],
                )
            return
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
            generated_at=generated_at,
        )


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
        if budget_for(connector.platform) is not None:
            # AI session progress would spend the post limit incident cards
            # need (M1-100).
            continue
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
    "incident.escalation_exhausted",
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
    Also surfaces escalation context - the level (latest page step index) and
    the previous responder paged before the current one - for escalation cards.
    Terminal incidents keep history and an existing owner without presenting
    a past page target as still waiting.
    """
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    assignment = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    pages = list(await IncidentPageRepo.list_for_incident(db, org_id, incident_id))
    latest = pages[-1] if pages else None

    ack_uid = assignment.assigned_to if assignment is not None else None
    esc_uid = latest.user_id if latest is not None else None
    esc_step = latest.step_index if latest is not None else None

    if ack_uid is not None:
        state, resp_uid = "assigned", ack_uid
    elif (
        esc_uid is not None
        and incident is not None
        and incident.status not in {"resolved", "merged"}
    ):
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
    edit_only: bool = False,
    previous_team_id: uuid.UUID | None = None,
    previous_team_only: bool = False,
    generated_at: datetime | None = None,
    only_connector_id: uuid.UUID | None = None,
    catch_up: bool = False,
    planned_receipt_id: uuid.UUID | None = None,
) -> int:
    """Post an incident card/message to every enabled Notification Channel that
    has the ``notifications`` capability.

    Honest delivery: each platform receives the same useful incident message
    with an authenticated incident link. Interactive action controls are only
    rendered when the platform supports interactive callbacks and the channel
    has native actions enabled with a configured verifier. Slack and Discord
    verify every callback before the shared action coordinator permits a
    mutation. A Microsoft Teams (Workflows) channel gets a new card per
    change; the old Teams channel type receives nothing while the Teams bot
    is being rebuilt.
    Delivery-only platforms (SMS, email, custom webhook, …) get the same
    message minus any card framing, which their adapters already handle.

    ``edit_only`` brings the cards already posted up to date and nothing
    else: a channel whose platform cannot edit a message, or that has no
    card to edit, gets no new post. Link-button channels (Teams Workflows)
    receive a new card only when the status changed since their last card,
    on reopen. Ownership changes alone post nothing.

    ``previous_team_id`` is the team that handled the incident before a
    reassign, handoff or service move. A channel scoped to that team but not
    to the new one gets this one edit: the card says the incident moved to
    another team, names no service, team or responder, and has no buttons.
    Later events skip that channel, like any channel scoped to other teams.
    With ``previous_team_only`` that edit is all this delivery does: the
    channels of the team that holds the incident now are left to the event
    scheduled for them (a resolve that moves the incident in the same change).

    ``generated_at`` is when the event was scheduled; see :func:`_deliver`.

    ``only_connector_id`` and ``catch_up`` are for :func:`post_held_cards`:
    one channel, posting the incident's current state over its held card.
    Returns how many deliveries were made (0: no channel takes the event).
    """
    generated_at = generated_at or _utcnow()
    if event_type not in INCIDENT_CHAT_EVENTS:
        return 0

    async with factory() as db:
        incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
        if incident is None:
            return 0

        team_id, team_name, service_name = await _resolve_incident_team(
            db, org_id, incident
        )
        org_name = await _resolve_org_name(db, org_id)

        responder = await _resolve_incident_responder(db, org_id, incident.id)
        connectors = list(
            await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        )
    # A channel with a post limit goes last (stable), so its turn, which can
    # wait on a slow workflow, never delays the others in this loop.
    connectors.sort(key=lambda c: budget_for(c.platform) is not None)

    calls = 0
    for connector in connectors:
        if only_connector_id is not None and connector.id != only_connector_id:
            continue
        if not _has_capability(connector, "notifications"):
            continue
        if get_adapter(connector.platform) is None and connector.platform != "telegram":
            continue
        moved_away = not _connector_matches_team(connector, team_id)
        if moved_away and not (
            edit_only
            and previous_team_id is not None
            and _connector_matches_team(connector, previous_team_id)
        ):
            continue
        if previous_team_only and not moved_away:
            continue
        is_track = _has_lane(connector, "track")
        if is_track and connector.platform not in {
            "slack",
            "discord",
            "google_chat",
            "eventbridge",
        }:
            continue
        if not is_track and not _has_lane(connector, "respond"):
            continue
        native_actions_ready = not moved_away and _native_actions_ready(
            connector, is_track=is_track
        )
        # The previous team's channel learns only that the incident moved.
        card = (
            {
                "responder": None,
                "service_name": None,
                "team_name": MOVED_TO_ANOTHER_TEAM,
                "org_name": org_name,
            }
            if moved_away
            else {
                "responder": responder,
                "service_name": service_name,
                "team_name": team_name,
                "org_name": org_name,
            }
        )
        text = build_incident_message(
            incident,
            event_type=event_type,
            base_url=base_url,
            supports_actions=native_actions_ready,
            **card,
        )
        chat_ids = _allowed_chat_ids(connector)
        if is_track:
            chat_ids = chat_ids[:1]
        for chat_id in chat_ids:
            calls += 1
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
                service_name=card["service_name"],
                team_name=card["team_name"],
                facts=incident_card_facts(incident, **card),
                acknowledged_by=(card["responder"] or {}).get(
                    "acknowledged_by_display_name"
                ),
                edit_only=edit_only,
                generated_at=generated_at,
                catch_up=catch_up,
                planned_receipt_id=planned_receipt_id,
            )
    return calls


def schedule_incident_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    event_type: str,
    incident_id: uuid.UUID,
    base_url: str | None = None,
    task_registry: set[asyncio.Task] | None = None,
    edit_only: bool = False,
    previous_team_id: uuid.UUID | None = None,
    previous_team_only: bool = False,
) -> asyncio.Task:
    task = asyncio.create_task(
        deliver_incident_event(
            factory,
            org_id=org_id,
            incident_id=incident_id,
            event_type=event_type,
            base_url=base_url,
            edit_only=edit_only,
            previous_team_id=previous_team_id,
            previous_team_only=previous_team_only,
            generated_at=_utcnow(),
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
    preserve_team: bool = False,
    respond_only: bool = False,
    strict_team_scope: bool = False,
    informational_only: bool = False,
    facts: list[tuple[str, str]] | None = None,
    acknowledged_by: str | None = None,
    generated_at: datetime | None = None,
) -> None:
    """Fan out a *pre-built* incident message to enabled Notification Channels
    with the ``notifications`` capability.

    Used when the caller has already resolved the message from a live session
    (e.g. the escalation engine, where the new page row may not be committed
    yet) so the background delivery does not have to re-read incident state.
    ``facts`` and ``acknowledged_by`` are pre-built the same way for the
    native cards, which keep their action buttons as on any other event.
    ``generated_at`` is when they were built; a channel whose card is newer
    by then is left alone (see :func:`_deliver`), so a delayed escalation
    never edits an acknowledged card back or posts a stale card after it.
    """
    generated_at = generated_at or _utcnow()
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
            owner_team_id, team_name, service_name = await _resolve_incident_team(
                db, org_id, incident
            )
            if not preserve_team:
                resolved_team_id = owner_team_id
            elif resolved_team_id is not None:
                receiving_team = await TeamRepo.get_by_id(db, org_id, resolved_team_id)
                team_name = receiving_team.name if receiving_team is not None else None
                service_name = None
    # A channel with a post limit goes last (see deliver_incident_event).
    connectors.sort(key=lambda c: budget_for(c.platform) is not None)

    for connector in connectors:
        if not _has_capability(connector, "notifications"):
            continue
        is_track = _has_lane(connector, "track") and not respond_only
        if respond_only and not _has_lane(connector, "respond"):
            continue
        if is_track and connector.platform not in {
            "slack",
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
        if strict_team_scope:
            scope, team_ids = _connector_team_scope(connector)
            if scope != "teams" or resolved_team_id not in team_ids:
                continue
        # The buttons need the card, and the card needs the incident: an
        # informational post has none, and neither has a delivery that cannot
        # read the incident yet (a new incident escalated before its commit).
        native_actions_ready = (
            incident is not None
            and not informational_only
            and _native_actions_ready(connector, is_track=is_track)
        )
        # The text was built once for every channel, with the sign-in hint;
        # a card that carries the buttons drops it.
        connector_text = (
            text.replace(f"\n{SIGN_IN_TO_ACT_HINT}", "")
            if native_actions_ready
            else text
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
                text=connector_text,
                command_label=f"notify:{event_type}",
                session_id=None,
                incident_id=incident_id,
                lifecycle_event=(
                    event_type
                    if incident_id is not None and not informational_only
                    else None
                ),
                rendered_status=rendered_status,
                incident=None if informational_only else incident,
                native_actions_ready=native_actions_ready,
                delivery_lane="track" if is_track else "respond",
                service_name=service_name,
                team_name=team_name,
                facts=facts,
                acknowledged_by=acknowledged_by,
                generated_at=generated_at,
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
    facts: list[tuple[str, str]] | None = None,
    acknowledged_by: str | None = None,
    generated_at: datetime | None = None,
) -> asyncio.Task:
    """``generated_at`` defaults to now. A caller that built the text and
    facts earlier (the escalation engine, before its commit) passes the
    moment it built them."""
    task = asyncio.create_task(
        deliver_incident_text(
            factory,
            org_id=org_id,
            text=text,
            event_type=event_type,
            team_id=team_id,
            incident_id=incident_id,
            rendered_status=rendered_status,
            facts=facts,
            acknowledged_by=acknowledged_by,
            generated_at=generated_at or _utcnow(),
        )
    )
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


async def post_held_cards(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> int:
    """Post the current state of each incident whose latest card in a
    Workflows channel is held, within the channel's room. Never raises.

    Every held change of one incident becomes one card showing the state read
    at post time, with the headline of its newest held event; open incidents
    go first, then P0 to P3, then the oldest hold. A channel that is paused,
    stopped by a refused post or full is skipped, and holds whose last row is
    older than an hour are not posted. The delivery turn checks again that
    the card is still held, so two sweeps (or a sweep and a normal post) post
    it once. Returns how many incidents it tried to post.
    """
    try:
        return await _post_held_cards(factory, now=now or _utcnow())
    except Exception as exc:  # noqa: BLE001 - the next tick sweeps again
        log.warning("held channel card sweep failed: %s", type(exc).__name__)
        return 0


async def _post_held_cards(
    factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> int:
    # (org_id, connector_id, incident_id, held receipt id, lifecycle event)
    planned: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, str]] = []
    async with factory() as db:
        rows = await BotActionAuditRepo.list_holds_since(
            db, since=now - HELD_LOOKBACK, platforms=budget_platforms()
        )
        if not rows:
            return 0
        # Rows come oldest first, so each group keeps its first hold.
        first_held: dict[tuple[uuid.UUID, uuid.UUID], dict] = {}
        for row in rows:
            held = first_held.setdefault((row.org_id, row.connector_id), {})
            held.setdefault((row.incident_id, row.chat_id), _as_utc(row.created_at))

        for (org_id, connector_id), held in first_held.items():
            connector = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
            if connector is None or not connector.is_enabled:
                continue
            budget = budget_for(connector.platform)
            if budget is None:
                continue
            state = await channel_state(db, org_id, connector_id, budget, now=now)
            if state.paused_until is not None or state.last_failed:
                continue
            room = budget.limit - state.used
            if room <= 0:
                continue
            candidates = []
            for (incident_id, chat_id), since in held.items():
                receipt = (
                    await IncidentNotificationReceiptRepo.latest_for_incident_channel(
                        db,
                        org_id,
                        incident_id=incident_id,
                        connector_id=connector_id,
                        external_channel_id=chat_id,
                        lifecycle_events=sorted(INCIDENT_CHAT_EVENTS),
                    )
                )
                if receipt is not None and receipt.delivery_status == "held":
                    candidates.append((incident_id, receipt, since))
            ranks = await IncidentRepo.list_priority_status(
                db, org_id, [incident_id for incident_id, _r, _s in candidates]
            )

            def order(candidate, ranks=ranks):
                priority, status = ranks.get(candidate[0], (None, ""))
                return (
                    status in CLOSED_STATUSES,
                    PRIORITY_RANK.get(priority or "", len(PRIORITY_RANK)),
                    candidate[2],
                )

            candidates.sort(key=order)
            planned += [
                (org_id, connector_id, incident_id, receipt.id, receipt.lifecycle_event)
                for incident_id, receipt, _since in candidates[:room]
            ]

    for org_id, connector_id, incident_id, receipt_id, event_type in planned:
        try:
            calls = await deliver_incident_event(
                factory,
                org_id=org_id,
                incident_id=incident_id,
                event_type=event_type,
                base_url=os.environ.get("OPSMENDER_PUBLIC_URL"),
                only_connector_id=connector_id,
                catch_up=True,
                planned_receipt_id=receipt_id,
                generated_at=_utcnow(),
            )
            if calls == 0:
                # The channel no longer takes this incident (team scope, lane,
                # capability, turned off): not tried again every tick, and it
                # takes no room from the others.
                async with factory() as db:
                    await IncidentNotificationReceiptRepo.mark_dropped(db, receipt_id)
                    await db.commit()
        except Exception as exc:  # noqa: BLE001 - one card never stops the rest
            log.warning("held channel card not posted: %s", type(exc).__name__)
    return len(planned)


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
