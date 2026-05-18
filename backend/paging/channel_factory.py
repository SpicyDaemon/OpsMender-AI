"""Env-driven channel factory builder (Sprint 35 wiring).

Reads optional notification credentials from the process env and returns a
``ChannelFactory`` that the dispatcher can use to fan pages out. Channels with
unconfigured credentials simply return ``None`` from the factory — the
dispatcher records a ``skipped`` row with reason ``channel_unconfigured``.

The factory is intentionally global (one set of Slack/Teams/Email/SMS
credentials per OpsMender deployment) rather than per-org for v1. Per-tenant
secrets are tracked as a follow-up.

Env vars consumed:

* ``OPSMENDER_SLACK_BOT_TOKEN``
* ``OPSMENDER_TEAMS_WEBHOOK_URL``
* ``OPSMENDER_SMTP_HOST`` (+ ``OPSMENDER_SMTP_PORT``,
  ``OPSMENDER_SMTP_USER``, ``OPSMENDER_SMTP_PASSWORD``,
  ``OPSMENDER_SMTP_FROM``, ``OPSMENDER_SMTP_USE_TLS``)
* ``OPSMENDER_TWILIO_ACCOUNT_SID`` (+ ``OPSMENDER_TWILIO_AUTH_TOKEN``,
  ``OPSMENDER_TWILIO_FROM_NUMBER``)
* ``OPSMENDER_TEAMS_GRAPH_TENANT_ID`` (+ ``OPSMENDER_TEAMS_GRAPH_CLIENT_ID``,
  ``OPSMENDER_TEAMS_GRAPH_CLIENT_SECRET``) — Sprint 37 step 2. When all
  three are set, the ``teams_dm_graph`` channel key resolves to a
  ``TeamsGraphDMChannel`` that posts via Graph ``chats/{id}/messages``.
"""

from __future__ import annotations

import os
from typing import Mapping

from backend.paging.channels import (
    EmailChannel,
    SlackDMChannel,
    SMSChannel,
    TeamsDMChannel,
    TeamsGraphDMChannel,
)
from backend.paging.dispatch import Channel, ChannelFactory


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_channel_factory(
    env: Mapping[str, str] | None = None,
) -> ChannelFactory:
    """Construct a channel factory from the given env mapping (defaults to
    ``os.environ``). Channels without configured credentials yield ``None``.
    """

    src: Mapping[str, str] = env if env is not None else os.environ

    slack_token = src.get("OPSMENDER_SLACK_BOT_TOKEN") or None
    teams_webhook = src.get("OPSMENDER_TEAMS_WEBHOOK_URL") or None
    smtp_host = src.get("OPSMENDER_SMTP_HOST") or None
    smtp_port_raw = src.get("OPSMENDER_SMTP_PORT") or ""
    try:
        smtp_port = int(smtp_port_raw) if smtp_port_raw else 587
    except ValueError:
        smtp_port = 587
    smtp_user = src.get("OPSMENDER_SMTP_USER") or None
    smtp_password = src.get("OPSMENDER_SMTP_PASSWORD") or None
    smtp_from = src.get("OPSMENDER_SMTP_FROM") or "opsmender@localhost"
    smtp_tls = _truthy(src.get("OPSMENDER_SMTP_USE_TLS")) if src.get(
        "OPSMENDER_SMTP_USE_TLS"
    ) is not None else True

    twilio_sid = src.get("OPSMENDER_TWILIO_ACCOUNT_SID") or None
    twilio_token = src.get("OPSMENDER_TWILIO_AUTH_TOKEN") or None
    twilio_from = src.get("OPSMENDER_TWILIO_FROM_NUMBER") or None

    teams_graph_tenant = src.get("OPSMENDER_TEAMS_GRAPH_TENANT_ID") or None
    teams_graph_client = src.get("OPSMENDER_TEAMS_GRAPH_CLIENT_ID") or None
    teams_graph_secret = src.get("OPSMENDER_TEAMS_GRAPH_CLIENT_SECRET") or None

    def factory(key: str) -> Channel | None:
        if key == "slack_dm" and slack_token:
            return SlackDMChannel(bot_token=slack_token)
        if key == "teams_dm" and teams_webhook:
            return TeamsDMChannel(webhook_url=teams_webhook)
        if key == "email" and smtp_host:
            return EmailChannel(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_user=smtp_user,
                smtp_password=smtp_password,
                from_addr=smtp_from,
                use_tls=smtp_tls,
            )
        if key == "sms" and twilio_sid and twilio_token and twilio_from:
            return SMSChannel(
                account_sid=twilio_sid,
                auth_token=twilio_token,
                from_number=twilio_from,
            )
        if (
            key == "teams_dm_graph"
            and teams_graph_tenant
            and teams_graph_client
            and teams_graph_secret
        ):
            return TeamsGraphDMChannel(
                tenant_id=teams_graph_tenant,
                client_id=teams_graph_client,
                client_secret=teams_graph_secret,
            )
        return None

    return factory


def null_channel_factory(key: str) -> Channel | None:
    """A factory that returns ``None`` for every channel. Useful for tests
    that want the dispatcher to run but stop short of real delivery."""

    return None
