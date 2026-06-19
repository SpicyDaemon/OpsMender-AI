"""Splunk Enterprise and Cloud webhook alert adapter."""

from __future__ import annotations

import json
from typing import Any

from backend.ingest.adapters.base import IngestAdapter, ParsedIncident

_SEVERITY_MAP = {
    "critical": "critical",
    "fatal": "critical",
    "high": "high",
    "error": "high",
    "medium": "medium",
    "warning": "medium",
    "low": "low",
    "info": "low",
}


class SplunkAdapter(IngestAdapter):
    label = "Splunk Webhook Alerts"
    provider_key = "splunk"

    def parse(self, payload: dict[str, Any]) -> ParsedIncident:
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            raise ValueError("Splunk webhook 'result' must be an object")
        sid = payload.get("sid")
        search_name = (
            payload.get("search_name")
            or result.get("search_name")
            or result.get("alert_name")
        )
        if not sid and not search_name:
            raise ValueError("Missing Splunk webhook sid or search_name")

        severity_raw = str(
            result.get("severity")
            or result.get("priority")
            or payload.get("severity")
            or "high"
        ).lower()
        state = str(
            result.get("status")
            or result.get("state")
            or payload.get("status")
            or "triggered"
        ).lower()
        status = (
            "resolved" if state in {"resolved", "closed", "ok", "recovered"} else "open"
        )
        external_id = (
            result.get("alert_id") or result.get("correlation_id") or sid or search_name
        )
        result_text = json.dumps(result, sort_keys=True, default=str)
        if len(result_text) > 2000:
            result_text = result_text[:1999] + "…"
        lines = [
            f"**Search:** {search_name or 'scheduled search'}",
            f"**State:** {state}",
            f"**App:** {payload.get('app') or 'unknown'}",
            f"**Owner:** {payload.get('owner') or 'unknown'}",
            f"**First result:** `{result_text}`",
        ]
        if payload.get("results_link"):
            lines.append(f"**Results:** {payload['results_link']}")
        return ParsedIncident(
            title=f"[Splunk] {search_name or 'Alert triggered'}",
            description="\n".join(lines),
            severity=_SEVERITY_MAP.get(severity_raw, "high"),
            external_id=str(external_id),
            external_source="splunk",
            status=status,
        )
