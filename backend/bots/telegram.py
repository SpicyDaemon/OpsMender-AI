"""Telegram Bot API outbound client.

Thin async wrapper over ``POST /bot{token}/sendMessage``. Used by
``backend.bots.notifier`` to push incident cards, session lifecycle events
and co-pilot assistant replies into the Telegram chats configured on a
connector. The text arrives in OpsMender's chat dialect and leaves as
Telegram HTML through :mod:`backend.bots.telegram_markup`, so no incident
title, summary or reply can make Telegram refuse the message (M1-121).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.bots.telegram_markup import PARSE_MODE, render

log = logging.getLogger(__name__)


TELEGRAM_API_BASE = "https://api.telegram.org"


async def send_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    timeout_seconds: float = 10.0,
) -> tuple[bool, str | None]:
    """Send a single Telegram message. Returns ``(ok, error_detail)``.

    ``text`` is in the chat dialect the notifier and dispatcher write; it is
    rendered as Telegram HTML here, with every dynamic value escaped.
    """
    if not bot_token:
        return False, "missing bot_token"
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": render(text),
        "parse_mode": PARSE_MODE,
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            return False, f"http {response.status_code}: {response.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, None
