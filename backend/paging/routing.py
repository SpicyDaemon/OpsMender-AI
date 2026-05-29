"""Priority routing stages — shared parsing for personal notification routing.

A priority's routing is an **ordered list of escalation stages**. Each stage
targets a single notification channel and carries a delay (in seconds) after
which the *next* stage fires if the incident is still unacknowledged.

Canonical (new) shape::

    {"P0": [{"channel_id": "telegram-ops", "delay_seconds": 300},
            {"channel_id": "sms-primary", "delay_seconds": 300}]}

Legacy (pre-staging) shape — a flat list of channel keys::

    {"P0": ["slack_dm", "email"]}

``parse_stages`` accepts either and always returns a normalized list of
``Stage`` objects. Legacy entries become stages with ``delay_seconds == 0``
(the first stage fires immediately; the rest follow with no extra wait),
so existing preferences keep working with no data migration — "existing
single-channel routing becomes Stage 1."

``channel_id`` is either a configured Notification Channel id (a
``BotConnector`` UUID, as a string) or a legacy delivery key
(``slack_dm`` / ``teams_dm`` / ``teams_dm_graph`` / ``email`` / ``sms``).
The dispatcher resolves both.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# Delivery keys the legacy paging channel factory understands directly.
LEGACY_CHANNEL_KEYS: frozenset[str] = frozenset(
    {"slack_dm", "teams_dm", "teams_dm_graph", "email", "sms"}
)

MAX_STAGES = 3
DEFAULT_DELAY_SECONDS = 300


@dataclasses.dataclass(slots=True)
class Stage:
    channel_id: str
    delay_seconds: int = DEFAULT_DELAY_SECONDS


def is_legacy_entry(value: Any) -> bool:
    """True when a routing list element is a bare channel-key string."""
    return isinstance(value, str)


def parse_stages(
    raw: Any,
    *,
    max_stages: int = MAX_STAGES,
    default_delay: int = DEFAULT_DELAY_SECONDS,
) -> list[Stage]:
    """Normalize a single priority's routing value into ordered stages.

    Accepts the legacy ``list[str]`` shape and the new ``list[dict]`` shape.
    Unknown/blank channels are skipped. The result is capped at
    ``max_stages``. Legacy entries get ``delay_seconds == 0`` so the prior
    "fan out to these channels" behavior is preserved (no escalation wait).
    """

    if not isinstance(raw, list):
        return []
    stages: list[Stage] = []
    for idx, entry in enumerate(raw):
        if isinstance(entry, str):
            channel_id = entry.strip()
            if not channel_id:
                continue
            # Legacy fan-out — no inter-stage delay.
            stages.append(Stage(channel_id=channel_id, delay_seconds=0))
        elif isinstance(entry, dict):
            channel_id = str(entry.get("channel_id") or "").strip()
            if not channel_id:
                continue
            delay_raw = entry.get("delay_seconds", default_delay)
            try:
                delay = int(delay_raw)
            except (TypeError, ValueError):
                delay = default_delay
            stages.append(Stage(channel_id=channel_id, delay_seconds=max(0, delay)))
        if len(stages) >= max_stages:
            break
    return stages


def routing_is_staged(raw: Any) -> bool:
    """True when a priority's routing uses the new stage (dict) shape.

    A staged value delegates delivery to the notification-escalation engine;
    a legacy value (list of strings) keeps the existing immediate fan-out.
    """
    return isinstance(raw, list) and any(isinstance(e, dict) for e in raw)


def stage_channel_ids(raw: Any) -> list[str]:
    """Ordered channel ids for a priority (new or legacy shape)."""
    return [s.channel_id for s in parse_stages(raw)]
