"""Google Cloud Monitoring (formerly Stackdriver) webhook adapter.

Parses GCP Cloud Monitoring alert notifications (v1.2 schema).
Reference: https://cloud.google.com/monitoring/support/notification-options

GCP sends incident-based payloads with this structure::

    {
        "incident": {
            "incident_id": "...",
            "resource_name": "webserver-85",
            "state": "open",            // open | closed | acknowledged
            "policy_name": "CPU Policy",
            "condition_name": "CPU usage",
            "summary": "CPU is above threshold",
            "started_at": 1385085727,
            "ended_at": null,
            "url": "https://console.cloud.google.com/..."
        },
        "version": "1.2"
    }
"""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import AvailabilitySignal, IngestAdapter, ParsedIncident

_SEVERITY_MAP = {
    "critical": "critical",
    "error": "high",
    "warning": "medium",
    "info": "low",
}


class GCPMonitoringAdapter(IngestAdapter):
    label = "Google Cloud Monitoring"
    provider_key = "gcp_monitoring"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        incident = payload.get("incident")
        if not isinstance(incident, dict):
            raise ValueError(
                "Missing 'incident' object in GCP Cloud Monitoring payload"
            )

        incident_id = incident.get("incident_id", "")
        resource_name = incident.get("resource_name", "unknown")
        state = incident.get("state", "open")  # open | closed | acknowledged
        policy_name = incident.get("policy_name", "Unknown Policy")
        condition_name = incident.get("condition_name", "")
        summary = incident.get("summary", "No summary")
        url = incident.get("url", "")
        incident.get("started_at", "")
        incident.get("ended_at")

        # Build display title and description
        title = f"[GCP Monitoring] {policy_name}"
        if condition_name:
            title += f" — {condition_name}"

        desc_text = (
            f"**Policy:** {policy_name}\n"
            f"**Condition:** {condition_name}\n"
            f"**Resource:** {resource_name}\n"
            f"**State:** {state}\n"
            f"**Summary:** {summary}\n"
        )
        if url:
            desc_text += f"**Console URL:** {url}\n"

        # GCP doesn't include severity in the webhook, so we default to high
        # for open incidents, low for closed.
        if state == "closed":
            severity = "low"
            status = "resolved"
        elif state == "acknowledged":
            severity = "medium"
            status = "investigating"
        else:
            severity = "high"
            status = "open"

        # Look for optional severity from condition metadata
        condition_severity = incident.get("severity", "")
        if condition_severity:
            mapped = _SEVERITY_MAP.get(condition_severity.lower())
            if mapped:
                severity = mapped

        # Fingerprint: incident_id is globally unique
        external_id = incident_id or f"{policy_name}:{resource_name}"

        # Availability signal — closed=up, open/acknowledged=down
        availability = AvailabilitySignal(
            target_name=policy_name,
            up=(state == "closed"),
            source="gcp_monitoring",
        )

        return ParsedIncident(
            title=title,
            description=desc_text,
            severity=severity,
            external_id=external_id,
            external_source="gcp_monitoring",
            status=status,
            availability=availability,
        )
