"""Inbound chat bot webhook endpoints.

Each platform exposes its own HTTP path so the URL structure remains
explicit, but verification, payload parsing, and reply shaping are
delegated to the registered ``BotConnectorAdapter``. Command handling
itself is platform-agnostic and lives in
``backend.bots.dispatcher.dispatch_inbound``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import backend.bots  # noqa: F401
from backend.api.deps import get_db
from backend.bots.actions import ExternalActorIdentity, IncidentActionError
from backend.bots.connectors import get_adapter
from backend.bots.connectors.discord import parse_incident_id_from_component
from backend.bots.dispatcher import dispatch_inbound
from backend.bots.native_callbacks import (
    NormalizedNativeCallback,
    callback_error_message,
    callback_result_message,
    dispatch_native_session_result,
    execute_normalized_callback,
)
from backend.db.models import BotConnector
from backend.db.repos import IncidentRepo

router = APIRouter(prefix="/bot-connectors", tags=["bot-webhooks"])
logger = logging.getLogger(__name__)


async def _dispatch_verified_payload(
    *,
    connector: BotConnector,
    adapter,
    payload: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    message = adapter.parse_inbound(payload)
    if message is None:
        return {"ok": True}

    result = await dispatch_inbound(db, connector=connector, message=message)
    if result.reply_text is None:
        return {"ok": True}

    inline = adapter.inline_reply(message.chat_id, result.reply_text)
    if inline is not None:
        return inline

    asyncio.create_task(
        adapter.send_message(
            connector,
            chat_id=message.chat_id,
            text=result.reply_text,
        )
    )
    return {"ok": True}


async def _process_webhook(
    *,
    connector_id: uuid.UUID,
    platform: str,
    request: Request,
    payload: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    # Webhooks are public endpoints. We look up the connector by ID globally
    # first to resolve its organization context.
    connector = await db.get(BotConnector, connector_id)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )

    adapter = get_adapter(platform)
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported connector platform: {platform}",
        )

    raw_body = await request.body()
    adapter.verify_webhook(connector, headers=request.headers, raw_body=raw_body)

    return await _dispatch_verified_payload(
        connector=connector,
        adapter=adapter,
        payload=payload,
        db=db,
    )


async def _complete_discord_component(
    app,
    *,
    connector_id: uuid.UUID,
    callback: NormalizedNativeCallback,
    application_id: str,
    interaction_token: str,
) -> None:
    """Execute after Discord has received the deferred ephemeral response."""

    followup_text = "Action could not be completed."
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        logger.error("Discord component callback has no database session factory")
        return

    async with factory() as db:
        connector = await db.get(BotConnector, connector_id)
        if connector is None:
            followup_text = "That notification channel no longer exists."
        else:
            try:
                result = await execute_normalized_callback(
                    db,
                    connector=connector,
                    callback=callback,
                    config=app.state.config,
                )
            except IncidentActionError as exc:
                followup_text = callback_error_message(exc).replace(
                    "Your external account",
                    "Your Discord account",
                )
            else:
                await dispatch_native_session_result(
                    app,
                    db,
                    org_id=connector.org_id,
                    result=result,
                )
                incident = await IncidentRepo.get_by_id(
                    db,
                    connector.org_id,
                    callback.incident_id,
                )
                title = (
                    incident.title
                    if incident is not None
                    else str(callback.incident_id)
                )
                followup_text = callback_result_message(result, title)
            await db.commit()

    adapter = get_adapter("discord")
    update_response = getattr(adapter, "update_interaction_response", None)
    if not callable(update_response):
        logger.error("Discord adapter cannot complete a deferred interaction")
        return
    ok, error = await update_response(
        application_id=application_id,
        interaction_token=interaction_token,
        text=followup_text,
    )
    if not ok:
        logger.warning("Discord interaction follow-up failed: %s", error)


@router.post(
    "/{connector_id}/telegram/webhook",
    summary="Handle inbound Telegram bot webhook updates",
)
async def telegram_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="telegram",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/signal/webhook",
    summary="Handle inbound Signal webhook updates (signal-cli-rest-api)",
)
async def signal_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="signal",
        request=request,
        payload=payload,
        db=db,
    )


@router.get(
    "/{connector_id}/whatsapp/webhook",
    summary="WhatsApp webhook verification challenge (Meta subscribe handshake)",
)
async def whatsapp_verify(
    connector_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Meta sends a GET request with ``hub.mode=subscribe``."""
    connector = await db.get(BotConnector, connector_id)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )

    mode = request.query_params.get("hub.mode")
    challenge = request.query_params.get("hub.challenge")
    verify_token = request.query_params.get("hub.verify_token")

    if mode != "subscribe" or not challenge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification request",
        )

    expected = (connector.credentials or {}).get("verify_token")
    if not expected or verify_token != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification token mismatch",
        )

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(content=challenge)


@router.post(
    "/{connector_id}/whatsapp/webhook",
    summary="Handle inbound WhatsApp Cloud API webhook updates",
)
async def whatsapp_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="whatsapp",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/slack/webhook",
    summary="Handle inbound Slack Events API webhook updates",
)
async def slack_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Handle Slack URL verification challenge immediately
    if payload.get("type") == "url_verification":
        connector = await db.get(BotConnector, connector_id)
        if connector is None:
            raise HTTPException(status_code=404, detail="Connector not found")

        # Verify even for challenges to ensure security
        adapter = get_adapter("slack")
        if adapter:
            raw_body = await request.body()
            adapter.verify_webhook(
                connector, headers=request.headers, raw_body=raw_body
            )

        return {"challenge": payload.get("challenge")}

    return await _process_webhook(
        connector_id=connector_id,
        platform="slack",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/discord/webhook",
    summary="Handle inbound Discord Interactions webhook updates",
)
async def discord_webhook(
    connector_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # Discord requires verification over the exact raw body. Do not parse the
    # interaction until its Ed25519 signature has passed.
    raw_body = await request.body()
    connector = await db.get(BotConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    adapter = get_adapter("discord")
    if adapter is None:
        raise HTTPException(status_code=400, detail="Discord adapter not found")
    adapter.verify_webhook(connector, headers=request.headers, raw_body=raw_body)
    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Discord payload")

    if payload.get("type") == 1:
        return {"type": 1}

    if payload.get("type") == 3:
        incident_id = parse_incident_id_from_component(payload)
        data = payload.get("data") or {}
        action_id = data.get("custom_id") if isinstance(data, dict) else None
        member = payload.get("member")
        actor = member.get("user") if isinstance(member, dict) else None
        if not isinstance(actor, dict):
            actor = payload.get("user")
        if not isinstance(actor, dict):
            actor = {}
        actor_id = actor.get("id") if isinstance(actor, dict) else None
        interaction_id = payload.get("id")
        application_id = payload.get("application_id")
        interaction_token = payload.get("token")
        if not all(
            (
                incident_id,
                action_id,
                actor_id,
                interaction_id,
                application_id,
                interaction_token,
            )
        ):
            return {
                "type": 4,
                "data": {
                    "content": "Could not identify that incident action.",
                    "flags": 64,
                },
            }
        message = payload.get("message")
        if not isinstance(message, dict):
            message = {}
        background_tasks.add_task(
            _complete_discord_component,
            request.app,
            connector_id=connector_id,
            callback=NormalizedNativeCallback(
                incident_id=incident_id,
                action_id=str(action_id),
                external_actor=ExternalActorIdentity(
                    platform_user_id=str(actor_id),
                    username=actor.get("username"),
                    display_name=actor.get("global_name")
                    or actor.get("display_name")
                    or actor.get("username"),
                ),
                idempotency_key=str(interaction_id),
                channel_id=str(payload.get("channel_id") or "") or None,
                message_id=str(message.get("id") or "") or None,
            ),
            application_id=str(application_id),
            interaction_token=str(interaction_token),
        )
        # Type 5 acknowledges before the three-second deadline. Flag 64 keeps
        # both the deferred placeholder and its completed result ephemeral.
        return {"type": 5, "data": {"flags": 64}}

    return await _dispatch_verified_payload(
        connector=connector,
        adapter=adapter,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/mattermost/webhook",
    summary="Handle inbound Mattermost Outgoing Webhook updates",
)
async def mattermost_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="mattermost",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/matrix/webhook",
    summary="Handle inbound Matrix App Service webhook updates",
)
async def matrix_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="matrix",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/feishu/webhook",
    summary="Handle inbound Feishu / Lark event updates",
)
async def feishu_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Handle Feishu URL verification challenge immediately
    if payload.get("type") == "url_verification":
        connector = await db.get(BotConnector, connector_id)
        if connector is None:
            raise HTTPException(status_code=404, detail="Connector not found")

        adapter = get_adapter("feishu")
        if adapter:
            raw_body = await request.body()
            adapter.verify_webhook(
                connector, headers=request.headers, raw_body=raw_body
            )

        return {"challenge": payload.get("challenge")}

    return await _process_webhook(
        connector_id=connector_id,
        platform="feishu",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/dingtalk/webhook",
    summary="Handle inbound DingTalk robot updates",
)
async def dingtalk_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="dingtalk",
        request=request,
        payload=payload,
        db=db,
    )


@router.get("/{connector_id}/wecom/webhook")
async def wecom_handshake(
    connector_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(BotConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")

    adapter = get_adapter("wecom")
    if not adapter:
        raise HTTPException(status_code=400, detail="Adapter not found")

    from backend.bots.connectors.wecom import WeComAdapter

    if isinstance(adapter, WeComAdapter):
        return adapter.handle_handshake(connector, request.query_params)
    return ""


@router.post("/{connector_id}/wecom/webhook")
async def wecom_webhook(
    connector_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(BotConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")

    adapter = get_adapter("wecom")
    from backend.bots.connectors.wecom import WeComAdapter

    if not isinstance(adapter, WeComAdapter):
        raise HTTPException(status_code=400, detail="Invalid adapter")

    raw_body = await request.body()
    # WeCom sends XML with an 'Encrypt' field
    import defusedxml.ElementTree as ET

    root = ET.fromstring(raw_body)
    encrypt = root.findtext("Encrypt")

    credentials = connector.credentials or {}
    aes_key = credentials.get("encoding_aes_key")
    decrypted_xml = adapter._decrypt(str(aes_key), str(encrypt))

    # Process the decrypted XML
    return await _process_webhook(
        connector_id=connector_id,
        platform="wecom",
        request=request,
        payload={"_xml_content": decrypted_xml},
        db=db,
    )


@router.get("/{connector_id}/weixin/webhook")
async def weixin_handshake(
    connector_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(BotConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")

    adapter = get_adapter("weixin")
    from backend.bots.connectors.weixin import WeixinAdapter

    if isinstance(adapter, WeixinAdapter):
        return adapter.handle_handshake(connector, request.query_params)
    return ""


@router.post("/{connector_id}/weixin/webhook")
async def weixin_webhook(
    connector_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    # Weixin sends raw XML in the body
    return await _process_webhook(
        connector_id=connector_id,
        platform="weixin",
        request=request,
        payload={"_xml_content": raw_body.decode("utf-8")},
        db=db,
    )


@router.post(
    "/{connector_id}/twilio/webhook",
    summary="Handle inbound Twilio SMS updates",
)
async def twilio_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="twilio",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/email/webhook",
    summary="Handle inbound Email updates (Mailgun format)",
)
async def email_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="email",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/homeassistant/webhook",
    summary="Handle inbound Home Assistant updates",
)
async def homeassistant_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="homeassistant",
        request=request,
        payload=payload,
        db=db,
    )


@router.post(
    "/{connector_id}/bluebubbles/webhook",
    summary="Handle inbound BlueBubbles (iMessage) updates",
)
async def bluebubbles_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await _process_webhook(
        connector_id=connector_id,
        platform="bluebubbles",
        request=request,
        payload=payload,
        db=db,
    )
