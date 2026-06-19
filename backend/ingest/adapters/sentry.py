"""Sentry issue and metric-alert webhook adapter."""

from __future__ import annotations

from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY_MAP = {
    "fatal": "critical",
    "error": "high",
    "warning": "medium",
    "info": "low",
    "debug": "low",
}
_RESOLVED = {"resolved", "ignored", "archived", "closed"}


def _project_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("slug") or value.get("name") or value.get("id") or "")
    return str(value or "")


class SentryAdapter(IngestAdapter):
    label = "Sentry Issues and Metric Alerts"
    provider_key = "sentry"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        data = payload.get("data") or {}
        action = str(payload.get("action") or data.get("action") or "").lower()
        issue = data.get("issue") or payload.get("issue")
        if isinstance(issue, dict):
            issue_id = issue.get("id") or issue.get("shortId")
            title = str(issue.get("title") or issue.get("message") or "Sentry issue")
            level = str(issue.get("level") or "error").lower()
            issue_status = str(issue.get("status") or "").lower()
            status = (
                "resolved"
                if issue_status in _RESOLVED or action in _RESOLVED
                else "open"
            )
            project = _project_name(issue.get("project"))
            lines = [
                f"**Action:** {action or 'issue update'}",
                f"**Project:** {project or 'unknown'}",
                f"**Level:** {level}",
            ]
            if issue.get("culprit"):
                lines.append(f"**Culprit:** {issue['culprit']}")
            if issue.get("permalink"):
                lines.append(f"**Issue:** {issue['permalink']}")
            return ParsedIncident(
                title=f"[Sentry] {title}",
                description="\n".join(lines),
                severity=_SEVERITY_MAP.get(level, "high"),
                external_id=str(issue_id) if issue_id else None,
                external_source="sentry",
                status=status,
            )

        metric = data.get("metric_alert") or data.get("metricAlert")
        if isinstance(metric, dict):
            alert_rule = metric.get("alert_rule") or metric.get("alertRule") or {}
            if not isinstance(alert_rule, dict):
                alert_rule = {}
            alert_id = metric.get("id") or alert_rule.get("id")
            title = (
                metric.get("title") or alert_rule.get("name") or "Sentry metric alert"
            )
            state = str(
                metric.get("status") or metric.get("state") or action or "triggered"
            ).lower()
            status = "resolved" if state in _RESOLVED else "open"
            description = str(
                data.get("description")
                or metric.get("description")
                or f"Metric alert state: {state}"
            )
            return ParsedIncident(
                title=f"[Sentry] {title}",
                description=description,
                severity="high",
                external_id=str(alert_id) if alert_id else None,
                external_source="sentry",
                status=status,
            )

        raise ValueError(
            "Missing Sentry 'data.issue' or 'data.metric_alert' webhook payload"
        )
