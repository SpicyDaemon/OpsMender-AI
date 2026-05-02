"""Inbound chat bot webhook endpoints.

Each platform exposes its own HTTP path so the URL structure remains
explicit, but verification, payload parsing, and reply shaping are
delegated to the registered ``BotConnectorAdapter``. Command handling
itself is platform-agnostic and lives in
``backend.bots.dispatcher.dispatch_inbound``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import backend.bots  # noqa: F401
from backend.api.deps import get_db
from backend.bots.connectors import get_adapter
from backend.bots.dispatcher import dispatch_inbound
from backend.db.models import BotConnector
from backend.db.repos import BotConnectorRepo

router = APIRouter(prefix="/bot-connectors", tags=["bot-webhooks"])


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

    message = adapter.parse_inbound(payload)
    if message is None:
        return {"ok": True}

    result = await dispatch_inbound(db, connector=connector, message=message)
    if result.reply_text is None:
        return {"ok": True}

    inline = adapter.inline_reply(message.chat_id, result.reply_text)
    if inline is not None:
        return inline

    # Platform doesn't support inline replies — schedule outbound delivery.
    asyncio.create_task(
        adapter.send_message(
            connector,
            chat_id=message.chat_id,
            text=result.reply_text,
        )
    )
    return {"ok": True}


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
