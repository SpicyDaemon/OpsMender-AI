"""New Relic alert-workflow webhook adapter."""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "warning": "medium",
    "low": "low",
}


def _first(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


class NewRelicAdapter(IngestAdapter):
    label = "New Relic Alert Workflows"
    provider_key = "newrelic"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        issue_id = payload.get("issueId") or payload.get("issue_id")
        issue_id = issue_id or payload.get("incident_id")
        if not issue_id:
            labels = payload.get("labels") or {}
            issue_id = _first(labels.get("nrIncidentId"))
        if not issue_id:
            raise ValueError("Missing New Relic issueId or incident_id")

        accumulations = payload.get("accumulations") or {}
        entities = payload.get("entitiesData") or {}
        title = (
            payload.get("issueTitle")
            or payload.get("details")
            or _first((payload.get("annotations") or {}).get("title"))
            or "New Relic issue"
        )
        priority = str(
            payload.get("priority") or payload.get("priorityText") or "high"
        ).lower()
        state = str(
            payload.get("state")
            or payload.get("stateText")
            or payload.get("current_state")
            or "activated"
        ).lower()
        status = "resolved" if state in {"closed", "resolved"} else "open"
        conditions = _first(accumulations.get("conditionName"))
        policy = _first(accumulations.get("policyName")) or str(
            payload.get("policy_name") or ""
        )
        entity_names = entities.get("names") or []
        if not isinstance(entity_names, list):
            entity_names = [entity_names]
        lines = [
            f"**State:** {state}",
            f"**Priority:** {priority}",
        ]
        if policy:
            lines.append(f"**Policy:** {policy}")
        if conditions:
            lines.append(f"**Condition:** {conditions}")
        if entity_names:
            lines.append(
                "**Entities:** " + ", ".join(str(item) for item in entity_names)
            )
        issue_url = payload.get("issuePageUrl") or payload.get("incident_url")
        if issue_url:
            lines.append(f"**Issue:** {issue_url}")
        return ParsedIncident(
            title=f"[New Relic] {title}",
            description="\n".join(lines),
            severity=_SEVERITY_MAP.get(priority, "high"),
            external_id=str(issue_id),
            external_source="newrelic",
            status=status,
        )
