"""Dynatrace problem-notification webhook adapter."""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY = {
    "availability": "critical",
    "error": "high",
    "performance": "high",
    "resource_contention": "medium",
    "custom_alert": "medium",
}


class DynatraceAdapter(IngestAdapter):
    label = "Dynatrace Problem Notifications"
    provider_key = "dynatrace"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        details = payload.get("ProblemDetailsJSON") or payload.get(
            "ProblemDetailsJSONv2"
        )
        if not isinstance(details, dict):
            details = {}
        problem_id = (
            payload.get("ProblemID")
            or payload.get("PID")
            or details.get("id")
            or details.get("problemId")
        )
        if not problem_id:
            raise ValueError("Missing Dynatrace ProblemID or PID")
        title = (
            payload.get("ProblemTitle") or details.get("title") or "Dynatrace problem"
        )
        state = str(payload.get("State") or details.get("status") or "OPEN").upper()
        severity_raw = str(
            payload.get("ProblemSeverity")
            or details.get("severityLevel")
            or payload.get("ProblemImpact")
            or "ERROR"
        ).lower()
        impacted = payload.get("ImpactedEntity") or details.get("affectedEntities")
        return ParsedIncident(
            title=f"[Dynatrace] {title}",
            description=(
                f"**State:** {state}\n"
                f"**Impact:** {payload.get('ProblemImpact') or 'unknown'}\n"
                f"**Impacted:** {impacted or 'unknown'}\n"
                f"**Problem:** {payload.get('ProblemURL') or payload.get('Problem URL') or 'n/a'}"
            ),
            severity=_SEVERITY.get(severity_raw, "high"),
            external_id=str(problem_id),
            external_source="dynatrace",
            status="resolved" if state == "RESOLVED" else "open",
        )
