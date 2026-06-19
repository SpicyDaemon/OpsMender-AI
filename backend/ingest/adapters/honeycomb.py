"""Honeycomb Trigger and SLO webhook adapter."""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY = {
    "critical": "critical",
    "high": "high",
    "warning": "medium",
    "medium": "medium",
    "low": "low",
    "info": "low",
}


class HoneycombAdapter(IngestAdapter):
    label = "Honeycomb Triggers and SLO Alerts"
    provider_key = "honeycomb"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        alert = payload.get("Alert") or payload.get("alert") or {}
        alert_id = (
            payload.get("ID")
            or payload.get("id")
            or alert.get("InstanceID")
            or alert.get("instance_id")
        )
        if not alert_id:
            raise ValueError("Missing Honeycomb alert instance or alert id")
        name = payload.get("Name") or payload.get("name") or "Honeycomb alert"
        summary = (
            alert.get("Summary")
            or alert.get("summary")
            or alert.get("Description")
            or payload.get("Description")
            or name
        )
        state = str(
            alert.get("Status")
            or alert.get("status")
            or payload.get("status")
            or "TRIGGERED"
        ).upper()
        severity_raw = str(
            payload.get("Severity")
            or payload.get("severity")
            or alert.get("Severity")
            or "high"
        ).lower()
        lines = [
            f"**State:** {state}",
            f"**Environment:** {payload.get('Environment') or payload.get('environment') or 'unknown'}",
            f"**Summary:** {summary}",
        ]
        url = (
            alert.get("InvestigateURL")
            or alert.get("investigate_url")
            or payload.get("URL")
            or payload.get("url")
        )
        if url:
            lines.append(f"**Investigate:** {url}")
        return ParsedIncident(
            title=f"[Honeycomb] {name}",
            description="\n".join(lines),
            severity=_SEVERITY.get(severity_raw, "high"),
            external_id=str(alert_id),
            external_source="honeycomb",
            status="resolved" if state == "OK" else "open",
        )
