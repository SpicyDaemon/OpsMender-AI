"""LegacyAlertVendor v2 webhook adapter.

Handles the LegacyAlertVendor v2 webhook format with ``event.event_type`` being
one of ``incident.triggered``, ``incident.acknowledged``,
``incident.resolved``, etc.

Reference: https://developer.legacy_alert_vendor.com/docs/db0fa830d7f2e-v2-webhook-payloads
"""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY_MAP = {
    "P1": "critical",
    "P2": "high",
    "P3": "medium",
    "P4": "low",
    "P5": "low",
}

_EVENT_STATUS_MAP = {
    "incident.triggered": "open",
    "incident.acknowledged": "investigating",
    "incident.resolved": "resolved",
}


class LegacyAlertVendorAdapter(IngestAdapter):
    label = "LegacyAlertVendor Webhooks"
    provider_key = "legacy_alert_vendor"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        # V2 webhook format: { "event": { "event_type": "...", "data": {...} } }
        event = payload.get("event")
        if not event:
            # V3 format or messages array
            messages = payload.get("messages", [])
            if messages:
                event = messages[0].get("event") or messages[0]
            else:
                raise ValueError(
                    "Missing 'event' or 'messages' in LegacyAlertVendor payload"
                )

        event_type = event.get("event_type", "")
        data = event.get("data", event)

        incident_id = data.get("id", "")
        title_raw = data.get("title", data.get("summary", "LegacyAlertVendor Incident"))
        urgency = data.get("urgency", "high")
        priority = data.get("priority", {})
        priority_summary = priority.get("summary", "") if isinstance(priority, dict) else ""
        html_url = data.get("html_url", "")
        service = data.get("service", {})
        service_name = service.get("summary", "Unknown Service") if isinstance(service, dict) else "Unknown Service"

        title = f"[LegacyAlertVendor] {title_raw}"

        # Map priority (P1–P5) or urgency to severity
        severity = None
        if priority_summary:
            severity = _SEVERITY_MAP.get(priority_summary.upper(), None)
        if severity is None:
            severity = "high" if urgency == "high" else "medium"

        status = _EVENT_STATUS_MAP.get(event_type, "open")

        description = (
            f"**Incident:** {title_raw}\n"
            f"**Service:** {service_name}\n"
            f"**Urgency:** {urgency}\n"
            f"**Priority:** {priority_summary or 'N/A'}\n"
            f"**Event:** {event_type}\n"
            f"**URL:** {html_url or 'N/A'}"
        )

        return ParsedIncident(
            title=title,
            description=description,
            severity=severity,
            external_id=incident_id or title_raw,
            external_source="legacy_alert_vendor",
            status=status,
        )
