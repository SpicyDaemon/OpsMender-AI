"""LegacyAlertRelay webhook adapter.

Parses LegacyAlertRelay webhook payloads emitted by the Webhook integration.
Supported actions include ``Create``, ``Acknowledge``, ``Close``, and
update-style actions that carry an ``alert`` block.

Reference:
https://support.atlassian.com/legacy_alert_relay/docs/legacy_alert_relay-edge-connector-alert-action-data/
"""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_PRIORITY_MAP = {
    "P1": "critical",
    "P2": "high",
    "P3": "medium",
    "P4": "low",
    "P5": "low",
}

_ACTION_STATUS_MAP = {
    "create": "open",
    "unacknowledge": "open",
    "close": "resolved",
    "delete": "resolved",
    "acknowledge": "investigating",
    "assignownership": "investigating",
    "takeownership": "investigating",
}


class LegacyAlertRelayAdapter(IngestAdapter):
    label = "LegacyAlertRelay Webhooks"
    provider_key = "legacy_alert_relay"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        action_raw = str(payload.get("action") or "Create")
        action = action_raw.strip() or "Create"

        alert = payload.get("alert")
        if not isinstance(alert, dict) or not alert:
            raise ValueError("Missing 'alert' object in LegacyAlertRelay payload")

        alert_id = alert.get("alertId")
        alias = alert.get("alias")
        tiny_id = alert.get("tinyId")
        message = alert.get("message") or "LegacyAlertRelay Alert"
        description_raw = alert.get("description")
        priority_raw = str(alert.get("priority") or "").upper()
        source_name = alert.get("source") or payload.get("source", {}).get("name")
        entity = alert.get("entity")
        username = alert.get("username")
        tags = alert.get("tags") or []
        details = alert.get("details") or {}

        severity = _PRIORITY_MAP.get(priority_raw, "medium")
        status = _ACTION_STATUS_MAP.get(action.lower(), "open")

        description_lines = [
            f"**Alert:** {message}",
            f"**Action:** {action}",
            f"**Priority:** {priority_raw or 'N/A'}",
            f"**Alias:** {alias or 'N/A'}",
            f"**Tiny ID:** {tiny_id or 'N/A'}",
            f"**Entity:** {entity or 'N/A'}",
            f"**Source:** {source_name or 'N/A'}",
            f"**User:** {username or 'N/A'}",
        ]
        if description_raw:
            description_lines.append(f"**Description:** {description_raw}")
        if tags:
            description_lines.append(f"**Tags:** {', '.join(str(tag) for tag in tags)}")
        if isinstance(details, dict) and details:
            detail_pairs = ", ".join(
                f"{key}={value}" for key, value in details.items()
            )
            description_lines.append(f"**Details:** {detail_pairs}")

        return ParsedIncident(
            title=f"[LegacyAlertRelay] {message}",
            description="\n".join(description_lines),
            severity=severity,
            external_id=str(alert_id or alias or tiny_id or message),
            external_source="legacy_alert_relay",
            status=status,
        )
