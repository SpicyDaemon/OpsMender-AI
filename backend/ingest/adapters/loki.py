"""Grafana Loki alert webhook adapter."""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY = {
    "critical": "critical",
    "high": "high",
    "error": "high",
    "warning": "medium",
    "medium": "medium",
    "low": "low",
    "info": "low",
}


class LokiAdapter(IngestAdapter):
    label = "Grafana Loki Alerts"
    provider_key = "loki"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        alerts = payload.get("alerts") or []
        alert = alerts[0] if isinstance(alerts, list) and alerts else {}
        if not isinstance(alert, dict):
            raise ValueError("Loki webhook alerts must be objects")
        labels = alert.get("labels") or payload.get("commonLabels") or {}
        annotations = alert.get("annotations") or payload.get("commonAnnotations") or {}
        fingerprint = (
            alert.get("fingerprint")
            or payload.get("groupKey")
            or labels.get("alertname")
        )
        if not fingerprint:
            raise ValueError("Missing Loki alert fingerprint or alertname")
        state = str(
            alert.get("status")
            or payload.get("status")
            or payload.get("state")
            or "firing"
        ).lower()
        title = (
            annotations.get("summary")
            or labels.get("alertname")
            or payload.get("title")
            or "Loki alert"
        )
        description = (
            annotations.get("description")
            or payload.get("message")
            or f"Loki alert state: {state}"
        )
        severity_raw = str(
            labels.get("severity") or annotations.get("severity") or "high"
        ).lower()
        return ParsedIncident(
            title=f"[Loki] {title}",
            description=description,
            severity=_SEVERITY.get(severity_raw, "high"),
            external_id=str(fingerprint),
            external_source="loki",
            status="resolved" if state in {"resolved", "ok"} else "open",
        )
