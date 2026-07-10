"""Per-incident Slack channel mirror (Sprint 36 step 5).

When an incident enters ``page`` mode and the owning org has
``slack_incident_channels_enabled`` set, OpsMender creates a workspace
channel named ``inc-<short>`` via ``conversations.create`` and posts the
Block Kit page card to it. The channel id is stored on the incident so
later updates can mirror to the same place.

The Slack bot token is read from env (``OPSMENDER_SLACK_BOT_TOKEN``) — the
same global credential the dispatcher already uses for ``slack_dm``.
Tests inject ``httpx.MockTransport`` via ``http_client_factory``.

Failure is non-fatal: the chain still runs whether or not the mirror
succeeded; we just log a warning. The mirror is intentionally idempotent
— calling it twice for the same incident is a no-op once
``incident.slack_channel_id`` is populated.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Incident, Organization
from backend.paging.slack_cards import (
    build_page_card_blocks,
    build_page_card_text,
)


logger = logging.getLogger(__name__)

HttpClientFactory = Callable[[], httpx.AsyncClient]

SLACK_API_BASE = "https://slack.com/api"

_NAME_INVALID = re.compile(r"[^a-z0-9_-]+")


def _default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


def channel_name_for_incident(incident: Incident, *, prefix: str = "inc-") -> str:
    """Build a Slack-safe channel name from the incident.

    Slack channel names are lowercase, ≤80 chars, and limited to letters,
    digits, hyphens, underscores, and periods. We use ``<prefix><first8>``
    where ``<first8>`` is the leading hex of the incident UUID.
    """

    short = uuid.UUID(str(incident.id)).hex[:8]
    raw = f"{prefix}{short}".lower()
    safe = _NAME_INVALID.sub("-", raw)
    return safe[:80]


async def mirror_incident_to_slack_channel(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    bot_token: str | None = None,
    http_client_factory: HttpClientFactory | None = None,
    base_url: str | None = None,
) -> str | None:
    """Create (or reuse) a Slack channel for ``incident`` and post the
    page card to it. Returns the channel id on success, ``None`` if the
    feature is disabled, the bot token is missing, or the Slack API call
    failed.
    """

    org = await db.get(Organization, org_id)
    if org is None or not getattr(org, "slack_incident_channels_enabled", False):
        return None

    if incident.slack_channel_id:
        return incident.slack_channel_id

    token = bot_token or os.environ.get("OPSMENDER_SLACK_BOT_TOKEN")
    if not token:
        logger.warning("slack channel mirror skipped: OPSMENDER_SLACK_BOT_TOKEN unset")
        return None

    factory = http_client_factory or _default_http_client
    name = channel_name_for_incident(incident)

    try:
        async with factory() as client:
            create_resp = await client.post(
                f"{SLACK_API_BASE}/conversations.create",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={"name": name, "is_private": False},
            )
    except httpx.HTTPError as exc:
        logger.warning("slack channel mirror network error: %s", exc)
        return None

    if create_resp.status_code != 200:
        logger.warning("slack channel mirror http %s", create_resp.status_code)
        return None
    try:
        create_data = create_resp.json()
    except ValueError:
        logger.warning("slack channel mirror invalid json")
        return None

    channel_id: str | None = None
    if create_data.get("ok"):
        channel_id = (create_data.get("channel") or {}).get("id")
    else:
        # ``name_taken`` is the common case: a channel with this exact name
        # already exists (e.g. operator retry). Slack returns no channel
        # object; we leave the incident unmirrored and let the operator
        # surface fix it manually. Same for ``missing_scope`` etc.
        logger.warning(
            "slack channel mirror create failed: %s",
            create_data.get("error") or "unknown",
        )
        return None

    if not channel_id:
        return None

    incident.slack_channel_id = channel_id
    incident.slack_channel_name = name
    await db.flush()

    # Post the page card to the new channel. Failure here is logged but
    # does not roll back the channel id — the channel exists either way.
    try:
        async with factory() as client:
            await client.post(
                f"{SLACK_API_BASE}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "channel": channel_id,
                    "text": build_page_card_text(incident),
                    "blocks": build_page_card_blocks(incident, base_url=base_url),
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("slack channel mirror initial post failed: %s", exc)

    return channel_id
