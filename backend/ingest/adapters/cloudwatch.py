"""CloudWatch Alarms via SNS adapter.

Handles two SNS message types:
- ``SubscriptionConfirmation`` — returns the ``SubscribeURL`` so the
  caller can auto-confirm.
- ``Notification`` — parses the embedded CloudWatch alarm JSON.

Signature verification is left to the caller / infrastructure layer
(e.g. an ALB or API Gateway validates SNS signatures before OpsMender).
"""

from __future__ import annotations

import json
from typing import Any

from backend.ingest.adapters.base import (
    AvailabilitySignal,
    IngestAdapter,
    ParsedIncident,
)

_SEVERITY_MAP = {
    "ALARM": "high",
    "OK": "low",
    "INSUFFICIENT_DATA": "medium",
}


class CloudWatchAdapter(IngestAdapter):
    label = "CloudWatch Alarms via SNS"
    provider_key = "cloudwatch"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        msg_type = payload.get("Type")

        if msg_type == "SubscriptionConfirmation":
            subscribe_url = payload.get("SubscribeURL", "")
            raise ValueError(f"SNS_SUBSCRIPTION_CONFIRMATION:{subscribe_url}")

        if msg_type != "Notification":
            raise ValueError(f"Unsupported SNS message type: {msg_type}")

        # The CloudWatch alarm detail is JSON-encoded inside "Message"
        message_raw = payload.get("Message", "{}")
        try:
            message = (
                json.loads(message_raw) if isinstance(message_raw, str) else message_raw
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"Invalid CloudWatch alarm JSON in Message: {exc}"
            ) from exc

        alarm_name = message.get("AlarmName", "Unknown Alarm")
        new_state = message.get("NewStateValue", "ALARM")
        reason = message.get("NewStateReason", "No reason provided")
        region = message.get("Region", "unknown")
        account = message.get("AWSAccountId", "unknown")

        title = f"[CloudWatch] {alarm_name} — {new_state}"
        description = (
            f"**Alarm:** {alarm_name}\n"
            f"**State:** {new_state}\n"
            f"**Region:** {region}\n"
            f"**Account:** {account}\n"
            f"**Reason:** {reason}"
        )

        severity = _SEVERITY_MAP.get(new_state, "medium")

        # If alarm is OK, map to resolved status
        status = "resolved" if new_state == "OK" else "open"

        # Fingerprint: alarm name + account + region
        external_id = f"{account}:{region}:{alarm_name}"

        # Emit an availability signal — CloudWatch alarms map naturally
        # to up/down: OK = up, ALARM/INSUFFICIENT_DATA = down.
        availability = AvailabilitySignal(
            target_name=alarm_name,
            up=(new_state == "OK"),
            source="cloudwatch",
        )

        return ParsedIncident(
            title=title,
            description=description,
            severity=severity,
            external_id=external_id,
            external_source="cloudwatch",
            status=status,
            availability=availability,
        )
