"""Splunk AppDynamics HTTP request action adapter."""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY = {
    "error": "high",
    "warn": "medium",
    "warning": "medium",
    "info": "low",
}


class AppDynamicsAdapter(IngestAdapter):
    label = "AppDynamics HTTP Request Actions"
    provider_key = "appdynamics"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        event_id = (
            payload.get("incident_id")
            or payload.get("event_guid")
            or payload.get("event_id")
        )
        if not event_id:
            raise ValueError("Missing AppDynamics event_guid, event_id, or incident_id")
        event_name = (
            payload.get("event_name")
            or payload.get("incident_name")
            or "AppDynamics event"
        )
        event_type = str(payload.get("event_type") or "").upper()
        state = str(
            payload.get("state")
            or payload.get("event_state")
            or payload.get("event_type")
            or "OPEN"
        ).upper()
        resolved = state in {
            "RESOLVED",
            "CLOSED",
            "HEALTH_RULE_VIOLATION_ENDED",
        } or event_type.endswith("_ENDED")
        severity_raw = str(payload.get("severity") or "ERROR").lower()
        lines = [
            f"**Application:** {payload.get('app_name') or 'unknown'}",
            f"**Policy:** {payload.get('policy') or 'unknown'}",
            f"**Event type:** {event_type or 'unknown'}",
            f"**Summary:** {payload.get('summary') or payload.get('event_message') or 'n/a'}",
        ]
        if payload.get("event_deep_link"):
            lines.append(f"**Event:** {payload['event_deep_link']}")
        return ParsedIncident(
            title=f"[AppDynamics] {event_name}",
            description="\n".join(lines),
            severity=_SEVERITY.get(severity_raw, "high"),
            external_id=str(event_id),
            external_source="appdynamics",
            status="resolved" if resolved else "open",
        )
